"""normalize incident unit labels

Revision ID: 20260810_02
Revises: 20260810_01
Create Date: 2026-08-10 00:00:02.000000
"""

from collections import defaultdict
import unicodedata

from alembic import op
import sqlalchemy as sa


revision = "20260810_02"
down_revision = "20260810_01"
branch_labels = None
depends_on = None


def _normalize_unit_key(value):
    normalized = unicodedata.normalize("NFKC", value or "").casefold()
    normalized = normalized.replace("\u00ba", "o").replace("\u00b0", "o")
    return "".join(normalized.split())


def upgrade():
    connection = op.get_bind()
    tables = set(sa.inspect(connection).get_table_names())
    if not {"incidente", "unidades"}.issubset(tables):
        return

    canonical_by_key = {}
    for row in connection.execute(sa.text("""
        SELECT cpa, btl
        FROM unidades
        WHERE cpa IS NOT NULL AND btl IS NOT NULL
        ORDER BY cpa, btl
    """)).mappings():
        cpa = (row["cpa"] or "").strip()
        btl = (row["btl"] or "").strip()
        key = (cpa, _normalize_unit_key(btl))
        if cpa and key[1] and key not in canonical_by_key:
            canonical_by_key[key] = btl

    incident_groups = defaultdict(set)
    for row in connection.execute(sa.text("""
        SELECT cpa, btl
        FROM incidente
        WHERE cpa IS NOT NULL AND btl IS NOT NULL
    """)).mappings():
        cpa = (row["cpa"] or "").strip()
        btl = (row["btl"] or "").strip()
        key = (cpa, _normalize_unit_key(btl))
        if cpa and key[1]:
            incident_groups[key].add(btl)

    for key, variants in incident_groups.items():
        canonical = canonical_by_key.get(key)
        if not canonical:
            continue
        variant_names = [value for value in variants if value != canonical]
        if not variant_names:
            continue
        connection.execute(
            sa.text("""
                UPDATE incidente
                SET btl = :canonical_btl
                WHERE cpa = :cpa AND btl IN :variant_names
            """).bindparams(sa.bindparam("variant_names", expanding=True)),
            {
                "canonical_btl": canonical,
                "cpa": key[0],
                "variant_names": variant_names,
            },
        )


def downgrade():
    # Data normalization is intentionally not reversed.
    pass
