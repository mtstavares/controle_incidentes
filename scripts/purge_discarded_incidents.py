#!/usr/bin/env python3
"""Remove incidentes descartados por deduplicacao.

Esta rotina apaga definitivamente apenas incidentes com deleted_at preenchido.
Antes do apply, cria backup do SQLite e emite relatorio JSON da operacao.
"""

from __future__ import annotations

import argparse
import json
import shutil
import sqlite3
from datetime import datetime
from pathlib import Path


def _project_root() -> Path:
    return Path(__file__).resolve().parents[1]


def _timestamp() -> str:
    return datetime.now().strftime("%Y%m%d-%H%M%S")


def _connect(db_path: Path) -> sqlite3.Connection:
    con = sqlite3.connect(db_path)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA foreign_keys = ON")
    return con


def _count(con: sqlite3.Connection, sql: str) -> int:
    return int(con.execute(sql).fetchone()[0])


def _find_attachment_files(upload_root: Path, stored_filenames: list[str]) -> dict[str, list[str]]:
    found: dict[str, list[str]] = {}
    if not upload_root.exists():
        return found

    for stored_filename in stored_filenames:
        matches = [
            str(path.relative_to(upload_root))
            for path in upload_root.rglob(stored_filename)
            if path.is_file()
        ]
        if matches:
            found[stored_filename] = matches
    return found


def _move_attachment_files(
    upload_root: Path,
    quarantine_root: Path,
    found_files: dict[str, list[str]],
) -> list[str]:
    moved: list[str] = []
    upload_root = upload_root.resolve()
    quarantine_root.mkdir(parents=True, exist_ok=True)

    for paths in found_files.values():
        for relative_name in paths:
            source = (upload_root / relative_name).resolve()
            if not source.is_file() or not source.is_relative_to(upload_root):
                continue
            target = quarantine_root / relative_name
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.move(str(source), str(target))
            moved.append(relative_name)
    return moved


def build_report(db_path: Path, upload_root: Path) -> dict:
    with _connect(db_path) as con:
        attachments = con.execute(
            """
            SELECT id, incident_id, original_filename, stored_filename
              FROM incident_attachments
             WHERE incident_id IN (
                   SELECT id FROM incidente WHERE deleted_at IS NOT NULL
             )
             ORDER BY incident_id, id
            """
        ).fetchall()
        stored_filenames = [row["stored_filename"] for row in attachments]

        return {
            "db_path": str(db_path),
            "upload_root": str(upload_root),
            "incidentes_total_antes": _count(con, "SELECT count(*) FROM incidente"),
            "incidentes_ativos_antes": _count(
                con, "SELECT count(*) FROM incidente WHERE deleted_at IS NULL"
            ),
            "incidentes_descartados": _count(
                con, "SELECT count(*) FROM incidente WHERE deleted_at IS NOT NULL"
            ),
            "observacoes_descartadas": _count(
                con,
                """
                SELECT count(*) FROM incidente_obs
                 WHERE incidente_id IN (
                       SELECT id FROM incidente WHERE deleted_at IS NOT NULL
                 )
                """,
            ),
            "anexos_descartados": len(attachments),
            "arquivos_anexos_encontrados": _find_attachment_files(upload_root, stored_filenames),
            "foreign_key_check_antes": [tuple(row) for row in con.execute("PRAGMA foreign_key_check")],
        }


def purge(db_path: Path, upload_root: Path, report_dir: Path, dry_run: bool) -> dict:
    db_path = db_path.resolve()
    upload_root = upload_root.resolve()
    report_dir.mkdir(parents=True, exist_ok=True)

    run_id = f"purge-discarded-{_timestamp()}"
    report = build_report(db_path, upload_root)
    report["run_id"] = run_id
    report["mode"] = "dry-run" if dry_run else "apply"
    report["backup"] = None
    report["arquivos_anexos_movidos_para_quarentena"] = []

    if report["foreign_key_check_antes"]:
        report["critical"] = "foreign_key_check falhou antes da purga"
        _write_report(report_dir, run_id, report)
        raise SystemExit(2)

    if dry_run:
        _write_report(report_dir, run_id, report)
        return report

    backup_path = db_path.with_name(f"{db_path.stem}-pre-purge-discarded-{_timestamp()}{db_path.suffix}.backup")
    shutil.copy2(db_path, backup_path)
    report["backup"] = str(backup_path)

    quarantine_root = upload_root.parent / "quarantine" / run_id
    report["arquivos_anexos_movidos_para_quarentena"] = _move_attachment_files(
        upload_root,
        quarantine_root,
        report["arquivos_anexos_encontrados"],
    )

    with _connect(db_path) as con:
        try:
            con.execute("BEGIN")
            con.execute(
                """
                DELETE FROM incident_attachments
                 WHERE incident_id IN (
                       SELECT id FROM incidente WHERE deleted_at IS NOT NULL
                 )
                """
            )
            con.execute(
                """
                DELETE FROM incidente_obs
                 WHERE incidente_id IN (
                       SELECT id FROM incidente WHERE deleted_at IS NOT NULL
                 )
                """
            )
            con.execute("DELETE FROM incidente WHERE deleted_at IS NOT NULL")
            con.commit()
        except Exception:
            con.rollback()
            shutil.copy2(backup_path, db_path)
            raise

        report["incidentes_total_depois"] = _count(con, "SELECT count(*) FROM incidente")
        report["incidentes_ativos_depois"] = _count(
            con, "SELECT count(*) FROM incidente WHERE deleted_at IS NULL"
        )
        report["incidentes_descartados_depois"] = _count(
            con, "SELECT count(*) FROM incidente WHERE deleted_at IS NOT NULL"
        )
        report["foreign_key_check_depois"] = [
            tuple(row) for row in con.execute("PRAGMA foreign_key_check")
        ]
        report["integrity_check_depois"] = con.execute("PRAGMA integrity_check").fetchone()[0]

    _write_report(report_dir, run_id, report)
    return report


def _write_report(report_dir: Path, run_id: str, report: dict) -> None:
    path = report_dir / f"{run_id}.json"
    path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    report["report_path"] = str(path)


def main() -> None:
    root = _project_root()
    parser = argparse.ArgumentParser(description="Purge incidentes descartados por deleted_at.")
    parser.add_argument("--db", default=str(root / "instance" / "divciber.db"))
    parser.add_argument("--upload-root", default=str(root / "instance" / "uploads"))
    parser.add_argument("--report-dir", default=str(root / "instance" / "duplicate_scans"))
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--dry-run", action="store_true")
    mode.add_argument("--apply", action="store_true")
    args = parser.parse_args()

    report = purge(
        Path(args.db),
        Path(args.upload_root),
        Path(args.report_dir),
        dry_run=args.dry_run,
    )
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
