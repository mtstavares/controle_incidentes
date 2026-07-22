"""Controlled importer for legacy DivCiber SQLite databases.

The script intentionally does not run during application startup. It reads a
legacy database, normalizes values, and merges them into an existing current
database inside a single transaction. It never logs passwords or descriptions.
"""

from __future__ import annotations

import argparse
import json
import re
import shutil
import sqlite3
import sys
import unicodedata
from contextlib import closing
from dataclasses import dataclass, field
from datetime import datetime, timezone
from html.parser import HTMLParser
from pathlib import Path
from typing import Any

REQUIRED_LEGACY_TABLES = {
    "user",
    "incidente",
    "incidente_obs",
    "status_incidente",
    "tipo_incidente",
    "unidades",
}

REQUIRED_CURRENT_TABLES = {
    "user",
    "incidente",
    "incidente_obs",
    "status_incidente",
    "tipo_incidente",
    "unidades",
    "organizational_commands",
    "organizational_units",
}

SENSITIVE_COLUMNS = {"password", "token", "cookie", "secret", "authorization"}
MAX_TEXT_LOG_LENGTH = 120


class _HTMLTextExtractor(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.parts: list[str] = []

    def handle_data(self, data: str) -> None:
        self.parts.append(data)

    def text(self) -> str:
        return normalize_text(" ".join(self.parts), preserve_newlines=False)


@dataclass
class MigrationReport:
    started_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    finished_at: str | None = None
    dry_run: bool = False
    backups: dict[str, str] = field(default_factory=dict)
    legacy_counts: dict[str, int] = field(default_factory=dict)
    inserted: dict[str, int] = field(default_factory=dict)
    updated: dict[str, int] = field(default_factory=dict)
    skipped: dict[str, int] = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)
    integrity_check: list[Any] = field(default_factory=list)
    foreign_key_check: list[Any] = field(default_factory=list)

    def inc(self, bucket: str, key: str, amount: int = 1) -> None:
        target = getattr(self, bucket)
        target[key] = target.get(key, 0) + amount

    def warn(self, message: str) -> None:
        self.warnings.append(message[:500])

    def finish(self) -> None:
        self.finished_at = datetime.now(timezone.utc).isoformat()

    def as_dict(self) -> dict[str, Any]:
        return {
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "dry_run": self.dry_run,
            "backups": self.backups,
            "legacy_counts": self.legacy_counts,
            "inserted": self.inserted,
            "updated": self.updated,
            "skipped": self.skipped,
            "warnings": self.warnings,
            "integrity_check": self.integrity_check,
            "foreign_key_check": self.foreign_key_check,
        }


def normalize_text(value: Any, *, preserve_newlines: bool = True, max_length: int | None = None) -> str:
    if value is None:
        return ""
    text = str(value)
    text = _repair_mojibake(text)
    text = unicodedata.normalize("NFC", text)
    allowed_controls = {"\n", "\r", "\t"} if preserve_newlines else set()
    text = "".join(char for char in text if ord(char) >= 32 or char in allowed_controls)
    if preserve_newlines:
        lines = [re.sub(r"[ \t]+", " ", line).strip() for line in text.splitlines()]
        text = "\n".join(line for line in lines if line)
    else:
        text = re.sub(r"\s+", " ", text).strip()
    if max_length:
        text = text[:max_length]
    return text


def normalize_key(value: Any) -> str:
    text = unicodedata.normalize("NFKD", normalize_text(value, preserve_newlines=False))
    text = "".join(char for char in text if not unicodedata.combining(char))
    return text.casefold()


def normalize_cpa(value: Any) -> str:
    text = normalize_text(value, preserve_newlines=False, max_length=100).upper()
    text = re.sub(r"\s*-\s*SEDE$", "", text)
    text = re.sub(r"^CPA/M\s*-?\s*(\d+)$", r"CPA/M-\1", text)
    text = re.sub(r"^CPA/M(\d+)$", r"CPA/M-\1", text)
    text = text.replace("CPAMB", "CPAmb").replace("CPRV", "CPRv")
    if text.startswith("CPA/M-"):
        return text
    return normalize_text(text, preserve_newlines=False, max_length=100)


