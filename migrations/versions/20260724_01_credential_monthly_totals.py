"""add credential monthly totals

Revision ID: 20260724_01
Revises: 20260722_01
Create Date: 2026-07-24 00:00:01.000000
"""

from datetime import datetime, timezone

from alembic import op
import sqlalchemy as sa


revision = "20260724_01"
down_revision = "20260722_01"
branch_labels = None
depends_on = None


HISTORICAL_MONTHLY_TOTALS = [
    (2025, 1, 2307),
    (2025, 2, 1577),
    (2025, 3, 2947),
    (2025, 4, 227),
    (2025, 5, 2528),
    (2025, 6, 415),
    (2025, 7, 557),
    (2025, 8, 950),
    (2025, 9, 473),
    (2025, 10, 693),
    (2025, 11, 826),
    (2025, 12, 485),
    (2026, 1, 717),
    (2026, 2, 309),
    (2026, 3, 579),
    (2026, 4, 1863),
    (2026, 5, 2188),
    (2026, 6, 1580),
]


def upgrade():
    inspector = sa.inspect(op.get_bind())
    table_name = "credenciais_coletas_mensais"
    if table_name not in inspector.get_table_names():
        op.create_table(
            table_name,
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("ano_referencia", sa.Integer(), nullable=False),
            sa.Column("mes_referencia", sa.Integer(), nullable=False),
            sa.Column("quantidade_localizada", sa.Integer(), nullable=False, server_default=sa.text("0")),
            sa.CheckConstraint("mes_referencia >= 1 AND mes_referencia <= 12", name="ck_credenciais_coletas_mes"),
            sa.CheckConstraint("quantidade_localizada >= 0", name="ck_credenciais_coletas_quantidade"),
            sa.UniqueConstraint(
                "ano_referencia",
                "mes_referencia",
                name="uq_credenciais_coletas_mensais_competencia",
            ),
        )

    existing_indexes = {index["name"] for index in sa.inspect(op.get_bind()).get_indexes(table_name)}
    if "ix_credenciais_coletas_mensais_ano_referencia" not in existing_indexes:
        op.create_index("ix_credenciais_coletas_mensais_ano_referencia", table_name, ["ano_referencia"])
    if "ix_credenciais_coletas_mensais_mes_referencia" not in existing_indexes:
        op.create_index("ix_credenciais_coletas_mensais_mes_referencia", table_name, ["mes_referencia"])

    _upsert_historical_totals()


def _upsert_historical_totals():
    bind = op.get_bind()
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    dialect = bind.dialect.name
    table_name = "credenciais_coletas_mensais"

    for year, month, total in HISTORICAL_MONTHLY_TOTALS:
        params = {
            "ano_referencia": year,
            "mes_referencia": month,
            "quantidade_localizada": total,
            "created_at": now,
            "updated_at": now,
        }
        if dialect == "sqlite":
            bind.execute(
                sa.text(
                    f"""
                    INSERT INTO {table_name}
                        (ano_referencia, mes_referencia, quantidade_localizada, created_at, updated_at)
                    VALUES
                        (:ano_referencia, :mes_referencia, :quantidade_localizada, :created_at, :updated_at)
                    ON CONFLICT(ano_referencia, mes_referencia)
                    DO UPDATE SET
                        quantidade_localizada = excluded.quantidade_localizada,
                        updated_at = excluded.updated_at
                    """
                ),
                params,
            )
        else:
            existing = bind.execute(
                sa.text(
                    f"""
                    SELECT id FROM {table_name}
                    WHERE ano_referencia = :ano_referencia AND mes_referencia = :mes_referencia
                    """
                ),
                params,
            ).first()
            if existing:
                bind.execute(
                    sa.text(
                        f"""
                        UPDATE {table_name}
                        SET quantidade_localizada = :quantidade_localizada, updated_at = :updated_at
                        WHERE ano_referencia = :ano_referencia AND mes_referencia = :mes_referencia
                        """
                    ),
                    params,
                )
            else:
                bind.execute(
                    sa.text(
                        f"""
                        INSERT INTO {table_name}
                            (ano_referencia, mes_referencia, quantidade_localizada, created_at, updated_at)
                        VALUES
                            (:ano_referencia, :mes_referencia, :quantidade_localizada, :created_at, :updated_at)
                        """
                    ),
                    params,
                )


def downgrade():
    op.drop_index("ix_credenciais_coletas_mensais_mes_referencia", table_name="credenciais_coletas_mensais")
    op.drop_index("ix_credenciais_coletas_mensais_ano_referencia", table_name="credenciais_coletas_mensais")
    op.drop_table("credenciais_coletas_mensais")
