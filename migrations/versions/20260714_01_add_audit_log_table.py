"""add audit log table

Revision ID: 20260714_01
Revises:
Create Date: 2026-07-14
"""

from alembic import op
import sqlalchemy as sa


revision = "20260714_01"
down_revision = None
branch_labels = None
depends_on = None


def upgrade():
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if "audit_logs" in inspector.get_table_names():
        return

    op.create_table(
        "audit_logs",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("timestamp", sa.DateTime(timezone=True), nullable=False),
        sa.Column("usuario_id", sa.Integer(), sa.ForeignKey("user.id"), nullable=True),
        sa.Column("usuario_identificacao", sa.String(length=255), nullable=False),
        sa.Column("acao", sa.String(length=50), nullable=False),
        sa.Column("modulo", sa.String(length=100), nullable=False),
        sa.Column("entidade", sa.String(length=100), nullable=True),
        sa.Column("entidade_id", sa.String(length=100), nullable=True),
        sa.Column("descricao", sa.String(length=500), nullable=False),
        sa.Column("alteracoes", sa.JSON(), nullable=True),
        sa.Column("ip_address", sa.String(length=45), nullable=True),
        sa.Column("user_agent", sa.String(length=500), nullable=True),
        sa.Column("endpoint", sa.String(length=255), nullable=True),
        sa.Column("metodo_http", sa.String(length=10), nullable=True),
        sa.Column("resultado", sa.String(length=30), nullable=False, server_default="SUCESSO"),
    )
    op.create_index("ix_audit_logs_timestamp", "audit_logs", ["timestamp"])
    op.create_index("ix_audit_logs_usuario_id", "audit_logs", ["usuario_id"])
    op.create_index("ix_audit_logs_acao", "audit_logs", ["acao"])
    op.create_index("ix_audit_logs_modulo", "audit_logs", ["modulo"])
    op.create_index("ix_audit_logs_entidade_id", "audit_logs", ["entidade_id"])


def downgrade():
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if "audit_logs" not in inspector.get_table_names():
        return
    op.drop_index("ix_audit_logs_entidade_id", table_name="audit_logs")
    op.drop_index("ix_audit_logs_modulo", table_name="audit_logs")
    op.drop_index("ix_audit_logs_acao", table_name="audit_logs")
    op.drop_index("ix_audit_logs_usuario_id", table_name="audit_logs")
    op.drop_index("ix_audit_logs_timestamp", table_name="audit_logs")
    op.drop_table("audit_logs")
