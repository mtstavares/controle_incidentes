"""add user soft delete fields

Revision ID: 20260715_02
Revises: 20260715_01
Create Date: 2026-07-15 00:00:01.000000
"""

from alembic import op
import sqlalchemy as sa


revision = "20260715_02"
down_revision = "20260715_01"
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table("user") as batch_op:
        batch_op.add_column(sa.Column("is_active", sa.Boolean(), nullable=True))
        batch_op.add_column(sa.Column("deleted_by_id", sa.Integer(), nullable=True))

    op.execute(sa.text('UPDATE "user" SET is_active = 1 WHERE is_active IS NULL'))

    with op.batch_alter_table("user") as batch_op:
        batch_op.alter_column("is_active", nullable=False)
        batch_op.create_index("ix_user_is_active", ["is_active"])
        batch_op.create_index("ix_user_deleted_by_id", ["deleted_by_id"])
        batch_op.create_foreign_key("fk_user_deleted_by_id_user", "user", ["deleted_by_id"], ["id"])


def downgrade():
    with op.batch_alter_table("user") as batch_op:
        batch_op.drop_constraint("fk_user_deleted_by_id_user", type_="foreignkey")
        batch_op.drop_index("ix_user_deleted_by_id")
        batch_op.drop_index("ix_user_is_active")
        batch_op.drop_column("deleted_by_id")
        batch_op.drop_column("is_active")
