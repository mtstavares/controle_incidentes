"""seed dashboard organizational units

Revision ID: 20260715_03
Revises: 20260715_02
Create Date: 2026-07-15 00:00:02.000000
"""

import json
from pathlib import Path

from alembic import op
import sqlalchemy as sa


revision = "20260715_03"
down_revision = "20260715_02"
branch_labels = None
depends_on = None


def _load_units():
    data_file = (
        Path(__file__).resolve().parents[2]
        / "app"
        / "seeds"
        / "data"
        / "organizational_units.dev.json"
    )
    with data_file.open("r", encoding="utf-8") as file:
        return json.load(file).get("organizationalUnits", [])


def upgrade():
    connection = op.get_bind()
    unidades = sa.table(
        "unidades",
        sa.column("id", sa.Integer),
        sa.column("cpa", sa.String),
        sa.column("btl", sa.String),
    )

    for group in _load_units():
        cpa = (group.get("cpa") or "").strip()
        if not cpa:
            continue

        for battalion in group.get("battalions") or []:
            btl = (battalion or "").strip()
            if not btl:
                continue

            exists = connection.execute(
                sa.select(unidades.c.id).where(
                    unidades.c.cpa == cpa,
                    unidades.c.btl == btl,
                )
            ).first()
            if exists:
                continue

            connection.execute(unidades.insert().values(cpa=cpa, btl=btl))


def downgrade():
    connection = op.get_bind()
    unidades = sa.table(
        "unidades",
        sa.column("id", sa.Integer),
        sa.column("cpa", sa.String),
        sa.column("btl", sa.String),
    )

    for group in _load_units():
        cpa = (group.get("cpa") or "").strip()
        for battalion in group.get("battalions") or []:
            btl = (battalion or "").strip()
            if cpa and btl:
                connection.execute(
                    unidades.delete().where(
                        unidades.c.cpa == cpa,
                        unidades.c.btl == btl,
                    )
                )