def normalize_unit(value: Any, cpa: str) -> str:
    text = normalize_text(value, preserve_newlines=False, max_length=100)
    if normalize_key(text) in {normalize_key("SEDE"), normalize_key(f"{cpa} - SEDE")}:
        return "SEDE"
    if normalize_key(text).startswith(normalize_key(cpa)) and "sede" in normalize_key(text):
        return "SEDE"
    text = re.sub(r"(\d+)º\s*BPM/M", r"\1º BPM/M", text, flags=re.IGNORECASE)
    text = re.sub(r"(\d+)º\s*BAEP", r"\1º BAEP", text, flags=re.IGNORECASE)
    return text.upper() if text.upper() in {"SEDE", "COPOM", "CIPM"} else text


def normalized_unit_name(value: str) -> str:
    return normalize_text(value, preserve_newlines=False).casefold()


def parse_datetime(value: Any, report: MigrationReport, context: str, *, required: bool = True) -> str | None:
    text = normalize_text(value, preserve_newlines=False)
    if not text:
        if required:
            report.warn(f"{context}: data obrigatória ausente.")
        return None
    candidates = [
        "%Y-%m-%d %H:%M:%S.%f",
        "%Y-%m-%d %H:%M:%S",
        "%Y-%m-%d",
        "%d/%m/%Y %H:%M:%S",
        "%d/%m/%Y",
    ]
    for fmt in candidates:
        try:
            parsed = datetime.strptime(text, fmt)
            return parsed.replace(microsecond=0).isoformat(sep=" ")
        except ValueError:
            continue
    try:
        parsed = datetime.fromisoformat(text)
        return parsed.replace(tzinfo=None, microsecond=0).isoformat(sep=" ")
    except ValueError:
        report.warn(f"{context}: data inválida não migrada ({_safe_log_value(text)}).")
        return None


def _repair_mojibake(text: str) -> str:
    suspicious_before = _mojibake_score(text)
    if suspicious_before == 0:
        return text
    try:
        repaired = text.encode("latin-1").decode("utf-8")
    except UnicodeError:
        return text
    return repaired if _mojibake_score(repaired) < suspicious_before else text


def _mojibake_score(text: str) -> int:
    markers = ("\u00c3", "\u00c2", "\ufffd", "\u00e2\u20ac", "N?o")
    return sum(text.count(token) for token in markers)


def _safe_log_value(value: Any) -> str:
    text = normalize_text(value, preserve_newlines=False)
    return text[:MAX_TEXT_LOG_LENGTH]


def description_plain_text(html: str) -> str:
    parser = _HTMLTextExtractor()
    try:
        parser.feed(html or "")
        return parser.text()
    except Exception:
        return normalize_text(re.sub(r"<[^>]+>", " ", html or ""), preserve_newlines=False)


def connect_sqlite(path: Path, *, readonly: bool = False) -> sqlite3.Connection:
    uri = f"file:{path}?mode=ro" if readonly else str(path)
    con = sqlite3.connect(uri, uri=readonly)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA foreign_keys = ON")
    return con


def table_names(con: sqlite3.Connection) -> set[str]:
    return {
        row["name"]
        for row in con.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'")
    }


def table_columns(con: sqlite3.Connection, table: str) -> set[str]:
    return {row["name"] for row in con.execute(f'PRAGMA table_info("{table}")')}


def require_tables(con: sqlite3.Connection, required: set[str], label: str) -> None:
    missing = sorted(required - table_names(con))
    if missing:
        raise ValueError(f"{label}: tabelas ausentes: {', '.join(missing)}")


def backup_database(source: Path, backup_dir: Path, prefix: str) -> Path:
    backup_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    target = backup_dir / f"{prefix}_{stamp}{source.suffix}.backup"
    shutil.copy2(source, target)
    with closing(sqlite3.connect(f"file:{target}?mode=ro", uri=True)) as con:
        result = con.execute("PRAGMA integrity_check").fetchone()[0]
    if result != "ok":
        raise ValueError(f"Backup inválido para {source.name}: integrity_check={result}")
    return target


