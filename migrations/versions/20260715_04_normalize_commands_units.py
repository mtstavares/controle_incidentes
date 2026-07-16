"""normalize organizational commands and units

Revision ID: 20260715_04
Revises: 20260715_03
Create Date: 2026-07-15 00:00:03.000000
"""

import json
from datetime import datetime, timezone
from pathlib import Path

from alembic import op
import sqlalchemy as sa


revision = "20260715_04"
down_revision = "20260715_03"
branch_labels = None
depends_on = None


RENAMES = {
    "CPA/M-5 - SEDE": "CPA/M-5",
    "CPA/M-1 - SEDE": "CPA/M-1",
}

TEXT_REPAIRS = {
    "Tentativa de intrusÃ£o": "Tentativa de intrusão",
    "RequisiÃ§Ãµes automatizadas": "Requisições automatizadas",
    "TransferÃªncia de arquivo malicioso": "Transferência de arquivo malicioso",
    "Em AnÃ¡lise": "Em Análise",
    "Em MitigaÃ§Ã£o": "Em Mitigação",
}


def _normalize_name(value):
    return " ".join((value or "").strip().casefold().split())


def _load_seed_units():
    data_file = (
        Path(__file__).resolve().parents[2]
        / "app"
        / "seeds"
        / "data"
        / "organizational_units.dev.json"
    )
    with data_file.open("r", encoding="utf-8") as file:
        return json.load(file).get("organizationalUnits", [])


def _now():
    return datetime.now(timezone.utc)


def _ensure_command(connection, commands, name, sort_order=None):
    row = connection.execute(
        sa.select(commands.c.id).where(commands.c.name == name)
    ).first()
    if row:
        return row[0]

    connection.execute(commands.insert().values(
        name=name,
        active=True,
        sort_order=sort_order,
        created_at=_now(),
        updated_at=_now(),
    ))
    return connection.execute(
        sa.select(commands.c.id).where(commands.c.name == name)
    ).scalar_one()


def _ensure_unit(connection, units, command_id, name, sort_order=None):
    normalized_name = _normalize_name(name)
    row = connection.execute(
        sa.select(units.c.id).where(
            units.c.command_id == command_id,
            units.c.normalized_name == normalized_name,
        )
    ).first()
    if row:
        connection.execute(
            units.update()
            .where(units.c.id == row[0])
            .values(name=name, active=True, sort_order=sort_order, updated_at=_now())
        )
        return row[0]

    connection.execute(units.insert().values(
        command_id=command_id,
        name=name,
        normalized_name=normalized_name,
        active=True,
        sort_order=sort_order,
        created_at=_now(),
        updated_at=_now(),
    ))
    return connection.execute(
        sa.select(units.c.id).where(
            units.c.command_id == command_id,
            units.c.normalized_name == normalized_name,
        )
    ).scalar_one()


