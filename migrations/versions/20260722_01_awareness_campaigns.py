"""add awareness campaigns table

Revision ID: 20260722_01
Revises: 20260721_01
Create Date: 2026-07-22 12:00:00.000000
"""

from alembic import op
import sqlalchemy as sa


revision = "20260722_01"
down_revision = "20260721_01"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "conscientizacao_campanhas",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("titulo", sa.String(length=150), nullable=False),
        sa.Column("imagem_arquivo", sa.String(length=255), nullable=False),
        sa.Column("imagem_mime_type", sa.String(length=50), nullable=False),
        sa.Column("imagem_tamanho", sa.BigInteger(), nullable=False),
        sa.Column("data_publicacao", sa.Date(), nullable=False),
        sa.Column("created_by_id", sa.Integer(), nullable=True),
        sa.ForeignKeyConstraint(["created_by_id"], ["user.id"]),
        sa.UniqueConstraint("imagem_arquivo", name="uq_conscientizacao_campanhas_imagem_arquivo"),
    )
    op.create_index("ix_conscientizacao_campanhas_titulo", "conscientizacao_campanhas", ["titulo"])
    op.create_index(
        "ix_conscientizacao_campanhas_data_publicacao",
        "conscientizacao_campanhas",
        ["data_publicacao"],
    )
    op.create_index(
        "ix_conscientizacao_campanhas_created_by_id",
        "conscientizacao_campanhas",
        ["created_by_id"],
    )


def downgrade():
    op.drop_index("ix_conscientizacao_campanhas_created_by_id", table_name="conscientizacao_campanhas")
    op.drop_index("ix_conscientizacao_campanhas_data_publicacao", table_name="conscientizacao_campanhas")
    op.drop_index("ix_conscientizacao_campanhas_titulo", table_name="conscientizacao_campanhas")
    op.drop_table("conscientizacao_campanhas")
