"""deduplicate organizational unit labels

Revision ID: 20260810_01
Revises: 20260728_01
Create Date: 2026-08-10 00:00:01.000000
"""

from collections import defaultdict
from datetime import datetime, timezone
import unicodedata

from alembic import op
import sqlalchemy as sa


revision = "20260810_01"
down_revision = "20260728_01"
branch_labels = None
depends_on = None


def _normalize_unit_key(value):
    normalized = unicodedata.normalize("NFKC", value or "").casefold()
    normalized = normalized.replace("\u00ba", "o").replace("\u00b0", "o")
    return "".join(normalized.split())


def _label_priority(label, count=0):
    compact = "".join((label or "").split())
    has_no_internal_spaces = int(compact == (label or "").strip())
    return (count or 0, has_no_internal_spaces, -len(label or ""), label or "")


def _now():
    return datetime.now(timezone.utc)


def _incident_counts(connection):
    rows = connection.execute(sa.text("""
        SELECT cpa, btl, COUNT(*) AS total
        FROM incidente
        WHERE cpa IS NOT NULL AND btl IS NOT NULL
        GROUP BY cpa, btl
    """)).mappings().all()
    return {(row["cpa"], row["btl"]): row["total"] for row in rows}


def _deduplicate_organizational_units(connection, incident_counts):
    command_rows = connection.execute(sa.text("SELECT id, name FROM organizational_commands")).mappings().all()
    commands = {row["id"]: row["name"] for row in command_rows}
    unit_rows = connection.execute(sa.text("""
        SELECT id, command_id, name, normalized_name, active, sort_order
        FROM organizational_units
        ORDER BY command_id, name, id
    """)).mappings().all()

    groups = defaultdict(list)
    for row in unit_rows:
        key = (row["command_id"], _normalize_unit_key(row["name"]))
        groups[key].append(dict(row))

    canonical_by_cpa_key = {}
    for (command_id, unit_key), rows in groups.items():
        command_name = commands.get(command_id)
        if not command_name or not unit_key:
            continue

        canonical = max(
            rows,
            key=lambda row: _label_priority(row["name"], incident_counts.get((command_name, row["name"]), 0)),
        )
        canonical_by_cpa_key[(command_name, unit_key)] = canonical["name"]
        variant_names = [row["name"] for row in rows if row["name"]]
        variant_ids = [row["id"] for row in rows]
        duplicate_ids = [row["id"] for row in rows if row["id"] != canonical["id"]]

        if duplicate_ids or canonical.get("normalized_name") != unit_key:
            connection.execute(
                sa.text("""
                    UPDATE incidente
                    SET command_id = :command_id, unit_id = :unit_id, cpa = :cpa, btl = :btl
                    WHERE unit_id IN :variant_ids
                       OR (cpa = :cpa AND btl IN :variant_names)
                """).bindparams(
                    sa.bindparam("variant_ids", expanding=True),
                    sa.bindparam("variant_names", expanding=True),
                ),
                {
                    "command_id": command_id,
                    "unit_id": canonical["id"],
                    "cpa": command_name,
                    "btl": canonical["name"],
                    "variant_ids": variant_ids,
                    "variant_names": variant_names,
                },
            )
            if duplicate_ids:
                connection.execute(
                    sa.text("DELETE FROM organizational_units WHERE id IN :ids").bindparams(
                        sa.bindparam("ids", expanding=True)
                    ),
                    {"ids": duplicate_ids},
                )
            connection.execute(
                sa.text("""
                    UPDATE organizational_units
                    SET name = :name, normalized_name = :normalized_name, active = 1, updated_at = :updated_at
                    WHERE id = :id
                """),
                {
                    "id": canonical["id"],
                    "name": canonical["name"],
                    "normalized_name": unit_key,
                    "updated_at": _now(),
                },
            )
    return canonical_by_cpa_key


def _deduplicate_legacy_units(connection, incident_counts, canonical_by_cpa_key):
    rows = connection.execute(sa.text("""
        SELECT id, cpa, btl
        FROM unidades
        WHERE cpa IS NOT NULL AND btl IS NOT NULL
        ORDER BY cpa, btl, id
    """)).mappings().all()
    groups = defaultdict(list)
    for row in rows:
        key = ((row["cpa"] or "").strip(), _normalize_unit_key(row["btl"]))
        if key[0] and key[1]:
            groups[key].append(dict(row))

    for (cpa, unit_key), group_rows in groups.items():
        preferred_name = canonical_by_cpa_key.get((cpa, unit_key))
        if preferred_name:
            canonical = next((row for row in group_rows if row["btl"] == preferred_name), None)
            if canonical is None:
                canonical = group_rows[0]
                connection.execute(
                    sa.text("UPDATE unidades SET btl = :btl WHERE id = :id"),
                    {"id": canonical["id"], "btl": preferred_name},
                )
                canonical["btl"] = preferred_name
        else:
            canonical = max(
                group_rows,
                key=lambda row: _label_priority(row["btl"], incident_counts.get((cpa, row["btl"]), 0)),
            )
            preferred_name = canonical["btl"]

        variant_names = [row["btl"] for row in group_rows if row["btl"]]
        duplicate_ids = [row["id"] for row in group_rows if row["id"] != canonical["id"]]
        connection.execute(
            sa.text("""
                UPDATE incidente
                SET btl = :canonical_btl
                WHERE cpa = :cpa AND btl IN :variant_names
            """).bindparams(sa.bindparam("variant_names", expanding=True)),
            {"canonical_btl": preferred_name, "cpa": cpa, "variant_names": variant_names},
        )
        if duplicate_ids:
            connection.execute(
                sa.text("DELETE FROM unidades WHERE id IN :ids").bindparams(sa.bindparam("ids", expanding=True)),
                {"ids": duplicate_ids},
            )


def upgrade():
    connection = op.get_bind()
    tables = set(sa.inspect(connection).get_table_names())
    required = {"incidente", "unidades", "organizational_commands", "organizational_units"}
    if not required.issubset(tables):
        return

    incident_counts = _incident_counts(connection)
    canonical_by_cpa_key = _deduplicate_organizational_units(connection, incident_counts)
    _deduplicate_legacy_units(connection, incident_counts, canonical_by_cpa_key)


def downgrade():
    # Data consolidation is intentionally not reversed.
    pass
