"""add operational timestamps

Revision ID: 20260715_01
Revises: 20260714_05
Create Date: 2026-07-15 00:00:00.000000
"""

from alembic import op
import sqlalchemy as sa


revision = "20260715_01"
down_revision = "20260714_05"
branch_labels = None
depends_on = None


TABLES_WITH_TIMESTAMPS = [
    "user",
    "incidente",
    "incidente_obs",
    "incident_attachments",
]


def upgrade():
    for table_name in TABLES_WITH_TIMESTAMPS:
        with op.batch_alter_table(table_name) as batch_op:
            batch_op.add_column(sa.Column("created_at", sa.DateTime(timezone=True), nullable=True))
            batch_op.add_column(sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True))
            batch_op.add_column(sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True))

        op.execute(
            sa.text(
                f"""
                UPDATE {table_name}
                SET
                    created_at = COALESCE(created_at, CURRENT_TIMESTAMP),
                    updated_at = COALESCE(updated_at, CURRENT_TIMESTAMP)
                """
            )
        )

        with op.batch_alter_table(table_name) as batch_op:
            batch_op.alter_column("created_at", nullable=False)
            batch_op.alter_column("updated_at", nullable=False)

    with op.batch_alter_table("audit_logs") as batch_op:
        batch_op.add_column(sa.Column("request_id", sa.String(length=64), nullable=True))
        batch_op.create_index("ix_audit_logs_request_id", ["request_id"])


def downgrade():
    with op.batch_alter_table("audit_logs") as batch_op:
        batch_op.drop_index("ix_audit_logs_request_id")
        batch_op.drop_column("request_id")

    for table_name in reversed(TABLES_WITH_TIMESTAMPS):
        with op.batch_alter_table(table_name) as batch_op:
            batch_op.drop_column("deleted_at")
            batch_op.drop_column("updated_at")
            batch_op.drop_column("created_at")
