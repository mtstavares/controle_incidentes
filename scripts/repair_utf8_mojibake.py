"""Repair known UTF-8 mojibake sequences in persisted text fields.

The script is idempotent and intentionally conservative: it only rewrites
recognized mojibake sequences such as "ç" and "ã". It never replaces plain
"?" characters because they may be legitimate data.

Usage:
    flask shell
    >>> exec(open("scripts/repair_utf8_mojibake.py", encoding="utf-8").read())
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

from app import db
from app.models import AuditLog, Incidente, IncidenteObs, StatusIncidente, TipoIncidente, Unidades, User


REPLACEMENTS = {
    "\u00c3\u00a7": "ç",
    "\u00c3\u0087": "Ç",
    "\u00c3\u00a3": "ã",
    "\u00c3\u00b5": "õ",
    "\u00c3\u00a1": "á",
    "\u00c3\u00a9": "é",
    "\u00c3\u00aa": "ê",
    "\u00c3\u00ad": "í",
    "\u00c3\u00b3": "ó",
    "\u00c3\u00ba": "ú",
    "\u00c3\u00a0": "à",
    "\u00c3\u00a2": "â",
    "\u00c3\u00b4": "ô",
    "\u00c2\u00ba": "º",
    "\u00c2\u00aa": "ª",
}


TEXT_FIELDS = {
    User: ("username", "name", "email", "profile"),
    Incidente: ("incident_type", "report_number", "message_number", "ticket_number", "cpa", "btl", "cia", "description", "description_plain_text", "status_incident"),
    IncidenteObs: ("texto_observacao",),
    Unidades: ("cpa", "btl"),
    TipoIncidente: ("tipo_incidente", "desc_incidente"),
    StatusIncidente: ("status", "desc_status"),
    AuditLog: ("usuario_identificacao", "acao", "modulo", "entidade", "entidade_id", "descricao", "ip_address", "user_agent", "endpoint", "metodo_http", "resultado", "request_id"),
}


def repair_text(value):
    if not isinstance(value, str):
        return value
    repaired = value
    for old, new in REPLACEMENTS.items():
        repaired = repaired.replace(old, new)
    return repaired


def main():
    backup = []
    changed_count = 0
    backup_dir = Path("instance") / "backups"
    backup_dir.mkdir(parents=True, exist_ok=True)

    for model, fields in TEXT_FIELDS.items():
        for row in model.query.all():
            changes = {}
            for field in fields:
                old_value = getattr(row, field, None)
                new_value = repair_text(old_value)
                if old_value != new_value:
                    changes[field] = {"old": old_value, "new": new_value}
                    setattr(row, field, new_value)
            if changes:
                changed_count += 1
                backup.append({
                    "table": getattr(model, "__tablename__", model.__name__.lower()),
                    "id": getattr(row, "id", None),
                    "changes": changes,
                })

    if not backup:
        print("Nenhum registro com mojibake conhecido foi encontrado.")
        return

    backup_file = backup_dir / f"mojibake_backup_{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}.json"
    backup_file.write_text(json.dumps(backup, ensure_ascii=False, indent=2), encoding="utf-8")
    db.session.commit()
    print(f"{changed_count} registro(s) corrigido(s). Backup: {backup_file}")


main()