def insert_row(con: sqlite3.Connection, table: str, values: dict[str, Any]) -> int:
    columns = table_columns(con, table)
    clean_values = {key: value for key, value in values.items() if key in columns}
    placeholders = ", ".join("?" for _ in clean_values)
    names = ", ".join(f'"{key}"' for key in clean_values)
    cur = con.execute(
        f'INSERT INTO "{table}" ({names}) VALUES ({placeholders})',
        list(clean_values.values()),
    )
    return int(cur.lastrowid)


def update_row(con: sqlite3.Connection, table: str, row_id: int, values: dict[str, Any]) -> None:
    columns = table_columns(con, table)
    clean_values = {key: value for key, value in values.items() if key in columns}
    if not clean_values:
        return
    assignments = ", ".join(f'"{key}" = ?' for key in clean_values)
    con.execute(
        f'UPDATE "{table}" SET {assignments} WHERE id = ?',
        [*clean_values.values(), row_id],
    )


def find_by_normalized(con: sqlite3.Connection, table: str, column: str, value: str) -> sqlite3.Row | None:
    for row in con.execute(f'SELECT * FROM "{table}"'):
        if normalize_key(row[column]) == normalize_key(value):
            return row
    return None


def get_or_create_status(con: sqlite3.Connection, name: str, desc: str | None, report: MigrationReport) -> str:
    row = find_by_normalized(con, "status_incidente", "status", name)
    if row:
        return row["status"]
    insert_row(con, "status_incidente", {"status": name, "desc_status": desc})
    report.inc("inserted", "status_incidente")
    return name


def get_or_create_type(con: sqlite3.Connection, name: str, desc: str | None, report: MigrationReport) -> str:
    row = find_by_normalized(con, "tipo_incidente", "tipo_incidente", name)
    if row:
        return row["tipo_incidente"]
    insert_row(con, "tipo_incidente", {"tipo_incidente": name, "desc_incidente": desc})
    report.inc("inserted", "tipo_incidente")
    return name


def get_or_create_command(con: sqlite3.Connection, name: str, report: MigrationReport) -> int:
    row = find_by_normalized(con, "organizational_commands", "name", name)
    if row:
        return int(row["id"])
    sort_order = con.execute("SELECT COALESCE(MAX(sort_order), 0) + 1 FROM organizational_commands").fetchone()[0]
    command_id = insert_row(
        con,
        "organizational_commands",
        {"name": name, "active": 1, "sort_order": sort_order, "created_at": now_sql(), "updated_at": now_sql()},
    )
    report.inc("inserted", "organizational_commands")
    return command_id


def get_or_create_unit(con: sqlite3.Connection, command_id: int, cpa: str, unit_name: str, report: MigrationReport) -> int:
    normalized = normalized_unit_name(unit_name)
    row = con.execute(
        "SELECT * FROM organizational_units WHERE command_id = ? AND normalized_name = ?",
        (command_id, normalized),
    ).fetchone()
    if row:
        return int(row["id"])
    sort_order = con.execute(
        "SELECT COALESCE(MAX(sort_order), 0) + 1 FROM organizational_units WHERE command_id = ?",
        (command_id,),
    ).fetchone()[0]
    unit_id = insert_row(
        con,
        "organizational_units",
        {
            "command_id": command_id,
            "name": unit_name,
            "normalized_name": normalized,
            "active": 1,
            "sort_order": sort_order,
            "created_at": now_sql(),
            "updated_at": now_sql(),
        },
    )
    if not con.execute("SELECT 1 FROM unidades WHERE cpa = ? AND btl = ?", (cpa, unit_name)).fetchone():
        insert_row(con, "unidades", {"cpa": cpa, "btl": unit_name})
    report.inc("inserted", "organizational_units")
    return unit_id


def now_sql() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat(sep=" ")


