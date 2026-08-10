"""normalize command labels in incidents and legacy units

Revision ID: 20260810_03
Revises: 20260810_02
Create Date: 2026-08-10 00:00:03.000000
"""

from collections import defaultdict
import unicodedata

from alembic import op
import sqlalchemy as sa


revision = "20260810_03"
down_revision = "20260810_02"
branch_labels = None
depends_on = None


def _normalize_key(value):
    normalized = unicodedata.normalize("NFKC", value or "").casefold()
    normalized = normalized.replace("\u00ba", "o").replace("\u00b0", "o")
    return "".join(normalized.split())


def _label_priority(label, count=0):
    compact = "".join((label or "").split())
    has_no_internal_spaces = int(compact == (label or "").strip())
    return (count or 0, has_no_internal_spaces, -len(label or ""), label or "")


def _command_canonicals(connection):
    canonicals = {}
    if "organizational_commands" in sa.inspect(connection).get_table_names():
        for row in connection.execute(sa.text("""
            SELECT name
            FROM organizational_commands
            WHERE name IS NOT NULL AND TRIM(name) <> ''
            ORDER BY name
        """)).mappings():
            name = row["name"].strip()
            canonicals.setdefault(_normalize_key(name), name)
    return canonicals


def _incident_counts(connection):
    counts = {}
    for row in connection.execute(sa.text("""
        SELECT cpa, btl, COUNT(*) AS total
        FROM incidente
        WHERE cpa IS NOT NULL AND btl IS NOT NULL
        GROUP BY cpa, btl
    """)).mappings():
        counts[(row["cpa"], row["btl"])] = row["total"]
    return counts


def _normalize_cpa_values(connection, table_name, canonicals):
    rows = connection.execute(sa.text(f"""
        SELECT cpa
        FROM {table_name}
        WHERE cpa IS NOT NULL AND TRIM(cpa) <> ''
        GROUP BY cpa
    """)).mappings().all()
    groups = defaultdict(list)
    for row in rows:
        cpa = row["cpa"].strip()
        groups[_normalize_key(cpa)].append(cpa)

    for key, variants in groups.items():
        canonical = canonicals.get(key)
        if not canonical:
            canonical = max(variants, key=lambda value: _label_priority(value))
        variant_names = [value for value in variants if value != canonical]
        if not variant_names:
            continue
        connection.execute(
            sa.text(f"""
                UPDATE {table_name}
                SET cpa = :canonical
                WHERE cpa IN :variants
            """).bindparams(sa.bindparam("variants", expanding=True)),
            {"canonical": canonical, "variants": variant_names},
        )


def _deduplicate_legacy_units(connection, incident_counts):
    rows = connection.execute(sa.text("""
        SELECT id, cpa, btl
        FROM unidades
        WHERE cpa IS NOT NULL AND btl IS NOT NULL
        ORDER BY cpa, btl, id
    """)).mappings().all()
    groups = defaultdict(list)
    for row in rows:
        cpa = (row["cpa"] or "").strip()
        btl = (row["btl"] or "").strip()
        if cpa and btl:
            groups[(cpa, _normalize_key(btl))].append(dict(row))

    for (cpa, _unit_key), group_rows in groups.items():
        canonical = max(
            group_rows,
            key=lambda row: _label_priority(row["btl"], incident_counts.get((cpa, row["btl"]), 0)),
        )
        variant_names = [row["btl"] for row in group_rows if row["btl"]]
        duplicate_ids = [row["id"] for row in group_rows if row["id"] != canonical["id"]]
        connection.execute(
            sa.text("""
                UPDATE incidente
                SET btl = :canonical_btl
                WHERE cpa = :cpa AND btl IN :variant_names
            """).bindparams(sa.bindparam("variant_names", expanding=True)),
            {"canonical_btl": canonical["btl"], "cpa": cpa, "variant_names": variant_names},
        )
        if duplicate_ids:
            connection.execute(
                sa.text("DELETE FROM unidades WHERE id IN :ids").bindparams(sa.bindparam("ids", expanding=True)),
                {"ids": duplicate_ids},
            )


def upgrade():
    connection = op.get_bind()
    tables = set(sa.inspect(connection).get_table_names())
    if not {"incidente", "unidades"}.issubset(tables):
        return

    canonicals = _command_canonicals(connection)
    _normalize_cpa_values(connection, "incidente", canonicals)
    _normalize_cpa_values(connection, "unidades", canonicals)
    _deduplicate_legacy_units(connection, _incident_counts(connection))


def downgrade():
    # Data normalization is intentionally not reversed.
    pass
