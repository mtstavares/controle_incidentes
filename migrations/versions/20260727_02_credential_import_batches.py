"""add credential import batches

Revision ID: 20260727_02
Revises: 20260727_01
Create Date: 2026-07-27 12:00:00.000000
"""

from alembic import op
import sqlalchemy as sa


revision = "20260727_02"
down_revision = "20260727_01"
branch_labels = None
depends_on = None


def _table_names():
    return set(sa.inspect(op.get_bind()).get_table_names())


def _column_names(table_name):
    return {column["name"] for column in sa.inspect(op.get_bind()).get_columns(table_name)}


def _index_names(table_name):
    return {index["name"] for index in sa.inspect(op.get_bind()).get_indexes(table_name)}


def upgrade():
    if "credenciais_import_lotes" not in _table_names():
        op.create_table(
            "credenciais_import_lotes",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("arquivo_nome_original", sa.String(length=255), nullable=False),
            sa.Column("arquivo_sha256", sa.String(length=64), nullable=False),
            sa.Column("ano_referencia", sa.Integer(), nullable=False),
            sa.Column("mes_referencia", sa.Integer(), nullable=False),
            sa.Column("imported_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("imported_by_id", sa.Integer(), nullable=True),
            sa.Column("total_testado", sa.Integer(), nullable=False, server_default=sa.text("0")),
            sa.Column("total_validado", sa.Integer(), nullable=False, server_default=sa.text("0")),
            sa.Column("total_somente_ad", sa.Integer(), nullable=False, server_default=sa.text("0")),
            sa.Column("total_somente_ms", sa.Integer(), nullable=False, server_default=sa.text("0")),
            sa.Column("total_ad_ms", sa.Integer(), nullable=False, server_default=sa.text("0")),
            sa.Column("total_nao_validado", sa.Integer(), nullable=False, server_default=sa.text("0")),
            sa.Column("rejeitados", sa.Integer(), nullable=False, server_default=sa.text("0")),
            sa.Column("status", sa.String(length=30), nullable=False, server_default="ativo"),
            sa.Column("versao", sa.Integer(), nullable=False, server_default=sa.text("1")),
            sa.Column("lote_substituido_id", sa.Integer(), nullable=True),
            sa.CheckConstraint("mes_referencia >= 1 AND mes_referencia <= 12", name="ck_credenciais_lotes_mes"),
            sa.CheckConstraint("total_testado >= 0", name="ck_credenciais_lotes_total_testado"),
            sa.CheckConstraint("total_validado >= 0", name="ck_credenciais_lotes_total_validado"),
            sa.CheckConstraint("total_nao_validado >= 0", name="ck_credenciais_lotes_total_nao_validado"),
            sa.ForeignKeyConstraint(["imported_by_id"], ["user.id"]),
            sa.ForeignKeyConstraint(["lote_substituido_id"], ["credenciais_import_lotes.id"]),
            sa.UniqueConstraint("arquivo_sha256", name="uq_credenciais_import_lotes_arquivo_sha256"),
            sa.UniqueConstraint(
                "ano_referencia",
                "mes_referencia",
                "versao",
                name="uq_credenciais_import_lotes_competencia_versao",
            ),
        )

    indexes = _index_names("credenciais_import_lotes")
    for name, columns in {
        "ix_credenciais_import_lotes_arquivo_sha256": ["arquivo_sha256"],
        "ix_credenciais_import_lotes_ano_referencia": ["ano_referencia"],
        "ix_credenciais_import_lotes_mes_referencia": ["mes_referencia"],
        "ix_credenciais_import_lotes_imported_at": ["imported_at"],
        "ix_credenciais_import_lotes_imported_by_id": ["imported_by_id"],
        "ix_credenciais_import_lotes_status": ["status"],
    }.items():
        if name not in indexes:
            op.create_index(name, "credenciais_import_lotes", columns)

    credential_columns = _column_names("credenciais_comprometidas")
    with op.batch_alter_table("credenciais_comprometidas") as batch_op:
        if "rds" not in credential_columns:
            batch_op.add_column(sa.Column("rds", sa.String(length=255), nullable=True))
        if "credencial_fingerprint" not in credential_columns:
            batch_op.add_column(sa.Column("credencial_fingerprint", sa.String(length=64), nullable=True))
        if "lote_id" not in credential_columns:
            batch_op.add_column(sa.Column("lote_id", sa.Integer(), nullable=True))
            batch_op.create_foreign_key(
                "fk_credenciais_comprometidas_lote_id",
                "credenciais_import_lotes",
                ["lote_id"],
                ["id"],
            )
        try:
            batch_op.drop_constraint("uq_credenciais_comprometidas_dedup", type_="unique")
        except (ValueError, NotImplementedError):
            pass
        batch_op.create_unique_constraint(
            "uq_credenciais_comprometidas_dedup",
            ["cpf", "credencial_fingerprint", "data_coleta", "lote_id"],
        )

    indexes = _index_names("credenciais_comprometidas")
    for name, columns in {
        "ix_credenciais_comprometidas_credencial_fingerprint": ["credencial_fingerprint"],
        "ix_credenciais_comprometidas_lote_id": ["lote_id"],
    }.items():
        if name not in indexes:
            op.create_index(name, "credenciais_comprometidas", columns)


def downgrade():
    indexes = _index_names("credenciais_comprometidas")
    for name in (
        "ix_credenciais_comprometidas_lote_id",
        "ix_credenciais_comprometidas_credencial_fingerprint",
    ):
        if name in indexes:
            op.drop_index(name, table_name="credenciais_comprometidas")

    credential_columns = _column_names("credenciais_comprometidas")
    with op.batch_alter_table("credenciais_comprometidas") as batch_op:
        try:
            batch_op.drop_constraint("uq_credenciais_comprometidas_dedup", type_="unique")
        except (ValueError, NotImplementedError):
            pass
        batch_op.create_unique_constraint(
            "uq_credenciais_comprometidas_dedup",
            ["cpf", "email", "url_origem", "data_coleta"],
        )
        for column in ("lote_id", "credencial_fingerprint", "rds"):
            if column in credential_columns:
                batch_op.drop_column(column)

    if "credenciais_import_lotes" in _table_names():
        for name in (
            "ix_credenciais_import_lotes_status",
            "ix_credenciais_import_lotes_imported_by_id",
            "ix_credenciais_import_lotes_imported_at",
            "ix_credenciais_import_lotes_mes_referencia",
            "ix_credenciais_import_lotes_ano_referencia",
            "ix_credenciais_import_lotes_arquivo_sha256",
        ):
            if name in _index_names("credenciais_import_lotes"):
                op.drop_index(name, table_name="credenciais_import_lotes")
        op.drop_table("credenciais_import_lotes")