def migrate_users(src: sqlite3.Connection, dst: sqlite3.Connection, report: MigrationReport) -> dict[int, int]:
    user_map: dict[int, int] = {}
    for legacy in src.execute("SELECT * FROM user"):
        username = normalize_text(legacy["username"], preserve_newlines=False, max_length=50)
        email = normalize_text(legacy["email"], preserve_newlines=False, max_length=255).lower()
        name = normalize_text(legacy["name"], preserve_newlines=False, max_length=150)
        profile = normalize_text(legacy["profile"], preserve_newlines=False, max_length=50)
        if profile not in {"Admin", "User", "Viewer"}:
            profile = "Viewer"
            report.warn(f"Usuário legado {legacy['id']}: perfil inválido convertido para Viewer.")

        by_username = dst.execute("SELECT * FROM user WHERE lower(username) = lower(?)", (username,)).fetchone()
        by_email = dst.execute("SELECT * FROM user WHERE lower(email) = lower(?)", (email,)).fetchone()
        if by_username and by_email and by_username["id"] != by_email["id"]:
            user_id = create_disabled_legacy_user(dst, legacy["id"], report)
        elif by_username or by_email:
            user_id = int((by_username or by_email)["id"])
            report.inc("skipped", "user_existing")
        else:
            user_id = insert_row(
                dst,
                "user",
                {
                    "username": username,
                    "name": name,
                    "email": email,
                    "profile": profile,
                    "is_temp_password": int(bool(legacy["is_temp_password"])),
                    "must_change_password": int(bool(legacy["is_temp_password"])),
                    "is_active": 1,
                    "password": legacy["password"],
                    "created_at": now_sql(),
                    "updated_at": now_sql(),
                },
            )
            report.inc("inserted", "user")
        user_map[int(legacy["id"])] = user_id
    return user_map


def create_disabled_legacy_user(dst: sqlite3.Connection, legacy_id: int, report: MigrationReport) -> int:
    username = f"legacy_user_{legacy_id}"
    existing = dst.execute("SELECT id FROM user WHERE username = ?", (username,)).fetchone()
    if existing:
        return int(existing["id"])
    report.warn(f"Usuário legado {legacy_id}: conflito username/email; criado placeholder inativo.")
    return insert_row(
        dst,
        "user",
        {
            "username": username,
            "name": f"Usuário legado {legacy_id}",
            "email": f"legacy-user-{legacy_id}@invalid.local",
            "profile": "Viewer",
            "is_temp_password": 0,
            "must_change_password": 0,
            "is_active": 0,
            "password": "disabled-legacy-account",
            "created_at": now_sql(),
            "updated_at": now_sql(),
            "deleted_at": now_sql(),
        },
    )


def migrate_library(src: sqlite3.Connection, dst: sqlite3.Connection, report: MigrationReport) -> None:
    for row in src.execute("SELECT * FROM status_incidente"):
        name = normalize_text(row["status"], preserve_newlines=False, max_length=50)
        if name:
            get_or_create_status(dst, name, normalize_text(row["desc_status"]), report)

    for row in src.execute("SELECT * FROM tipo_incidente"):
        name = normalize_text(row["tipo_incidente"], preserve_newlines=False, max_length=100)
        if name:
            get_or_create_type(dst, name, normalize_text(row["desc_incidente"]), report)

    pairs: set[tuple[str, str]] = set()
    for row in src.execute("SELECT cpa, btl FROM unidades"):
        cpa = normalize_cpa(row["cpa"])
        unit = normalize_unit(row["btl"], cpa)
        if cpa and unit:
            pairs.add((cpa, unit))
    for row in src.execute("SELECT DISTINCT cpa, btl FROM incidente"):
        cpa = normalize_cpa(row["cpa"])
        unit = normalize_unit(row["btl"], cpa)
        if cpa and unit:
            pairs.add((cpa, unit))
        else:
            report.warn("Incidente legado com CPA/Batalhão vazio não pôde criar biblioteca.")

    for cpa, unit in sorted(pairs):
        command_id = get_or_create_command(dst, cpa, report)
        get_or_create_unit(dst, command_id, cpa, unit, report)


