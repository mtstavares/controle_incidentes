"""Deduplica incidentes por assinatura operacional.

Uso seguro:
    python scripts/deduplicate_incidents.py --db instance/divciber.db --dry-run
    python scripts/deduplicate_incidents.py --db instance/divciber.db --apply

A rotina mantém sempre o menor ID de cada grupo, move anexos e observações
únicas para ele, e aplica soft delete nos demais registros.
"""

from __future__ import annotations

import argparse
import json
import re
import shutil
import sqlite3
import unicodedata
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path


FINAL_STATUSES = {"encerrado", "falso positivo"}
MERGE_IF_EMPTY_FIELDS = (
    "ticket_number",
    "message_number",
    "cia",
    "end_date",
    "description_plain_text",
    "command_id",
    "unit_id",
)


def norm_text(value) -> str:
    if value is None:
        return ""
    text = unicodedata.normalize("NFKC", str(value)).casefold()
    text = text.replace("\u00ba", "o").replace("\u00b0", "o")
    return re.sub(r"\s+", " ", text).strip()


def compact(value) -> str:
    return re.sub(r"\W+", "", norm_text(value), flags=re.UNICODE)


def norm_date(value) -> str:
    return str(value).split()[0] if value else ""


def norm_ticket(value) -> str:
    if not value:
        return ""
    text = re.sub(r"\brds\s*", "rds", norm_text(value))
    return compact(text)


def operational_key(row: sqlite3.Row) -> tuple[str, ...]:
    return (
        compact(row["incident_type"]),
        norm_date(row["start_date"]),
        compact(row["report_number"]),
        norm_ticket(row["message_number"]),
        norm_ticket(row["ticket_number"]),
        compact(row["cpa"]),
        compact(row["btl"]),
    )


def normalized_obs_key(row: sqlite3.Row) -> tuple[str, str, int]:
    return (
        compact(row["texto_observacao"]),
        str(row["data_observacao"] or ""),
        int(row["usuario_id"]),
    )


def is_final_status(value) -> bool:
    return norm_text(value) in FINAL_STATUSES


def load_duplicate_groups(con: sqlite3.Connection) -> list[list[sqlite3.Row]]:
    rows = con.execute("SELECT * FROM incidente WHERE deleted_at IS NULL ORDER BY id").fetchall()
    groups: dict[tuple[str, ...], list[sqlite3.Row]] = defaultdict(list)
    for row in rows:
        key = operational_key(row)
        if any(key):
            groups[key].append(row)
    return [group for group in groups.values() if len(group) > 1]


def backup_database(db_path: Path) -> Path:
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    backup_path = db_path.with_name(f"{db_path.stem}-pre-deduplicate-incidents-{stamp}{db_path.suffix}.backup")
    shutil.copy2(db_path, backup_path)
    return backup_path


def row_by_id(con: sqlite3.Connection, incident_id: int) -> sqlite3.Row:
    row = con.execute("SELECT * FROM incidente WHERE id = ?", (incident_id,)).fetchone()
    if row is None:
        raise RuntimeError(f"Incidente nao encontrado: {incident_id}")
    return row


def merge_core_fields(con: sqlite3.Connection, keep_id: int, duplicate_ids: list[int], now: str) -> list[str]:
    keeper = row_by_id(con, keep_id)
    updates = {}
    changed = []
    duplicates = [row_by_id(con, incident_id) for incident_id in duplicate_ids]

    for field in MERGE_IF_EMPTY_FIELDS:
        if keeper[field] not in (None, ""):
            continue
        incoming = next((row[field] for row in duplicates if row[field] not in (None, "")), None)
        if incoming not in (None, ""):
            updates[field] = incoming
            changed.append(field)

    if not is_final_status(keeper["status_incident"]):
        final_duplicate = next((row for row in duplicates if is_final_status(row["status_incident"])), None)
        if final_duplicate is not None:
            updates["status_incident"] = final_duplicate["status_incident"]
            changed.append("status_incident")
            if final_duplicate["end_date"] and not keeper["end_date"]:
                updates["end_date"] = final_duplicate["end_date"]
                changed.append("end_date")

    if updates:
        updates["updated_at"] = now
        assignments = ", ".join(f"{field} = :{field}" for field in updates)
        updates["id"] = keep_id
        con.execute(f"UPDATE incidente SET {assignments} WHERE id = :id", updates)
    return changed


def move_observations(con: sqlite3.Connection, keep_id: int, duplicate_ids: list[int], now: str) -> dict:
    existing = {
        normalized_obs_key(row)
        for row in con.execute(
            "SELECT * FROM incidente_obs WHERE incidente_id = ? AND deleted_at IS NULL",
            (keep_id,),
        )
    }
    moved = 0
    soft_deleted_duplicates = 0
    for duplicate_id in duplicate_ids:
        rows = con.execute(
            "SELECT * FROM incidente_obs WHERE incidente_id = ? AND deleted_at IS NULL ORDER BY id",
            (duplicate_id,),
        ).fetchall()
        for row in rows:
            key = normalized_obs_key(row)
            if key in existing:
                con.execute(
                    "UPDATE incidente_obs SET deleted_at = ?, updated_at = ? WHERE id = ?",
                    (now, now, row["id"]),
                )
                soft_deleted_duplicates += 1
                continue
            con.execute(
                "UPDATE incidente_obs SET incidente_id = ?, updated_at = ? WHERE id = ?",
                (keep_id, now, row["id"]),
            )
            existing.add(key)
            moved += 1
    return {"moved": moved, "soft_deleted_duplicates": soft_deleted_duplicates}


