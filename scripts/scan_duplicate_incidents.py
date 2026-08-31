"""Verifica duplicidades operacionais em incidentes sem alterar o banco.

Uso:
    python scripts/scan_duplicate_incidents.py --db instance/divciber.db

O critério não usa ID. Ele agrupa por tipo, data, relatório, mensagem,
chamado/RDS, CPA e BTL normalizados.
"""

from __future__ import annotations

import argparse
import csv
import json
import re
import sqlite3
import unicodedata
from collections import defaultdict
from datetime import datetime
from pathlib import Path


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


def description_key(row: sqlite3.Row) -> str:
    text = row["description_plain_text"] or row["description"] or ""
    text = re.sub(r"<[^>]+>", " ", str(text))
    return compact(text)


def row_summary(row: sqlite3.Row) -> dict:
    return {
        "id": row["id"],
        "incident_type": row["incident_type"] or "",
        "start_date": norm_date(row["start_date"]),
        "report_number": row["report_number"] or "",
        "message_number": row["message_number"] or "",
        "ticket_number": row["ticket_number"] or "",
        "cpa": row["cpa"] or "",
        "btl": row["btl"] or "",
        "status_incident": row["status_incident"] or "",
        "created_at": row["created_at"] or "",
        "updated_at": row["updated_at"] or "",
        "description_hash": description_key(row)[:32],
    }


def scan(con: sqlite3.Connection) -> dict:
    rows = con.execute("SELECT * FROM incidente WHERE deleted_at IS NULL ORDER BY id").fetchall()
    groups: dict[tuple[str, ...], list[sqlite3.Row]] = defaultdict(list)
    for row in rows:
        key = operational_key(row)
        if any(key):
            groups[key].append(row)

    duplicate_groups = [group for group in groups.values() if len(group) > 1]
    duplicate_groups.sort(key=lambda group: (-len(group), [row["id"] for row in group]))

    payload = {
        "total_active_incidents": len(rows),
        "duplicate_groups": len(duplicate_groups),
        "duplicate_incidents": sum(len(group) for group in duplicate_groups),
        "duplicate_ids_to_remove_if_keep_lowest": sum(len(group) - 1 for group in duplicate_groups),
        "groups": [],
    }
    for index, group in enumerate(duplicate_groups, 1):
        ids = [row["id"] for row in group]
        payload["groups"].append({
            "group": index,
            "ids": ids,
            "keep_lowest_id": min(ids),
            "description_variants": len({description_key(row) for row in group}),
            "records": [row_summary(row) for row in group],
        })
    return payload


def write_reports(payload: dict, report_dir: Path) -> tuple[Path, Path]:
    report_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    json_path = report_dir / f"incident_duplicate_scan_{stamp}.json"
    csv_path = report_dir / f"incident_duplicate_scan_{stamp}.csv"
    json_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    rows = []
    for group in payload["groups"]:
        for record in group["records"]:
            rows.append({
                "grupo": group["group"],
                "id": record["id"],
                "manter_menor_id": "sim" if record["id"] == group["keep_lowest_id"] else "nao",
                "ids_grupo": ",".join(str(item) for item in group["ids"]),
                "variantes_descricao": group["description_variants"],
                "tipo": record["incident_type"],
                "data": record["start_date"],
                "relatorio": record["report_number"],
                "mensagem": record["message_number"],
                "ticket": record["ticket_number"],
                "cpa": record["cpa"],
                "btl": record["btl"],
                "status": record["status_incident"],
                "criado": record["created_at"],
                "atualizado": record["updated_at"],
            })
    with csv_path.open("w", newline="", encoding="utf-8-sig") as handle:
        if rows:
            writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
            writer.writeheader()
            writer.writerows(rows)
    return json_path, csv_path


def main() -> int:
    parser = argparse.ArgumentParser(description="Verifica incidentes duplicados sem alterar o banco.")
    parser.add_argument("--db", type=Path, default=Path("instance/divciber.db"), help="Caminho do banco SQLite.")
    parser.add_argument("--report-dir", type=Path, default=Path("instance/duplicate_scans"), help="Diretorio dos relatorios.")
    args = parser.parse_args()

    db_path = args.db.resolve()
    if not db_path.is_file():
        parser.error(f"Banco nao encontrado: {db_path}")

    con = sqlite3.connect(db_path)
    con.row_factory = sqlite3.Row
    try:
        payload = scan(con)
        payload["database"] = str(db_path)
        payload["integrity_check"] = con.execute("PRAGMA integrity_check").fetchone()[0]
        payload["foreign_key_check"] = [list(row) for row in con.execute("PRAGMA foreign_key_check").fetchall()]
        json_path, csv_path = write_reports(payload, args.report_dir)
        print(json.dumps({
            "database": str(db_path),
            "active_incidents": payload["total_active_incidents"],
            "duplicate_groups": payload["duplicate_groups"],
            "duplicate_incidents": payload["duplicate_incidents"],
            "duplicate_ids_to_remove_if_keep_lowest": payload["duplicate_ids_to_remove_if_keep_lowest"],
            "integrity_check": payload["integrity_check"],
            "foreign_key_check": payload["foreign_key_check"],
            "json_report": str(json_path),
            "csv_report": str(csv_path),
        }, ensure_ascii=False, indent=2))
    finally:
        con.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
