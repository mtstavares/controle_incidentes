"""add backup management

Revision ID: 20260728_01
Revises: 20260727_02
Create Date: 2026-07-28 12:00:00.000000
"""

import sqlalchemy as sa
from alembic import op

revision = "20260728_01"
down_revision = "20260727_02"
branch_labels = None
depends_on = None


def _table_names():
    return set(sa.inspect(op.get_bind()).get_table_names())


def _index_names(table_name):
    if table_name not in _table_names():
        return set()
    return {index["name"] for index in sa.inspect(op.get_bind()).get_indexes(table_name)}


def upgrade():
    tables = _table_names()
    if "backup_config" not in tables:
        op.create_table(
            "backup_config",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("diretorio", sa.String(length=500), nullable=False),
            sa.Column("intervalo_horas", sa.Integer(), nullable=False, server_default=sa.text("6")),
            sa.Column("habilitado", sa.Boolean(), nullable=False, server_default=sa.text("1")),
            sa.Column("retencao_dias", sa.Integer(), nullable=False, server_default=sa.text("30")),
            sa.Column("min_backups_completos", sa.Integer(), nullable=False, server_default=sa.text("4")),
            sa.Column("ultima_execucao", sa.DateTime(timezone=True), nullable=True),
            sa.Column("proxima_execucao", sa.DateTime(timezone=True), nullable=True),
            sa.Column("ultimo_resultado", sa.String(length=30), nullable=True),
            sa.Column("formato_versao", sa.String(length=20), nullable=False, server_default="1"),
            sa.Column("updated_by_id", sa.Integer(), nullable=True),
            sa.CheckConstraint("intervalo_horas >= 1 AND intervalo_horas <= 168", name="ck_backup_config_intervalo"),
            sa.CheckConstraint("retencao_dias >= 1 AND retencao_dias <= 3650", name="ck_backup_config_retencao"),
            sa.CheckConstraint("min_backups_completos >= 1 AND min_backups_completos <= 100", name="ck_backup_config_min_full"),
            sa.ForeignKeyConstraint(["updated_by_id"], ["user.id"]),
        )

    if "backup_registros" not in tables:
        op.create_table(
            "backup_registros",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("backup_uid", sa.String(length=64), nullable=False),
            sa.Column("tipo", sa.String(length=20), nullable=False),
            sa.Column("status", sa.String(length=30), nullable=False, server_default="EM_ANDAMENTO"),
            sa.Column("arquivo_nome", sa.String(length=255), nullable=False),
            sa.Column("arquivo_caminho", sa.String(length=700), nullable=False),
            sa.Column("manifesto_caminho", sa.String(length=700), nullable=True),
            sa.Column("base_backup_uid", sa.String(length=64), nullable=True),
            sa.Column("backup_anterior_uid", sa.String(length=64), nullable=True),
            sa.Column("pacote_sha256", sa.String(length=64), nullable=True),
            sa.Column("tamanho_bytes", sa.BigInteger(), nullable=False, server_default=sa.text("0")),
            sa.Column("conteudos", sa.Text(), nullable=True),
            sa.Column("criado_por", sa.String(length=20), nullable=False, server_default="automatico"),
            sa.Column("usuario_id", sa.Integer(), nullable=True),
            sa.Column("iniciado_em", sa.DateTime(timezone=True), nullable=False),
            sa.Column("concluido_em", sa.DateTime(timezone=True), nullable=True),
            sa.Column("duracao_ms", sa.Integer(), nullable=True),
            sa.Column("erro_sanitizado", sa.String(length=500), nullable=True),
            sa.Column("integridade_status", sa.String(length=30), nullable=False, server_default="NAO_VALIDADO"),
            sa.Column("app_commit", sa.String(length=80), nullable=True),
            sa.CheckConstraint("tipo IN ('COMPLETO', 'INCREMENTAL')", name="ck_backup_registros_tipo"),
            sa.CheckConstraint(
                "status IN ('EM_ANDAMENTO', 'CONCLUIDO', 'FALHA', 'INVALIDO', 'QUARENTENA', 'EXCLUIDO')",
                name="ck_backup_registros_status",
            ),
            sa.ForeignKeyConstraint(["usuario_id"], ["user.id"]),
            sa.UniqueConstraint("backup_uid", name="uq_backup_registros_backup_uid"),
        )

    indexes_by_table = {
        "backup_config": {
            "ix_backup_config_updated_by_id": ["updated_by_id"],
        },
        "backup_registros": {
            "ix_backup_registros_backup_uid": ["backup_uid"],
            "ix_backup_registros_tipo": ["tipo"],
            "ix_backup_registros_status": ["status"],
            "ix_backup_registros_base_backup_uid": ["base_backup_uid"],
            "ix_backup_registros_backup_anterior_uid": ["backup_anterior_uid"],
            "ix_backup_registros_usuario_id": ["usuario_id"],
            "ix_backup_registros_iniciado_em": ["iniciado_em"],
        },
    }
    for table_name, indexes in indexes_by_table.items():
        existing = _index_names(table_name)
        for name, columns in indexes.items():
            if name not in existing:
                op.create_index(name, table_name, columns)


def downgrade():
    for table_name in ("backup_registros", "backup_config"):
        if table_name in _table_names():
            for name in list(_index_names(table_name)):
                op.drop_index(name, table_name=table_name)
            op.drop_table(table_name)
