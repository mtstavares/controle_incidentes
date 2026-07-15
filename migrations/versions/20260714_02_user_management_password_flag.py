"""user management password flag

Revision ID: 20260714_02
Revises: 20260714_01
Create Date: 2026-07-14
"""

from alembic import op
import sqlalchemy as sa


revision = "20260714_02"
down_revision = "20260714_01"
branch_labels = None
depends_on = None


def _columns(table_name):
    return {column["name"] for column in sa.inspect(op.get_bind()).get_columns(table_name)}


def _indexes(table_name):
    return {index["name"] for index in sa.inspect(op.get_bind()).get_indexes(table_name)}


def upgrade():
    columns = _columns("user")
    if "must_change_password" not in columns:
        op.add_column(
            "user",
            sa.Column("must_change_password", sa.Boolean(), nullable=False, server_default=sa.false()),
        )

    indexes = _indexes("user")
    if "ix_user_username" not in indexes:
        op.create_index("ix_user_username", "user", ["username"], unique=False)
    if "ix_user_email" not in indexes:
        op.create_index("ix_user_email", "user", ["email"], unique=False)


def downgrade():
    indexes = _indexes("user")
    if "ix_user_email" in indexes:
        op.drop_index("ix_user_email", table_name="user")
    if "ix_user_username" in indexes:
        op.drop_index("ix_user_username", table_name="user")
    if "must_change_password" in _columns("user"):
        op.drop_column("user", "must_change_password")