def upgrade():
    connection = op.get_bind()
    inspector = sa.inspect(connection)
    tables = set(inspector.get_table_names())

    if "organizational_commands" not in tables:
        op.create_table(
            "organizational_commands",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("name", sa.String(length=100), nullable=False),
            sa.Column("active", sa.Boolean(), nullable=False, server_default=sa.text("1")),
            sa.Column("sort_order", sa.Integer(), nullable=True),
        )
        op.create_index("ix_organizational_commands_name", "organizational_commands", ["name"], unique=True)
        op.create_index("ix_organizational_commands_active", "organizational_commands", ["active"])
        op.create_index("ix_organizational_commands_sort_order", "organizational_commands", ["sort_order"])

    if "organizational_units" not in tables:
        op.create_table(
            "organizational_units",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("command_id", sa.Integer(), nullable=False),
            sa.Column("name", sa.String(length=100), nullable=False),
            sa.Column("normalized_name", sa.String(length=100), nullable=False),
            sa.Column("active", sa.Boolean(), nullable=False, server_default=sa.text("1")),
            sa.Column("sort_order", sa.Integer(), nullable=True),
            sa.ForeignKeyConstraint(["command_id"], ["organizational_commands.id"]),
            sa.UniqueConstraint("command_id", "normalized_name", name="uq_organizational_units_command_name"),
        )
        op.create_index("ix_organizational_units_command_id", "organizational_units", ["command_id"])
        op.create_index("ix_organizational_units_active", "organizational_units", ["active"])
        op.create_index("ix_organizational_units_sort_order", "organizational_units", ["sort_order"])

    incident_columns = {column["name"] for column in inspector.get_columns("incidente")}
    incident_indexes = {index["name"] for index in inspector.get_indexes("incidente")}
    with op.batch_alter_table("incidente") as batch_op:
        if "command_id" not in incident_columns:
            batch_op.add_column(sa.Column("command_id", sa.Integer(), nullable=True))
        if "unit_id" not in incident_columns:
            batch_op.add_column(sa.Column("unit_id", sa.Integer(), nullable=True))
        if "ix_incidente_command_id" not in incident_indexes:
            batch_op.create_index("ix_incidente_command_id", ["command_id"])
        if "ix_incidente_unit_id" not in incident_indexes:
            batch_op.create_index("ix_incidente_unit_id", ["unit_id"])

    unidades = sa.table("unidades", sa.column("id", sa.Integer), sa.column("cpa", sa.String), sa.column("btl", sa.String))
    incidentes = sa.table("incidente", sa.column("id", sa.Integer), sa.column("cpa", sa.String), sa.column("btl", sa.String), sa.column("incident_type", sa.String), sa.column("status_incident", sa.String), sa.column("command_id", sa.Integer), sa.column("unit_id", sa.Integer))
    tipos = sa.table("tipo_incidente", sa.column("id", sa.Integer), sa.column("tipo_incidente", sa.String), sa.column("desc_incidente", sa.String))
    status_table = sa.table("status_incidente", sa.column("id", sa.Integer), sa.column("status", sa.String), sa.column("desc_status", sa.String))
    commands = sa.table("organizational_commands", sa.column("id", sa.Integer), sa.column("name", sa.String), sa.column("active", sa.Boolean), sa.column("sort_order", sa.Integer), sa.column("created_at", sa.DateTime), sa.column("updated_at", sa.DateTime))
    units = sa.table("organizational_units", sa.column("id", sa.Integer), sa.column("command_id", sa.Integer), sa.column("name", sa.String), sa.column("normalized_name", sa.String), sa.column("active", sa.Boolean), sa.column("sort_order", sa.Integer), sa.column("created_at", sa.DateTime), sa.column("updated_at", sa.DateTime))

    for old, new in RENAMES.items():
        connection.execute(unidades.update().where(unidades.c.cpa == old).values(cpa=new))
        connection.execute(incidentes.update().where(incidentes.c.cpa == old).values(cpa=new))

    for old, new in TEXT_REPAIRS.items():
        connection.execute(incidentes.update().where(incidentes.c.incident_type == old).values(incident_type=new))
        connection.execute(incidentes.update().where(incidentes.c.status_incident == old).values(status_incident=new))
        connection.execute(tipos.update().where(tipos.c.tipo_incidente == old).values(tipo_incidente=new))
        connection.execute(status_table.update().where(status_table.c.status == old).values(status=new))

    for command_index, group in enumerate(_load_seed_units(), start=1):
        cpa = (group.get("cpa") or "").strip()
        if not cpa:
            continue
        command_id = _ensure_command(connection, commands, cpa, command_index)
        for unit_index, unit_name in enumerate(group.get("battalions") or []):
            name = (unit_name or "").strip()
            if not name:
                continue
            _ensure_unit(connection, units, command_id, name, 0 if name == "SEDE" else unit_index + 1)
            exists = connection.execute(
                sa.select(unidades.c.id).where(unidades.c.cpa == cpa, unidades.c.btl == name)
            ).first()
            if not exists:
                connection.execute(unidades.insert().values(cpa=cpa, btl=name))

    for cpa, btl in connection.execute(sa.select(unidades.c.cpa, unidades.c.btl)).all():
        cpa_name = (cpa or "").strip()
        btl_name = (btl or "").strip()
        if not cpa_name or not btl_name:
            continue
        command_id = _ensure_command(connection, commands, cpa_name)
        unit_id = _ensure_unit(connection, units, command_id, btl_name)
        connection.execute(
            incidentes.update()
            .where(incidentes.c.cpa == cpa_name, incidentes.c.btl == btl_name)
            .values(command_id=command_id, unit_id=unit_id)
        )


def downgrade():
    with op.batch_alter_table("incidente") as batch_op:
        batch_op.drop_constraint("fk_incidente_unit_id", type_="foreignkey")
        batch_op.drop_constraint("fk_incidente_command_id", type_="foreignkey")
        batch_op.drop_index("ix_incidente_unit_id")
        batch_op.drop_index("ix_incidente_command_id")
        batch_op.drop_column("unit_id")
        batch_op.drop_column("command_id")

    op.drop_index("ix_organizational_units_sort_order", table_name="organizational_units")
    op.drop_index("ix_organizational_units_active", table_name="organizational_units")
    op.drop_index("ix_organizational_units_command_id", table_name="organizational_units")
    op.drop_table("organizational_units")
    op.drop_index("ix_organizational_commands_sort_order", table_name="organizational_commands")
    op.drop_index("ix_organizational_commands_active", table_name="organizational_commands")
    op.drop_index("ix_organizational_commands_name", table_name="organizational_commands")
    op.drop_table("organizational_commands")
