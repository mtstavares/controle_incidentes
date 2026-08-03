"""Migra incidentes e anexos de um backup legado DivCiber.

O script trata a pasta de origem como somente leitura, gera relatório antes de
gravar, faz backup verificável do banco/uploads atuais e mantém a importação
idempotente por chaves normalizadas e hash de anexos.
"""

from __future__ import annotations

import argparse
import hashlib
import html
import json
import mimetypes
import os
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
from uuid import uuid4


REQUIRED_LEGACY_TABLES = {
    "incidente",
    "incidente_obs",
    "status_incidente",
    "tipo_incidente",
    "unidades",
    "user",
}
REQUIRED_CURRENT_TABLES = {
    "incidente",
    "incidente_obs",
    "incident_attachments",
    "organizational_commands",
    "organizational_units",
    "status_incidente",
    "tipo_incidente",
    "unidades",
    "user",
}
LEGACY_UPLOAD_ROOT_PARTS = ("uploads", "incidentes")
ALLOWED_ATTACHMENT_EXTENSIONS = {
    ".pdf",
    ".png",
    ".jpg",
    ".jpeg",
    ".webp",
    ".doc",
    ".docx",
    ".xls",
    ".xlsx",
}
BLOCKED_ATTACHMENT_SUFFIXES = {
    ".exe",
    ".dll",
    ".ps1",
    ".bat",
    ".cmd",
    ".js",
    ".vbs",
    ".jar",
    ".msi",
    ".scr",
    ".com",
    ".hta",
    ".lnk",
    ".iso",
    ".img",
    ".html",
    ".htm",
    ".svg",
    ".zip",
    ".rar",
    ".7z",
    ".tar",
    ".gz",
}
SIGNATURES = {
    ".pdf": (b"%PDF-",),
    ".png": (b"\x89PNG\r\n\x1a\n",),
    ".jpg": (b"\xff\xd8\xff",),
    ".jpeg": (b"\xff\xd8\xff",),
    ".webp": (b"RIFF",),
    ".doc": (b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1",),
    ".xls": (b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1",),
    ".docx": (b"PK\x03\x04", b"PK\x05\x06", b"PK\x07\x08"),
    ".xlsx": (b"PK\x03\x04", b"PK\x05\x06", b"PK\x07\x08"),
}
MIME_BY_EXTENSION = {
    ".pdf": "application/pdf",
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".webp": "image/webp",
    ".doc": "application/msword",
    ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    ".xls": "application/vnd.ms-excel",
    ".xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
}
RESERVED_WINDOWS_NAMES = {
    "con",
    "prn",
    "aux",
    "nul",
    *(f"com{i}" for i in range(1, 10)),
    *(f"lpt{i}" for i in range(1, 10)),
}
MAX_ATTACHMENT_SIZE = 50 * 1024 * 1024
MAX_CHECKSUM_FILE_SIZE = 200 * 1024 * 1024
MAX_TEXT_LENGTH = 100_000


class MigrationError(RuntimeError):
    pass


class CriticalMigrationError(MigrationError):
    pass


class _TextExtractor(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.parts: list[str] = []

    def handle_data(self, data: str) -> None:
        self.parts.append(data)

    def text(self) -> str:
        return normalize_text(" ".join(self.parts), preserve_newlines=False)


@dataclass
class Issue:
    severity: str
    category: str
    message: str
    context: dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        return {
            "severity": self.severity,
            "category": self.category,
            "message": self.message[:500],
            "context": sanitize_context(self.context),
        }


@dataclass
class MigrationReport:
    run_id: str
    mode: str
    source_root: str
    started_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    finished_at: str | None = None
    source_db: str | None = None
    current_db: str | None = None
    reports: dict[str, str] = field(default_factory=dict)
    backups: dict[str, Any] = field(default_factory=dict)
    schema_legacy: dict[str, Any] = field(default_factory=dict)
    schema_current: dict[str, Any] = field(default_factory=dict)
    field_mapping: dict[str, str] = field(default_factory=dict)
    table_counts_legacy: dict[str, int] = field(default_factory=dict)
    table_counts_current_before: dict[str, int] = field(default_factory=dict)
    table_counts_current_after: dict[str, int] = field(default_factory=dict)
    checksums_before: dict[str, Any] = field(default_factory=dict)
    checksums_after: dict[str, Any] = field(default_factory=dict)
    incidents_analyzed: int = 0
    incidents_inserted: int = 0
    incidents_updated: int = 0
    incidents_skipped_duplicate: int = 0
    incidents_conflicts: int = 0
    observations_inserted: int = 0
    observations_skipped_duplicate: int = 0
    attachments_analyzed: int = 0
    attachments_imported: int = 0
    attachments_skipped_duplicate: int = 0
    attachments_orphan: int = 0
    attachments_invalid: int = 0
    encoding_corrections: int = 0
    date_corrections: int = 0
    relationship_corrections: int = 0
    commands_created: int = 0
    units_created: int = 0
    statuses_created: int = 0
    types_created: int = 0
    current_files_created: list[str] = field(default_factory=list)
    migrated_legacy_ids: list[int] = field(default_factory=list)
    updated_current_ids: list[int] = field(default_factory=list)
    skipped_legacy_ids: list[int] = field(default_factory=list)
    quarantined: list[dict[str, Any]] = field(default_factory=list)
    issues: list[Issue] = field(default_factory=list)
    integrity_check: list[Any] = field(default_factory=list)
    foreign_key_check: list[Any] = field(default_factory=list)
    rollback: dict[str, Any] = field(default_factory=dict)

    def add_issue(self, severity: str, category: str, message: str, **context: Any) -> None:
        self.issues.append(Issue(severity, category, message, context))

    def has_critical_errors(self) -> bool:
        return any(issue.severity == "critical" for issue in self.issues)

    def finish(self) -> None:
        self.finished_at = datetime.now(timezone.utc).isoformat()

    def as_dict(self) -> dict[str, Any]:
        return {
            "run_id": self.run_id,
            "mode": self.mode,
            "source_root": self.source_root,
            "source_db": self.source_db,
            "current_db": self.current_db,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "reports": self.reports,
            "backups": self.backups,
            "schema_legacy": self.schema_legacy,
            "schema_current": self.schema_current,
            "field_mapping": self.field_mapping,
            "table_counts_legacy": self.table_counts_legacy,
            "table_counts_current_before": self.table_counts_current_before,
            "table_counts_current_after": self.table_counts_current_after,
            "checksums_before": self.checksums_before,
            "checksums_after": self.checksums_after,
            "incidents_analyzed": self.incidents_analyzed,
            "incidents_inserted": self.incidents_inserted,
            "incidents_updated": self.incidents_updated,
            "incidents_skipped_duplicate": self.incidents_skipped_duplicate,
            "incidents_conflicts": self.incidents_conflicts,
            "observations_inserted": self.observations_inserted,
            "observations_skipped_duplicate": self.observations_skipped_duplicate,
            "attachments_analyzed": self.attachments_analyzed,
            "attachments_imported": self.attachments_imported,
            "attachments_skipped_duplicate": self.attachments_skipped_duplicate,
            "attachments_orphan": self.attachments_orphan,
            "attachments_invalid": self.attachments_invalid,
            "encoding_corrections": self.encoding_corrections,
            "date_corrections": self.date_corrections,
            "relationship_corrections": self.relationship_corrections,
            "commands_created": self.commands_created,
            "units_created": self.units_created,
            "statuses_created": self.statuses_created,
            "types_created": self.types_created,
            "current_files_created": self.current_files_created,
            "migrated_legacy_ids": self.migrated_legacy_ids,
            "updated_current_ids": self.updated_current_ids,
            "skipped_legacy_ids": self.skipped_legacy_ids,
            "quarantined": self.quarantined,
            "issues": [issue.as_dict() for issue in self.issues],
            "integrity_check": self.integrity_check,
            "foreign_key_check": self.foreign_key_check,
            "rollback": self.rollback,
        }


def repo_root() -> Path:
    return Path(__file__).resolve().parents[1]


def default_source_root() -> Path:
    return repo_root() / "2026-08-03"


def run_id() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S") + "-" + uuid4().hex[:8]


def sanitize_context(context: dict[str, Any]) -> dict[str, Any]:
    sanitized: dict[str, Any] = {}
    for key, value in context.items():
        key_lower = key.lower()
        if any(token in key_lower for token in ("password", "senha", "cpf", "email", "token", "cookie")):
            sanitized[key] = "***"
        elif isinstance(value, (int, float, bool)) or value is None:
            sanitized[key] = value
        else:
            sanitized[key] = normalize_text(value, preserve_newlines=False, max_length=200)
    return sanitized


def _mojibake_score(text: str) -> int:
    return sum(text.count(token) for token in ("\u00c3", "\u00c2", "\ufffd", "\u00e2\u20ac", "N?o", "??"))


def _repair_mojibake(text: str) -> tuple[str, bool]:
    before = _mojibake_score(text)
    if before == 0:
        return text, False
    candidates = [text]
    for source_encoding in ("latin-1", "cp1252"):
        try:
            candidates.append(text.encode(source_encoding).decode("utf-8"))
        except UnicodeError:
            pass
    best = min(candidates, key=_mojibake_score)
    return best, best != text


def normalize_text(
    value: Any,
    *,
    preserve_newlines: bool = True,
    max_length: int | None = None,
) -> str:
    if value is None:
        return ""
    text, _ = _repair_mojibake(str(value))
    text = unicodedata.normalize("NFC", text)
    allowed_controls = {"\n", "\r", "\t"} if preserve_newlines else set()
    text = "".join(char for char in text if ord(char) >= 32 or char in allowed_controls)
    if preserve_newlines:
        lines = [re.sub(r"[ \t]+", " ", line).strip() for line in text.splitlines()]
        text = "\n".join(line for line in lines if line)
    else:
        text = re.sub(r"\s+", " ", text).strip()
    if max_length is not None:
        text = text[:max_length]
    return text


def normalize_key(value: Any) -> str:
    text = unicodedata.normalize("NFKD", normalize_text(value, preserve_newlines=False))
    text = "".join(char for char in text if not unicodedata.combining(char))
    return re.sub(r"\s+", " ", text).casefold().strip()


def normalize_cpa(value: Any) -> str:
    text = normalize_text(value, preserve_newlines=False, max_length=100)
    text = re.sub(r"\s*-\s*SEDE$", "", text, flags=re.IGNORECASE)
    text = re.sub(r"^CPA/M\s*-?\s*(\d+)$", r"CPA/M-\1", text, flags=re.IGNORECASE)
    text = re.sub(r"^CPA/M(\d+)$", r"CPA/M-\1", text, flags=re.IGNORECASE)
    replacements = {
        "CPAMB": "CPAmb",
        "CPRV": "CPRv",
        "CPI ": "CPI-",
        "CBI ": "CBI-",
    }
    upper_key = text.upper()
    for old, new in replacements.items():
        upper_key = upper_key.replace(old, new.upper())
    if upper_key.startswith(("CPA/M-", "CPI-", "CBI-")):
        return upper_key
    if upper_key in {"DIRETORIAS", "CPAMB", "CPRV", "CAES"}:
        return {"CPAMB": "CPAmb", "CPRV": "CPRv"}.get(upper_key, upper_key)
    return text


def normalize_unit(value: Any, cpa: str) -> str:
    text = normalize_text(value, preserve_newlines=False, max_length=100)
    key = normalize_key(text)
    cpa_key = normalize_key(cpa)
    if key in {"sede", f"{cpa_key} - sede"} or (key.startswith(cpa_key) and "sede" in key):
        return "SEDE"
    text = re.sub(r"(\d+)[º°]\s*BPM/M", r"\1º BPM/M", text, flags=re.IGNORECASE)
    text = re.sub(r"(\d+)[º°]\s*BAEP", r"\1º BAEP", text, flags=re.IGNORECASE)
    return text


def normalize_datetime(value: Any, report: MigrationReport, context: str, *, required: bool = True) -> str | None:
    text = normalize_text(value, preserve_newlines=False)
    if not text:
        if required:
            report.add_issue("error", "data", "Data obrigatória ausente.", context=context)
        return None
    formats = (
        "%Y-%m-%d %H:%M:%S.%f",
        "%Y-%m-%d %H:%M:%S",
        "%Y-%m-%d",
        "%d/%m/%Y %H:%M:%S",
        "%d/%m/%Y",
    )
    for fmt in formats:
        try:
            parsed = datetime.strptime(text, fmt)
            if fmt in {"%Y-%m-%d", "%d/%m/%Y"}:
                report.date_corrections += 1
            return parsed.replace(microsecond=0, tzinfo=None).isoformat(sep=" ")
        except ValueError:
            continue
    try:
        parsed = datetime.fromisoformat(text)
        if parsed.tzinfo is not None:
            parsed = parsed.astimezone(timezone.utc).replace(tzinfo=None)
            report.date_corrections += 1
        return parsed.replace(microsecond=0).isoformat(sep=" ")
    except ValueError:
        report.add_issue("error", "data", "Data inválida não migrada.", context=context)
        return None


def plain_text_from_html(value: str) -> str:
    parser = _TextExtractor()
    try:
        parser.feed(value or "")
        text = parser.text()
    except Exception:
        text = normalize_text(re.sub(r"<[^>]+>", " ", value or ""), preserve_newlines=False)
    return text[:MAX_TEXT_LENGTH]


def safe_description_html(value: str) -> tuple[str, str]:
    text = normalize_text(value, preserve_newlines=True, max_length=MAX_TEXT_LENGTH)
    if "<" in text and ">" in text:
        plain = plain_text_from_html(text)
        return text, plain or normalize_text(text, preserve_newlines=False)
    escaped = html.escape(text).replace("\n", "<br>")
    return escaped, normalize_text(text, preserve_newlines=False)


def connect_sqlite(path: Path, *, readonly: bool = False) -> sqlite3.Connection:
    if readonly:
        uri = f"file:{path.as_posix()}?mode=ro"
        con = sqlite3.connect(uri, uri=True)
    else:
        con = sqlite3.connect(path)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA foreign_keys = ON")
    return con


def table_names(con: sqlite3.Connection) -> set[str]:
    return {
        row["name"]
        for row in con.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'")
    }


def table_columns(con: sqlite3.Connection, table: str) -> dict[str, sqlite3.Row]:
    return {row["name"]: row for row in con.execute(f'PRAGMA table_info("{table}")')}


def inspect_schema(con: sqlite3.Connection) -> dict[str, Any]:
    schema: dict[str, Any] = {}
    for table in sorted(table_names(con)):
        columns = []
        for row in con.execute(f'PRAGMA table_info("{table}")'):
            columns.append({"name": row["name"], "type": row["type"], "notnull": row["notnull"], "pk": row["pk"]})
        fks = [dict(row) for row in con.execute(f'PRAGMA foreign_key_list("{table}")')]
        indexes = [dict(row) for row in con.execute(f'PRAGMA index_list("{table}")')]
        schema[table] = {"columns": columns, "foreign_keys": fks, "indexes": indexes}
    return schema


def collect_counts(con: sqlite3.Connection) -> dict[str, int]:
    return {table: con.execute(f'SELECT COUNT(*) FROM "{table}"').fetchone()[0] for table in sorted(table_names(con))}


def insert_row(con: sqlite3.Connection, table: str, values: dict[str, Any]) -> int:
    allowed = table_columns(con, table)
    clean_values = {key: value for key, value in values.items() if key in allowed}
    names = ", ".join(f'"{key}"' for key in clean_values)
    placeholders = ", ".join("?" for _ in clean_values)
    cur = con.execute(f'INSERT INTO "{table}" ({names}) VALUES ({placeholders})', list(clean_values.values()))
    return int(cur.lastrowid)


def update_row(con: sqlite3.Connection, table: str, row_id: int, values: dict[str, Any]) -> None:
    allowed = table_columns(con, table)
    clean_values = {key: value for key, value in values.items() if key in allowed}
    if not clean_values:
        return
    assignments = ", ".join(f'"{key}" = ?' for key in clean_values)
    con.execute(f'UPDATE "{table}" SET {assignments} WHERE id = ?', [*clean_values.values(), row_id])


def now_sql() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat(sep=" ")


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as file_obj:
        for chunk in iter(lambda: file_obj.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def display_path(path: Path) -> str:
    try:
        return path.resolve().relative_to(repo_root()).as_posix()
    except ValueError:
        return str(path.resolve())


def directory_checksums(root: Path, *, max_files: int = 5000) -> dict[str, str]:
    checksums: dict[str, str] = {}
    if not root.exists():
        return checksums
    root = root.resolve()
    for path in sorted(root.rglob("*")):
        if not path.is_file():
            continue
        rel = path.relative_to(root).as_posix()
        if len(checksums) >= max_files:
            checksums["__truncated__"] = f"mais de {max_files} arquivos"
            break
        if path.stat().st_size > MAX_CHECKSUM_FILE_SIZE:
            checksums[rel] = "SKIPPED_SIZE_LIMIT"
        else:
            checksums[rel] = sha256_file(path)
    return checksums


def ensure_child_path(root: Path, candidate: Path) -> Path:
    root_resolved = root.resolve()
    candidate_resolved = candidate.resolve()
    if candidate_resolved != root_resolved and root_resolved not in candidate_resolved.parents:
        raise CriticalMigrationError(f"Caminho fora da raiz autorizada: {candidate}")
    return candidate_resolved


def reject_reparse_points(root: Path, report: MigrationReport) -> None:
    for path in root.rglob("*"):
        if path.is_symlink():
            report.add_issue(
                "critical",
                "path_traversal",
                "Link simbólico ou junction encontrado na origem.",
                relative_path=path.relative_to(root).as_posix(),
            )


def find_legacy_database(source_root: Path, report: MigrationReport) -> Path:
    candidates = []
    for path in source_root.rglob("*"):
        if not path.is_file() or path.suffix.lower() not in {".db", ".sqlite", ".sqlite3"}:
            continue
        safe_path = ensure_child_path(source_root, path)
        try:
            with closing(connect_sqlite(safe_path, readonly=True)) as con:
                integrity = con.execute("PRAGMA integrity_check").fetchone()[0]
                tables = table_names(con)
                count = con.execute('SELECT COUNT(*) FROM "incidente"').fetchone()[0] if "incidente" in tables else 0
        except sqlite3.DatabaseError:
            continue
        score = len(tables & REQUIRED_LEGACY_TABLES)
        candidates.append((score, count, safe_path, integrity))
    candidates.sort(key=lambda item: (item[0], item[1]), reverse=True)
    if not candidates or candidates[0][0] < len(REQUIRED_LEGACY_TABLES):
        raise CriticalMigrationError("Nenhum banco legado válido com as tabelas obrigatórias foi encontrado.")
    chosen = candidates[0][2]
    if candidates[0][3] != "ok":
        raise CriticalMigrationError("Banco legado falhou no integrity_check.")
    if len(candidates) > 1:
        report.add_issue(
            "info",
            "origem",
            "Mais de um banco SQLite encontrado; selecionado o que possui dados legados.",
            selected=chosen.relative_to(source_root).as_posix(),
            candidates=len(candidates),
        )
    return chosen


def find_legacy_upload_root(source_root: Path) -> Path:
    candidate = source_root.joinpath(*LEGACY_UPLOAD_ROOT_PARTS)
    if candidate.exists():
        return ensure_child_path(source_root, candidate)
    uploads = source_root / "uploads"
    if uploads.exists():
        return ensure_child_path(source_root, uploads)
    raise CriticalMigrationError("Pasta de uploads legada não encontrada.")


def validate_required_tables(con: sqlite3.Connection, required: set[str], label: str) -> None:
    missing = sorted(required - table_names(con))
    if missing:
        raise CriticalMigrationError(f"{label}: tabelas obrigatórias ausentes: {', '.join(missing)}")


def find_by_normalized(con: sqlite3.Connection, table: str, column: str, value: str) -> sqlite3.Row | None:
    for row in con.execute(f'SELECT * FROM "{table}"'):
        if normalize_key(row[column]) == normalize_key(value):
            return row
    return None


def get_or_create_status(con: sqlite3.Connection, name: str, report: MigrationReport) -> str:
    row = find_by_normalized(con, "status_incidente", "status", name)
    if row:
        return row["status"]
    insert_row(con, "status_incidente", {"status": name, "desc_status": ""})
    report.statuses_created += 1
    return name


def get_or_create_type(con: sqlite3.Connection, name: str, report: MigrationReport) -> str:
    row = find_by_normalized(con, "tipo_incidente", "tipo_incidente", name)
    if row:
        return row["tipo_incidente"]
    insert_row(con, "tipo_incidente", {"tipo_incidente": name, "desc_incidente": ""})
    report.types_created += 1
    return name


def get_or_create_command(con: sqlite3.Connection, cpa: str, report: MigrationReport) -> int:
    row = find_by_normalized(con, "organizational_commands", "name", cpa)
    if row:
        return int(row["id"])
    sort_order = con.execute("SELECT COALESCE(MAX(sort_order), 0) + 1 FROM organizational_commands").fetchone()[0]
    command_id = insert_row(
        con,
        "organizational_commands",
        {"name": cpa, "active": 1, "sort_order": sort_order, "created_at": now_sql(), "updated_at": now_sql()},
    )
    report.commands_created += 1
    report.relationship_corrections += 1
    return command_id


def get_or_create_unit(con: sqlite3.Connection, command_id: int, cpa: str, unit: str, report: MigrationReport) -> int:
    normalized = normalize_text(unit, preserve_newlines=False).casefold()
    row = con.execute(
        "SELECT id FROM organizational_units WHERE command_id = ? AND normalized_name = ?",
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
            "name": unit,
            "normalized_name": normalized,
            "active": 1,
            "sort_order": sort_order,
            "created_at": now_sql(),
            "updated_at": now_sql(),
        },
    )
    if not con.execute("SELECT 1 FROM unidades WHERE cpa = ? AND btl = ?", (cpa, unit)).fetchone():
        insert_row(con, "unidades", {"cpa": cpa, "btl": unit})
    report.units_created += 1
    report.relationship_corrections += 1
    return unit_id


def incident_identity(row: sqlite3.Row, normalized: dict[str, Any]) -> tuple[str, ...]:
    return (
        normalize_key(normalized["incident_type"]),
        normalize_key(normalized["report_number"]),
        normalize_key(normalized["ticket_number"]),
        normalized["start_date"] or "",
        normalize_key(normalized["cpa"]),
        normalize_key(normalized["btl"]),
        hashlib.sha256((normalized["description_plain_text"] or "").encode("utf-8")).hexdigest(),
    )


def existing_incident_indexes(con: sqlite3.Connection) -> tuple[dict[tuple[str, ...], int], dict[tuple[str, ...], list[int]]]:
    strong: dict[tuple[str, ...], int] = {}
    loose: dict[tuple[str, ...], list[int]] = {}
    for row in con.execute("SELECT * FROM incidente WHERE deleted_at IS NULL OR deleted_at IS NULL"):
        description = row["description_plain_text"] or plain_text_from_html(row["description"] or "")
        normalized = {
            "incident_type": normalize_text(row["incident_type"], preserve_newlines=False),
            "report_number": normalize_text(row["report_number"], preserve_newlines=False),
            "ticket_number": normalize_text(row["ticket_number"], preserve_newlines=False),
            "start_date": normalize_datetime(row["start_date"], MigrationReport("index", "index", ""), "index", required=False),
            "cpa": normalize_cpa(row["cpa"]),
            "btl": normalize_unit(row["btl"], normalize_cpa(row["cpa"])),
            "description_plain_text": normalize_text(description, preserve_newlines=False),
        }
        key = incident_identity(row, normalized)
        strong[key] = int(row["id"])
        loose_key = (
            normalize_key(normalized["incident_type"]),
            normalize_key(normalized["report_number"]),
            normalize_key(normalized["ticket_number"]),
            (normalized["start_date"] or "")[:10],
        )
        loose.setdefault(loose_key, []).append(int(row["id"]))
    return strong, loose


def normalize_incident(row: sqlite3.Row, dst: sqlite3.Connection, report: MigrationReport) -> dict[str, Any] | None:
    start_date = normalize_datetime(row["start_date"], report, f"incidente {row['id']} start_date")
    if not start_date:
        report.skipped_legacy_ids.append(int(row["id"]))
        return None
    end_date = normalize_datetime(row["end_date"], report, f"incidente {row['id']} end_date", required=False)
    raw_values = [row["incident_type"], row["report_number"], row["ticket_number"], row["cpa"], row["btl"], row["cia"], row["description"], row["status_incident"]]
    repaired_values = [_repair_mojibake(str(value))[1] for value in raw_values if value is not None]
    report.encoding_corrections += sum(1 for repaired in repaired_values if repaired)
    cpa = normalize_cpa(row["cpa"])
    btl = normalize_unit(row["btl"], cpa)
    if not cpa or not btl:
        report.add_issue("error", "relacionamento", "CPA/Batalhão vazio após normalização.", legacy_id=row["id"])
        report.skipped_legacy_ids.append(int(row["id"]))
        return None
    status = get_or_create_status(dst, normalize_text(row["status_incident"], preserve_newlines=False, max_length=50), report)
    incident_type = get_or_create_type(dst, normalize_text(row["incident_type"], preserve_newlines=False, max_length=100), report)
    command_id = get_or_create_command(dst, cpa, report)
    unit_id = get_or_create_unit(dst, command_id, cpa, btl, report)
    description, plain = safe_description_html(row["description"] or "")
    return {
        "legacy_id": int(row["id"]),
        "incident_type": incident_type or "Não informado",
        "report_number": normalize_text(row["report_number"], preserve_newlines=False, max_length=50) or f"LEGADO-{row['id']}",
        "ticket_number": normalize_text(row["ticket_number"], preserve_newlines=False, max_length=50),
        "message_number": None,
        "cpa": cpa,
        "btl": btl,
        "cia": normalize_text(row["cia"], preserve_newlines=False, max_length=100),
        "description": description,
        "description_plain_text": plain,
        "start_date": start_date,
        "end_date": end_date,
        "status_incident": status or "Em Análise",
        "command_id": command_id,
        "unit_id": unit_id,
        "legacy_user_id": int(row["user_id"]) if row["user_id"] is not None else None,
    }


def migrate_users(src: sqlite3.Connection, dst: sqlite3.Connection, report: MigrationReport) -> dict[int, int]:
    user_map: dict[int, int] = {}
    fallback = dst.execute(
        "SELECT id FROM user WHERE profile = 'Admin' AND is_active = 1 ORDER BY id LIMIT 1"
    ).fetchone() or dst.execute("SELECT id FROM user WHERE is_active = 1 ORDER BY id LIMIT 1").fetchone()
    if not fallback:
        raise CriticalMigrationError("Nenhum usuário ativo encontrado no banco atual para associar a migração.")
    fallback_id = int(fallback["id"])
    for user in src.execute("SELECT id, username, email FROM user ORDER BY id"):
        username = normalize_text(user["username"], preserve_newlines=False, max_length=50)
        email = normalize_text(user["email"], preserve_newlines=False, max_length=255)
        existing = None
        if username:
            existing = dst.execute("SELECT id FROM user WHERE lower(username) = lower(?) LIMIT 1", (username,)).fetchone()
        if not existing and email:
            existing = dst.execute("SELECT id FROM user WHERE lower(email) = lower(?) LIMIT 1", (email,)).fetchone()
        user_map[int(user["id"])] = int(existing["id"]) if existing else fallback_id
    return user_map


def migrate_library(src: sqlite3.Connection, dst: sqlite3.Connection, report: MigrationReport) -> None:
    for row in src.execute("SELECT status FROM status_incidente"):
        name = normalize_text(row["status"], preserve_newlines=False, max_length=50)
        if name:
            get_or_create_status(dst, name, report)
    for row in src.execute("SELECT tipo_incidente FROM tipo_incidente"):
        name = normalize_text(row["tipo_incidente"], preserve_newlines=False, max_length=100)
        if name:
            get_or_create_type(dst, name, report)
    pairs: set[tuple[str, str]] = set()
    for row in src.execute("SELECT cpa, btl FROM unidades UNION SELECT cpa, btl FROM incidente"):
        cpa = normalize_cpa(row["cpa"])
        unit = normalize_unit(row["btl"], cpa)
        if cpa and unit:
            pairs.add((cpa, unit))
    for cpa, unit in sorted(pairs):
        command_id = get_or_create_command(dst, cpa, report)
        get_or_create_unit(dst, command_id, cpa, unit, report)


def migrate_incidents(src: sqlite3.Connection, dst: sqlite3.Connection, report: MigrationReport, user_map: dict[int, int]) -> dict[int, int]:
    strong_index, loose_index = existing_incident_indexes(dst)
    legacy_loose_counts: dict[tuple[str, ...], int] = {}
    legacy_rows = list(src.execute("SELECT * FROM incidente ORDER BY id"))
    for row in legacy_rows:
        start_date = normalize_datetime(row["start_date"], report, f"incidente {row['id']} start_date", required=False) or ""
        legacy_loose_key = (
            normalize_key(row["incident_type"]),
            normalize_key(row["report_number"]),
            normalize_key(row["ticket_number"]),
            start_date[:10],
        )
        legacy_loose_counts[legacy_loose_key] = legacy_loose_counts.get(legacy_loose_key, 0) + 1
    incident_map: dict[int, int] = {}
    for row in legacy_rows:
        report.incidents_analyzed += 1
        normalized = normalize_incident(row, dst, report)
        if not normalized:
            continue
        key = incident_identity(row, normalized)
        existing_id = strong_index.get(key)
        loose_key = (
            normalize_key(normalized["incident_type"]),
            normalize_key(normalized["report_number"]),
            normalize_key(normalized["ticket_number"]),
            normalized["start_date"][:10],
        )
        if (
            existing_id is None
            and legacy_loose_counts.get(loose_key) == 1
            and len(loose_index.get(loose_key, [])) == 1
        ):
            existing_id = loose_index[loose_key][0]
            report.incidents_conflicts += 1
            report.add_issue(
                "warning",
                "duplicidade",
                "Incidente correspondente encontrado por chave parcial; conteúdo atual preservado.",
                legacy_id=normalized["legacy_id"],
                current_id=existing_id,
            )
        values = {
            "message_number": normalized["message_number"],
            "incident_type": normalized["incident_type"],
            "report_number": normalized["report_number"],
            "ticket_number": normalized["ticket_number"],
            "cpa": normalized["cpa"],
            "btl": normalized["btl"],
            "cia": normalized["cia"],
            "description": normalized["description"],
            "description_plain_text": normalized["description_plain_text"],
            "start_date": normalized["start_date"],
            "end_date": normalized["end_date"],
            "status_incident": normalized["status_incident"],
            "command_id": normalized["command_id"],
            "unit_id": normalized["unit_id"],
            "user_id": user_map.get(normalized["legacy_user_id"]) or next(iter(user_map.values())),
            "created_at": normalized["start_date"],
            "updated_at": now_sql(),
        }
        if existing_id:
            current = dst.execute("SELECT * FROM incidente WHERE id = ?", (existing_id,)).fetchone()
            fill_values = {
                "description_plain_text": values["description_plain_text"] if not current["description_plain_text"] else current["description_plain_text"],
                "command_id": values["command_id"] if not current["command_id"] else current["command_id"],
                "unit_id": values["unit_id"] if not current["unit_id"] else current["unit_id"],
            }
            if any(fill_values[column] != current[column] for column in fill_values):
                update_row(dst, "incidente", existing_id, fill_values | {"updated_at": now_sql()})
                report.incidents_updated += 1
                report.updated_current_ids.append(existing_id)
            else:
                report.incidents_skipped_duplicate += 1
                report.skipped_legacy_ids.append(normalized["legacy_id"])
            incident_map[normalized["legacy_id"]] = existing_id
            continue
        incident_id = insert_row(dst, "incidente", values)
        strong_index[key] = incident_id
        loose_index.setdefault(loose_key, []).append(incident_id)
        incident_map[normalized["legacy_id"]] = incident_id
        report.incidents_inserted += 1
        report.migrated_legacy_ids.append(normalized["legacy_id"])
    return incident_map


def migrate_observations(src: sqlite3.Connection, dst: sqlite3.Connection, report: MigrationReport, user_map: dict[int, int], incident_map: dict[int, int]) -> None:
    for row in src.execute("SELECT * FROM incidente_obs ORDER BY id"):
        incident_id = incident_map.get(int(row["incidente_id"]))
        if not incident_id:
            report.add_issue("warning", "observacao_orfa", "Observação sem incidente correspondente.", legacy_obs_id=row["id"])
            continue
        observed_at = normalize_datetime(row["data_observacao"], report, f"observacao {row['id']} data_observacao")
        if not observed_at:
            continue
        text = normalize_text(row["texto_observacao"], preserve_newlines=True, max_length=MAX_TEXT_LENGTH)
        user_id = user_map.get(int(row["usuario_id"])) or next(iter(user_map.values()))
        existing = dst.execute(
            """
            SELECT id FROM incidente_obs
            WHERE incidente_id = ? AND usuario_id = ? AND data_observacao = ? AND texto_observacao = ?
            LIMIT 1
            """,
            (incident_id, user_id, observed_at, text),
        ).fetchone()
        if existing:
            report.observations_skipped_duplicate += 1
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
        report.observations_inserted += 1


def validate_attachment_name(path: Path) -> None:
    name = path.name
    if not name or name in {".", ".."}:
        raise ValueError("nome vazio")
    if any(sep in name for sep in ("/", "\\")) or ".." in Path(name).parts:
        raise ValueError("path traversal")
    if any(ord(char) < 32 for char in name):
        raise ValueError("caractere de controle")
    stem = path.stem.rstrip(" .").casefold()
    if stem in RESERVED_WINDOWS_NAMES:
        raise ValueError("nome reservado")
    suffixes = [suffix.lower() for suffix in path.suffixes]
    if not suffixes or suffixes[-1] not in ALLOWED_ATTACHMENT_EXTENSIONS:
        raise ValueError("extensão não permitida")
    if any(suffix in BLOCKED_ATTACHMENT_SUFFIXES for suffix in suffixes):
        raise ValueError("extensão bloqueada")


def validate_attachment_content(path: Path) -> tuple[str, int, str]:
    validate_attachment_name(path)
    size = path.stat().st_size
    if size <= 0:
        raise ValueError("arquivo vazio")
    if size > MAX_ATTACHMENT_SIZE:
        raise ValueError("arquivo acima do limite")
    extension = path.suffix.lower()
    head = path.read_bytes()[:8192]
    signatures = SIGNATURES.get(extension, ())
    if signatures and not any(head.startswith(signature) for signature in signatures):
        raise ValueError("assinatura incompatível")
    if extension == ".webp" and head[8:12] != b"WEBP":
        raise ValueError("assinatura incompatível")
    return MIME_BY_EXTENSION.get(extension, mimetypes.guess_type(path.name)[0] or "application/octet-stream"), size, sha256_file(path)


def incident_id_from_folder(path: Path) -> int | None:
    match = re.fullmatch(r"\d+", path.name.strip())
    return int(path.name) if match else None


def migrate_attachments(src_upload_root: Path, dst_upload_root: Path, dst: sqlite3.Connection, report: MigrationReport, incident_map: dict[int, int], uploaded_by_id: int, *, dry_run: bool) -> None:
    dst_upload_root.mkdir(parents=True, exist_ok=True)
    for file_path in sorted(src_upload_root.rglob("*")):
        if not file_path.is_file():
            continue
        report.attachments_analyzed += 1
        rel = file_path.relative_to(src_upload_root).as_posix()
        parts = file_path.relative_to(src_upload_root).parts
        if not parts:
            continue
        legacy_incident_id = incident_id_from_folder(Path(parts[0]))
        if legacy_incident_id is None:
            report.attachments_orphan += 1
            report.quarantined.append({"relative_path": rel, "reason": "fora de pasta numerada"})
            continue
        current_incident_id = incident_map.get(legacy_incident_id)
        if not current_incident_id:
            report.attachments_orphan += 1
            report.quarantined.append({"relative_path": rel, "reason": "incidente inexistente"})
            continue
        try:
            mime_type, file_size, digest = validate_attachment_content(file_path)
        except ValueError as exc:
            report.attachments_invalid += 1
            report.quarantined.append({"relative_path": rel, "reason": str(exc)})
            continue
        existing = dst.execute(
            "SELECT id FROM incident_attachments WHERE incident_id = ? AND sha256 = ? LIMIT 1",
            (current_incident_id, digest),
        ).fetchone()
        if existing:
            report.attachments_skipped_duplicate += 1
            continue
        stored_filename = f"{uuid4().hex}{file_path.suffix.lower()}"
        if not dry_run:
            destination = (dst_upload_root / stored_filename).resolve()
            if dst_upload_root.resolve() not in destination.parents:
                raise CriticalMigrationError("Destino de anexo fora da pasta autorizada.")
            shutil.copy2(file_path, destination)
            report.current_files_created.append(display_path(destination))
        insert_row(
            dst,
            "incident_attachments",
            {
                "incident_id": current_incident_id,
                "original_filename": normalize_text(file_path.name, preserve_newlines=False, max_length=255),
                "stored_filename": stored_filename,
                "mime_type": mime_type,
                "file_size": file_size,
                "sha256": digest,
                "uploaded_by_id": uploaded_by_id,
                "uploaded_at": now_sql(),
                "created_at": now_sql(),
                "updated_at": now_sql(),
            },
        )
        report.attachments_imported += 1


def register_migration_audit(dst: sqlite3.Connection, report: MigrationReport) -> None:
    if "audit_logs" not in table_names(dst):
        report.add_issue("warning", "auditoria", "Tabela audit_logs ausente; auditoria da migração não gravada.")
        return
    insert_row(
        dst,
        "audit_logs",
        {
            "timestamp": now_sql(),
            "usuario_id": None,
            "usuario_identificacao": "script:migrate_legacy_incident_backup",
            "acao": "MIGRAR_LEGADO_INCIDENTES",
            "modulo": "Migração legado",
            "entidade": "Incidente",
            "entidade_id": report.run_id,
            "descricao": "Migração de incidentes legados executada por rotina administrativa.",
            "alteracoes": json.dumps(
                {
                    "origem": "2026-08-03",
                    "incidentes_analisados": report.incidents_analyzed,
                    "incidentes_inseridos": report.incidents_inserted,
                    "incidentes_atualizados": report.incidents_updated,
                    "incidentes_ignorados": report.incidents_skipped_duplicate,
                    "anexos_importados": report.attachments_imported,
                    "anexos_orfaos": report.attachments_orphan,
                    "anexos_invalidos": report.attachments_invalid,
                    "run_id": report.run_id,
                },
                ensure_ascii=False,
            ),
            "ip_address": None,
            "user_agent": None,
            "endpoint": "scripts/migrate_legacy_incident_backup.py",
            "metodo_http": "CLI",
            "resultado": "SUCESSO",
            "request_id": report.run_id.replace("-", "")[:64],
        },
    )


def copy_tree_backup(source: Path, target: Path) -> None:
    if target.exists():
        raise CriticalMigrationError(f"Backup de destino já existe: {target}")
    if source.exists():
        shutil.copytree(source, target)
    else:
        target.mkdir(parents=True)


def backup_current_state(current_db: Path, upload_root: Path, backup_root: Path, report: MigrationReport) -> Path:
    backup_dir = backup_root / report.run_id
    backup_dir.mkdir(parents=True, exist_ok=True)
    db_backup = backup_dir / current_db.name
    shutil.copy2(current_db, db_backup)
    with closing(connect_sqlite(db_backup, readonly=True)) as con:
        if con.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
            raise CriticalMigrationError("Backup do banco atual falhou no integrity_check.")
    uploads_backup = backup_dir / "uploads"
    copy_tree_backup(upload_root, uploads_backup)
    manifest = {
        "run_id": report.run_id,
        "created_at": now_sql(),
        "current_db": str(current_db),
        "upload_root": str(upload_root),
        "db_backup": str(db_backup),
        "uploads_backup": str(uploads_backup),
        "db_sha256": sha256_file(db_backup),
        "uploads_sha256": directory_checksums(uploads_backup),
    }
    manifest_path = backup_dir / "rollback_manifest.json"
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    report.backups = {
        "backup_dir": str(backup_dir),
        "db_backup": str(db_backup),
        "uploads_backup": str(uploads_backup),
        "rollback_manifest": str(manifest_path),
    }
    report.rollback = {"command": f"python scripts/migrate_legacy_incident_backup.py --rollback {report.run_id}"}
    return backup_dir


def restore_backup(run: str, backup_root: Path) -> None:
    backup_dir = backup_root / run
    manifest_path = backup_dir / "rollback_manifest.json"
    if not manifest_path.exists():
        raise CriticalMigrationError(f"Manifesto de rollback não encontrado para {run}.")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    current_db = Path(manifest["current_db"])
    upload_root = Path(manifest["upload_root"])
    db_backup = Path(manifest["db_backup"])
    uploads_backup = Path(manifest["uploads_backup"])
    restore_safety = backup_dir / f"pre_rollback_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
    restore_safety.mkdir(parents=True, exist_ok=True)
    if current_db.exists():
        shutil.copy2(current_db, restore_safety / current_db.name)
    if upload_root.exists():
        shutil.copytree(upload_root, restore_safety / "uploads_current")
        shutil.rmtree(upload_root)
    shutil.copy2(db_backup, current_db)
    shutil.copytree(uploads_backup, upload_root)
    print(f"Rollback concluído para run_id={run}. Cópia pré-rollback: {restore_safety}")


def write_reports(report: MigrationReport, reports_dir: Path) -> None:
    reports_dir.mkdir(parents=True, exist_ok=True)
    json_path = reports_dir / f"legacy_incident_migration_{report.run_id}.json"
    txt_path = reports_dir / f"legacy_incident_migration_{report.run_id}.txt"
    data = report.as_dict()
    json_path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    lines = [
        f"Migração legado DivCiber - {report.run_id}",
        f"Modo: {report.mode}",
        f"Origem: {report.source_root}",
        f"Banco legado: {report.source_db}",
        f"Banco atual: {report.current_db}",
        "",
        f"Incidentes analisados: {report.incidents_analyzed}",
        f"Incidentes inseridos: {report.incidents_inserted}",
        f"Incidentes atualizados tecnicamente: {report.incidents_updated}",
        f"Incidentes ignorados por duplicidade: {report.incidents_skipped_duplicate}",
        f"Conflitos/alertas de correspondência: {report.incidents_conflicts}",
        f"Observações inseridas: {report.observations_inserted}",
        f"Observações duplicadas ignoradas: {report.observations_skipped_duplicate}",
        f"Anexos analisados: {report.attachments_analyzed}",
        f"Anexos importados: {report.attachments_imported}",
        f"Anexos duplicados ignorados: {report.attachments_skipped_duplicate}",
        f"Anexos órfãos: {report.attachments_orphan}",
        f"Anexos inválidos: {report.attachments_invalid}",
        f"CPAs criados: {report.commands_created}",
        f"Batalhões/Unidades criados: {report.units_created}",
        f"Status criados: {report.statuses_created}",
        f"Tipos criados: {report.types_created}",
        "",
        "Issues:",
    ]
    lines.extend(f"- {issue.severity.upper()} {issue.category}: {issue.message}" for issue in report.issues[:200])
    if len(report.issues) > 200:
        lines.append(f"- ... {len(report.issues) - 200} issues adicionais no JSON.")
    txt_path.write_text("\n".join(lines), encoding="utf-8")
    report.reports = {"json": str(json_path), "text": str(txt_path)}
    json_path.write_text(json.dumps(report.as_dict(), ensure_ascii=False, indent=2), encoding="utf-8")


def field_mapping() -> dict[str, str]:
    return {
        "incidente.id": "chave legada usada para associar pasta uploads/incidentes/<id>",
        "incidente.incident_type": "incidente.incident_type",
        "incidente.report_number": "incidente.report_number",
        "incidente.ticket_number": "incidente.ticket_number",
        "incidente.cpa": "incidente.cpa + organizational_commands.name",
        "incidente.btl": "incidente.btl + organizational_units.name",
        "incidente.cia": "incidente.cia",
        "incidente.description": "incidente.description e incidente.description_plain_text",
        "incidente.start_date": "incidente.start_date",
        "incidente.end_date": "incidente.end_date",
        "incidente.status_incident": "incidente.status_incident + status_incidente.status",
        "incidente.user_id": "usuário atual por username/e-mail ou administrador ativo fallback",
        "incidente_obs.*": "incidente_obs.* com novo incidente_id mapeado",
        "uploads/incidentes/<id>/<arquivo>": "incident_attachments + arquivo seguro em instance/uploads/incidents",
    }


def run_package_migration(
    *,
    source_root: Path,
    current_db: Path,
    upload_root: Path,
    reports_dir: Path,
    backup_root: Path,
    mode: str,
    report_only: bool = False,
) -> MigrationReport:
    source_root = source_root.resolve()
    current_db = current_db.resolve()
    upload_root = upload_root.resolve()
    reports_dir = reports_dir.resolve()
    backup_root = backup_root.resolve()
    report = MigrationReport(run_id=run_id(), mode=mode, source_root=str(source_root))
    try:
        if not source_root.exists():
            raise CriticalMigrationError(f"Pasta de origem não encontrada: {source_root}")
        reject_reparse_points(source_root, report)
        if report.has_critical_errors():
            raise CriticalMigrationError("Origem contém links simbólicos/junctions proibidos.")
        legacy_db = find_legacy_database(source_root, report)
        legacy_upload_root = find_legacy_upload_root(source_root)
        report.source_db = str(legacy_db)
        report.current_db = str(current_db)
        report.field_mapping = field_mapping()
        report.checksums_before = {
            "legacy_db_sha256": sha256_file(legacy_db),
            "current_db_sha256": sha256_file(current_db) if current_db.exists() else None,
            "current_uploads": directory_checksums(upload_root),
        }
        with closing(connect_sqlite(legacy_db, readonly=True)) as src, closing(connect_sqlite(current_db, readonly=report_only)) as dst:
            validate_required_tables(src, REQUIRED_LEGACY_TABLES, "Banco legado")
            validate_required_tables(dst, REQUIRED_CURRENT_TABLES, "Banco atual")
            report.schema_legacy = inspect_schema(src)
            report.schema_current = inspect_schema(dst)
            report.table_counts_legacy = collect_counts(src)
            report.table_counts_current_before = collect_counts(dst)
            if report_only:
                report.finish()
                write_reports(report, reports_dir)
                return report
            dry_run = mode != "apply"
            if not dry_run:
                backup_current_state(current_db, upload_root, backup_root, report)
            dst.execute("BEGIN IMMEDIATE")
            try:
                migrate_library(src, dst, report)
                user_map = migrate_users(src, dst, report)
                incident_map = migrate_incidents(src, dst, report, user_map)
                migrate_observations(src, dst, report, user_map, incident_map)
                uploaded_by_id = next(iter(user_map.values()))
                migrate_attachments(legacy_upload_root, upload_root, dst, report, incident_map, uploaded_by_id, dry_run=dry_run)
                if not dry_run:
                    register_migration_audit(dst, report)
                report.integrity_check = [tuple(row) for row in dst.execute("PRAGMA integrity_check").fetchall()]
                report.foreign_key_check = [tuple(row) for row in dst.execute("PRAGMA foreign_key_check").fetchall()]
                if report.integrity_check != [("ok",)] or report.foreign_key_check:
                    raise CriticalMigrationError("Validação de integridade falhou após migração.")
                report.table_counts_current_after = collect_counts(dst)
                if dry_run:
                    dst.rollback()
                else:
                    dst.commit()
            except Exception:
                dst.rollback()
                if not dry_run:
                    for rel in report.current_files_created:
                        candidate = (repo_root() / rel).resolve()
                        try:
                            if candidate.exists() and upload_root in candidate.parents:
                                candidate.unlink()
                        except OSError:
                            pass
                raise
        if mode == "apply":
            report.checksums_after = {
                "current_db_sha256": sha256_file(current_db),
                "current_uploads": directory_checksums(upload_root),
            }
        report.finish()
        write_reports(report, reports_dir)
        return report
    except Exception as exc:
        report.add_issue("critical", "execucao", "Migração abortada com rollback.", error=type(exc).__name__)
        report.finish()
        write_reports(report, reports_dir)
        raise


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Migra pacote legado de incidentes DivCiber.")
    parser.add_argument("--source", type=Path, default=Path(os.getenv("DIVCIBER_LEGACY_BACKUP_DIR", default_source_root())))
    parser.add_argument("--current-db", type=Path, default=repo_root() / "instance" / "divciber.db")
    parser.add_argument("--upload-root", type=Path, default=repo_root() / "instance" / "uploads" / "incidents")
    parser.add_argument("--reports-dir", type=Path, default=repo_root() / "instance" / "migration_reports")
    parser.add_argument("--backup-root", type=Path, default=repo_root() / "instance" / "migration_backups")
    parser.add_argument("--analyze-only", action="store_true", help="Inspeciona schemas e origem sem simular gravação.")
    parser.add_argument("--dry-run", action="store_true", help="Simula a migração e desfaz tudo ao final.")
    parser.add_argument("--apply", action="store_true", help="Executa a migração efetiva.")
    parser.add_argument("--rollback", metavar="RUN_ID", help="Restaura banco/uploads a partir de um backup de execução.")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv or sys.argv[1:])
    try:
        if args.rollback:
            restore_backup(args.rollback, args.backup_root.resolve())
            return 0
        selected_modes = sum(bool(flag) for flag in (args.analyze_only, args.dry_run, args.apply))
        if selected_modes > 1:
            print("ERRO: selecione apenas um modo entre --analyze-only, --dry-run e --apply.", file=sys.stderr)
            return 2
        mode = "apply" if args.apply else "analyze" if args.analyze_only else "dry-run"
        report = run_package_migration(
            source_root=args.source,
            current_db=args.current_db,
            upload_root=args.upload_root,
            reports_dir=args.reports_dir,
            backup_root=args.backup_root,
            mode=mode,
            report_only=args.analyze_only,
        )
    except Exception as exc:
        print(f"ERRO: {exc}", file=sys.stderr)
        return 1
    summary = {
        "run_id": report.run_id,
        "mode": report.mode,
        "incidentes_analisados": report.incidents_analyzed,
        "incidentes_inseridos": report.incidents_inserted,
        "incidentes_atualizados": report.incidents_updated,
        "anexos_importados": report.attachments_imported,
        "anexos_orfaos": report.attachments_orphan,
        "anexos_invalidos": report.attachments_invalid,
        "issues": len(report.issues),
        "critical": sum(1 for issue in report.issues if issue.severity == "critical"),
        "reports": report.reports,
        "rollback": report.rollback,
    }
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