def move_attachments(con: sqlite3.Connection, keep_id: int, duplicate_ids: list[int], now: str) -> dict:
    existing_hashes = {
        row["sha256"]
        for row in con.execute(
            "SELECT sha256 FROM incident_attachments WHERE incident_id = ? AND deleted_at IS NULL",
            (keep_id,),
        )
        if row["sha256"]
    }
    moved = 0
    soft_deleted_duplicates = 0
    for duplicate_id in duplicate_ids:
        rows = con.execute(
            "SELECT * FROM incident_attachments WHERE incident_id = ? AND deleted_at IS NULL ORDER BY id",
            (duplicate_id,),
        ).fetchall()
        for row in rows:
            if row["sha256"] and row["sha256"] in existing_hashes:
                con.execute(
                    "UPDATE incident_attachments SET deleted_at = ?, updated_at = ? WHERE id = ?",
                    (now, now, row["id"]),
                )
                soft_deleted_duplicates += 1
                continue
            con.execute(
                "UPDATE incident_attachments SET incident_id = ?, updated_at = ? WHERE id = ?",
                (keep_id, now, row["id"]),
            )
            if row["sha256"]:
                existing_hashes.add(row["sha256"])
            moved += 1
    return {"moved": moved, "soft_deleted_duplicates": soft_deleted_duplicates}


def deduplicate(con: sqlite3.Connection, *, apply: bool) -> dict:
    now = datetime.now(timezone.utc).isoformat()
    groups = load_duplicate_groups(con)
    report = {
        "mode": "apply" if apply else "dry-run",
        "generated_at": now,
        "groups": [],
        "total_groups": len(groups),
        "total_duplicate_incidents": 0,
        "observations_moved": 0,
        "observations_soft_deleted": 0,
        "attachments_moved": 0,
        "attachments_soft_deleted": 0,
        "incidents_soft_deleted": 0,
    }

    for group in groups:
        ids = [int(row["id"]) for row in group]
        keep_id = min(ids)
        duplicate_ids = [incident_id for incident_id in ids if incident_id != keep_id]
        group_report = {
            "keep_id": keep_id,
            "duplicate_ids": duplicate_ids,
            "all_ids": ids,
            "core_fields_updated": [],
            "observations": {"moved": 0, "soft_deleted_duplicates": 0},
            "attachments": {"moved": 0, "soft_deleted_duplicates": 0},
        }
        report["total_duplicate_incidents"] += len(duplicate_ids)

        if apply:
            group_report["core_fields_updated"] = merge_core_fields(con, keep_id, duplicate_ids, now)
            obs = move_observations(con, keep_id, duplicate_ids, now)
            att = move_attachments(con, keep_id, duplicate_ids, now)
            con.execute(
                f"UPDATE incidente SET deleted_at = ?, updated_at = ? WHERE id IN ({','.join('?' for _ in duplicate_ids)})",
                [now, now, *duplicate_ids],
            )
            group_report["observations"] = obs
            group_report["attachments"] = att
            report["observations_moved"] += obs["moved"]
            report["observations_soft_deleted"] += obs["soft_deleted_duplicates"]
            report["attachments_moved"] += att["moved"]
            report["attachments_soft_deleted"] += att["soft_deleted_duplicates"]
            report["incidents_soft_deleted"] += len(duplicate_ids)

        report["groups"].append(group_report)

    return report


def main() -> int:
    parser = argparse.ArgumentParser(description="Deduplica incidentes mantendo sempre o menor ID.")
    parser.add_argument("--db", type=Path, default=Path("instance/divciber.db"), help="Caminho do banco SQLite.")
    parser.add_argument("--apply", action="store_true", help="Aplica as alteracoes.")
    parser.add_argument("--dry-run", action="store_true", help="Somente simula a deduplicacao.")
    parser.add_argument("--report-dir", type=Path, default=Path("instance/duplicate_scans"), help="Diretorio dos relatorios.")
    args = parser.parse_args()

    if args.apply == args.dry_run:
        parser.error("Use exatamente um modo: --dry-run ou --apply.")
    db_path = args.db.resolve()
    if not db_path.is_file():
        parser.error(f"Banco nao encontrado: {db_path}")

    args.report_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    report_path = args.report_dir / f"incident_deduplication_{stamp}.json"

    backup_path = None
    if args.apply:
        backup_path = backup_database(db_path)

    con = sqlite3.connect(db_path)
    con.row_factory = sqlite3.Row
    try:
        con.execute("PRAGMA foreign_keys = ON")
        before_count = con.execute("SELECT COUNT(*) FROM incidente WHERE deleted_at IS NULL").fetchone()[0]
        with con:
            report = deduplicate(con, apply=args.apply)
        after_count = con.execute("SELECT COUNT(*) FROM incidente WHERE deleted_at IS NULL").fetchone()[0]
        report["database"] = str(db_path)
        report["backup"] = str(backup_path) if backup_path else None
        report["active_incidents_before"] = before_count
        report["active_incidents_after"] = after_count
        report["integrity_check"] = con.execute("PRAGMA integrity_check").fetchone()[0]
        report["foreign_key_check"] = [list(row) for row in con.execute("PRAGMA foreign_key_check").fetchall()]
        report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        print(json.dumps({
            "mode": report["mode"],
            "groups": report["total_groups"],
            "incidents_soft_deleted": report["incidents_soft_deleted"],
            "active_before": before_count,
            "active_after": after_count,
            "observations_moved": report["observations_moved"],
            "attachments_moved": report["attachments_moved"],
            "backup": report["backup"],
            "report": str(report_path),
            "integrity_check": report["integrity_check"],
            "foreign_key_check": report["foreign_key_check"],
        }, ensure_ascii=False, indent=2))
    finally:
        con.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