def migrate_incidents(src: sqlite3.Connection, dst: sqlite3.Connection, report: MigrationReport, user_map: dict[int, int]) -> dict[int, int]:
    incident_map: dict[int, int] = {}
    for row in src.execute("SELECT * FROM incidente ORDER BY id"):
        start_date = parse_datetime(row["start_date"], report, f"Incidente {row['id']} start_date")
        if not start_date:
            report.inc("skipped", "incidente_invalid_date")
            continue
        end_date = parse_datetime(row["end_date"], report, f"Incidente {row['id']} end_date", required=False)
        status = get_or_create_status(
            dst,
            normalize_text(row["status_incident"], preserve_newlines=False, max_length=50),
            None,
            report,
        )
        incident_type = get_or_create_type(
            dst,
            normalize_text(row["incident_type"], preserve_newlines=False, max_length=100),
            None,
            report,
        )
        cpa = normalize_cpa(row["cpa"])
        btl = normalize_unit(row["btl"], cpa)
        command_id = get_or_create_command(dst, cpa, report)
        unit_id = get_or_create_unit(dst, command_id, cpa, btl, report)
        user_id = user_map.get(int(row["user_id"]))
        if not user_id:
            user_id = create_disabled_legacy_user(dst, int(row["user_id"]), report)

        description = normalize_text(row["description"], preserve_newlines=True)
        report_number = normalize_text(row["report_number"], preserve_newlines=False, max_length=50) or f"LEGADO-{row['id']}"
        ticket_number = normalize_text(row["ticket_number"], preserve_newlines=False, max_length=50)
        existing = dst.execute(
            """
            SELECT id FROM incidente
            WHERE report_number = ? AND start_date = ? AND incident_type = ? AND cpa = ? AND btl = ?
            LIMIT 1
            """,
            (report_number, start_date, incident_type, cpa, btl),
        ).fetchone()
        values = {
            "message_number": None,
            "incident_type": incident_type,
            "report_number": report_number,
            "ticket_number": ticket_number,
            "cpa": cpa,
            "btl": btl,
            "cia": normalize_text(row["cia"], preserve_newlines=False, max_length=100),
            "description": description,
            "description_plain_text": description_plain_text(description),
            "start_date": start_date,
            "end_date": end_date,
            "status_incident": status,
            "command_id": command_id,
            "unit_id": unit_id,
            "user_id": user_id,
            "created_at": start_date,
            "updated_at": now_sql(),
        }
        if existing:
            incident_id = int(existing["id"])
            update_row(dst, "incidente", incident_id, values)
            report.inc("updated", "incidente")
        else:
            incident_id = insert_row(dst, "incidente", values)
            report.inc("inserted", "incidente")
        incident_map[int(row["id"])] = incident_id
    return incident_map


def migrate_observations(
    src: sqlite3.Connection,
    dst: sqlite3.Connection,
    report: MigrationReport,
    user_map: dict[int, int],
    incident_map: dict[int, int],
) -> None:
    for row in src.execute("SELECT * FROM incidente_obs ORDER BY id"):
        incident_id = incident_map.get(int(row["incidente_id"]))
        if not incident_id:
            report.inc("skipped", "incidente_obs_orphan")
            report.warn(f"Observação legado {row['id']}: incidente não migrado.")
            continue
        user_id = user_map.get(int(row["usuario_id"])) or create_disabled_legacy_user(dst, int(row["usuario_id"]), report)
        observed_at = parse_datetime(row["data_observacao"], report, f"Observação {row['id']} data_observacao")
        if not observed_at:
            report.inc("skipped", "incidente_obs_invalid_date")
            continue
        text = normalize_text(row["texto_observacao"], preserve_newlines=True)
        existing = dst.execute(
            """
            SELECT id FROM incidente_obs
            WHERE incidente_id = ? AND usuario_id = ? AND data_observacao = ? AND texto_observacao = ?
            LIMIT 1
            """,
            (incident_id, user_id, observed_at, text),
        ).fetchone()
        if existing:
            report.inc("skipped", "incidente_obs_existing")
            continue
        insert_row(
            dst,
            "incidente_obs",
            {
                "texto_observacao": text,
                "data_observacao": observed_at,
                "usuario_id": user_id,
                "incidente_id": incident_id,
                "created_at": observed_at,
                "updated_at": now_sql(),
            },
        )
        report.inc("inserted", "incidente_obs")


