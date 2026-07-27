"""seed 2024 credential monthly totals

Revision ID: 20260727_01
Revises: 20260724_01
Create Date: 2026-07-27
"""

from alembic import op
import sqlalchemy as sa


revision = "20260727_01"
down_revision = "20260724_01"
branch_labels = None
depends_on = None


MONTHLY_TOTALS_2024 = {
    2: 508,
    3: 854,
    4: 350,
    5: 453,
    6: 1900,
    7: 911,
    8: 586,
    9: 561,
    10: 485,
    11: 1965,
    12: 1251,
}


def _upsert_total(connection, month, total):
    dialect = connection.dialect.name
    if dialect == "sqlite":
        connection.execute(
            sa.text(
                """
                INSERT INTO credenciais_coletas_mensais
                    (ano_referencia, mes_referencia, quantidade_localizada, created_at, updated_at)
                VALUES
                    (:year, :month, :total, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)
                ON CONFLICT(ano_referencia, mes_referencia)
                DO UPDATE SET
                    quantidade_localizada = excluded.quantidade_localizada,
                    updated_at = excluded.updated_at
                """
            ),
            {"year": 2024, "month": month, "total": total},
        )
        return

    existing = connection.execute(
        sa.text(
            """
            SELECT id FROM credenciais_coletas_mensais
            WHERE ano_referencia = :year AND mes_referencia = :month
            """
        ),
        {"year": 2024, "month": month},
    ).first()
    if existing:
        connection.execute(
            sa.text(
                """
                UPDATE credenciais_coletas_mensais
                SET quantidade_localizada = :total, updated_at = CURRENT_TIMESTAMP
                WHERE ano_referencia = :year AND mes_referencia = :month
                """
            ),
            {"year": 2024, "month": month, "total": total},
        )
    else:
        connection.execute(
            sa.text(
                """
                INSERT INTO credenciais_coletas_mensais
                    (ano_referencia, mes_referencia, quantidade_localizada, created_at, updated_at)
                VALUES
                    (:year, :month, :total, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)
                """
            ),
            {"year": 2024, "month": month, "total": total},
        )


def upgrade():
    connection = op.get_bind()
    for month, total in MONTHLY_TOTALS_2024.items():
        _upsert_total(connection, month, total)


def downgrade():
    connection = op.get_bind()
    connection.execute(
        sa.text(
            """
            DELETE FROM credenciais_coletas_mensais
            WHERE ano_referencia = :year
            """
        ),
        {"year": 2024},
    )
