"""remove attachment scanner status

Revision ID: 20260714_05
Revises: 20260714_04
Create Date: 2026-07-14
"""

from alembic import op
import sqlalchemy as sa


revision = "20260714_05"
down_revision = "20260714_04"
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table("incident_attachments") as batch_op:
        batch_op.drop_column("scanner_status")


def downgrade():
    with op.batch_alter_table("incident_attachments") as batch_op:
        batch_op.add_column(sa.Column("scanner_status", sa.String(length=30), nullable=False, server_default="PENDENTE"))
