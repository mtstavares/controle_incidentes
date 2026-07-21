"""add compromised credentials table

Revision ID: 20260721_01
Revises: 20260715_04
Create Date: 2026-07-21 00:00:01.000000
"""

from alembic import op
import sqlalchemy as sa


revision = "20260721_01"
down_revision = "20260715_04"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "credenciais_comprometidas",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("nome", sa.String(length=255), nullable=False),
        sa.Column("nome_busca", sa.String(length=255), nullable=False),
        sa.Column("cpf", sa.String(length=11), nullable=False),
        sa.Column("email", sa.String(length=255), nullable=False),
        sa.Column("url_origem", sa.Text(), nullable=True),
        sa.Column("data_coleta", sa.DateTime(timezone=True), nullable=True),
        sa.Column("permitiu_acesso", sa.Boolean(), nullable=False, server_default=sa.text("0")),
        sa.Column("acesso_ad", sa.Boolean(), nullable=False, server_default=sa.text("0")),
        sa.Column("acesso_ms", sa.Boolean(), nullable=False, server_default=sa.text("0")),
        sa.Column("situacao_legal", sa.String(length=150), nullable=True),
        sa.Column("situacao_legal_normalizada", sa.String(length=150), nullable=True),
        sa.Column("observacoes", sa.Text(), nullable=True),
        sa.Column("mensagem_bloqueio", sa.Text(), nullable=True),
        sa.Column("imported_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("imported_by_id", sa.Integer(), nullable=True),
        sa.ForeignKeyConstraint(["imported_by_id"], ["user.id"]),
        sa.UniqueConstraint(
            "cpf",
            "email",
            "url_origem",
            "data_coleta",
            name="uq_credenciais_comprometidas_dedup",
        ),
    )
    op.create_index("ix_credenciais_comprometidas_cpf", "credenciais_comprometidas", ["cpf"])
    op.create_index("ix_credenciais_comprometidas_email", "credenciais_comprometidas", ["email"])
    op.create_index("ix_credenciais_comprometidas_nome_busca", "credenciais_comprometidas", ["nome_busca"])
    op.create_index("ix_credenciais_comprometidas_data_coleta", "credenciais_comprometidas", ["data_coleta"])
    op.create_index("ix_credenciais_comprometidas_acesso_ad", "credenciais_comprometidas", ["acesso_ad"])
    op.create_index("ix_credenciais_comprometidas_acesso_ms", "credenciais_comprometidas", ["acesso_ms"])
    op.create_index("ix_credenciais_comprometidas_permitiu_acesso", "credenciais_comprometidas", ["permitiu_acesso"])
    op.create_index(
        "ix_credenciais_comprometidas_situacao_legal_normalizada",
        "credenciais_comprometidas",
        ["situacao_legal_normalizada"],
    )
    op.create_index("ix_credenciais_comprometidas_imported_at", "credenciais_comprometidas", ["imported_at"])
    op.create_index("ix_credenciais_comprometidas_imported_by_id", "credenciais_comprometidas", ["imported_by_id"])


def downgrade():
    op.drop_index("ix_credenciais_comprometidas_imported_by_id", table_name="credenciais_comprometidas")
    op.drop_index("ix_credenciais_comprometidas_imported_at", table_name="credenciais_comprometidas")
    op.drop_index("ix_credenciais_comprometidas_situacao_legal_normalizada", table_name="credenciais_comprometidas")
    op.drop_index("ix_credenciais_comprometidas_permitiu_acesso", table_name="credenciais_comprometidas")
    op.drop_index("ix_credenciais_comprometidas_acesso_ms", table_name="credenciais_comprometidas")
    op.drop_index("ix_credenciais_comprometidas_acesso_ad", table_name="credenciais_comprometidas")
    op.drop_index("ix_credenciais_comprometidas_data_coleta", table_name="credenciais_comprometidas")
    op.drop_index("ix_credenciais_comprometidas_nome_busca", table_name="credenciais_comprometidas")
    op.drop_index("ix_credenciais_comprometidas_email", table_name="credenciais_comprometidas")
    op.drop_index("ix_credenciais_comprometidas_cpf", table_name="credenciais_comprometidas")
    op.drop_table("credenciais_comprometidas")