def collect_counts(con: sqlite3.Connection, tables: set[str]) -> dict[str, int]:
    return {table: con.execute(f'SELECT COUNT(*) FROM "{table}"').fetchone()[0] for table in sorted(tables)}


def run_migration(
    legacy_db: Path,
    current_db: Path,
    *,
    dry_run: bool = False,
    backup_dir: Path | None = None,
    report_path: Path | None = None,
) -> MigrationReport:
    legacy_db = legacy_db.resolve()
    current_db = current_db.resolve()
    if legacy_db == current_db:
        raise ValueError("Banco legado e banco atual não podem apontar para o mesmo arquivo.")
    if not legacy_db.exists():
        raise FileNotFoundError(f"Banco legado não encontrado: {legacy_db}")
    if not current_db.exists():
        raise FileNotFoundError(f"Banco atual não encontrado: {current_db}")

    report = MigrationReport(dry_run=dry_run)
    backup_dir = backup_dir or current_db.parent / "backups"
    if not dry_run:
        report.backups["legacy"] = str(backup_database(legacy_db, backup_dir, "legacy"))
        report.backups["current"] = str(backup_database(current_db, backup_dir, "current"))

    with closing(connect_sqlite(legacy_db, readonly=True)) as src, closing(connect_sqlite(current_db)) as dst:
        require_tables(src, REQUIRED_LEGACY_TABLES, "Banco legado")
        require_tables(dst, REQUIRED_CURRENT_TABLES, "Banco atual")
        report.legacy_counts = collect_counts(src, REQUIRED_LEGACY_TABLES)
        dst.execute("BEGIN IMMEDIATE")
        try:
            migrate_library(src, dst, report)
            user_map = migrate_users(src, dst, report)
            incident_map = migrate_incidents(src, dst, report, user_map)
            migrate_observations(src, dst, report, user_map, incident_map)
            report.integrity_check = [tuple(row) for row in dst.execute("PRAGMA integrity_check").fetchall()]
            report.foreign_key_check = [tuple(row) for row in dst.execute("PRAGMA foreign_key_check").fetchall()]
            if report.integrity_check != [("ok",)] or report.foreign_key_check:
                raise ValueError("Validação de integridade falhou após migração.")
            if dry_run:
                dst.rollback()
            else:
                dst.commit()
        except Exception:
            dst.rollback()
            raise
    report.finish()
    if report_path:
        report_path.parent.mkdir(parents=True, exist_ok=True)
        report_path.write_text(json.dumps(report.as_dict(), ensure_ascii=False, indent=2), encoding="utf-8")
    return report


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Migra dados de um banco legado DivCiber SQLite para o schema atual.")
    parser.add_argument("--legacy-db", required=True, type=Path, help="Caminho explícito do banco legado SQLite.")
    parser.add_argument("--current-db", required=True, type=Path, help="Caminho explícito do banco atual SQLite.")
    parser.add_argument("--dry-run", action="store_true", help="Executa toda a validação e desfaz a transação ao final.")
    parser.add_argument("--backup-dir", type=Path, help="Diretório de backups. Padrão: <diretório do banco atual>/backups.")
    parser.add_argument("--report", type=Path, help="Arquivo JSON para relatório da migração.")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv or sys.argv[1:])
    try:
        report = run_migration(
            args.legacy_db,
            args.current_db,
            dry_run=args.dry_run,
            backup_dir=args.backup_dir,
            report_path=args.report,
        )
    except Exception as exc:
        print(f"ERRO: migração abortada com rollback: {exc}", file=sys.stderr)
        return 1
    print(json.dumps(report.as_dict(), ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
