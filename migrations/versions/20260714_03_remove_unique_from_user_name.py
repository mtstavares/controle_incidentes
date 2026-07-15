"""remove unique from user name

Revision ID: 20260714_03
Revises: 20260714_02
Create Date: 2026-07-14
"""

from alembic import op
import sqlalchemy as sa


revision = "20260714_03"
down_revision = "20260714_02"
branch_labels = None
depends_on = None


def upgrade():
    bind = op.get_bind()
    if bind.dialect.name != "sqlite":
        return

    op.execute("PRAGMA foreign_keys=OFF")
    op.create_table(
        "user_new",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("username", sa.String(length=50), nullable=False),
        sa.Column("name", sa.String(length=150), nullable=False),
        sa.Column("email", sa.String(length=255), nullable=False),
        sa.Column("profile", sa.String(length=50), nullable=False),
        sa.Column("is_temp_password", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("must_change_password", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("password", sa.String(length=256), nullable=False),
        sa.UniqueConstraint("username"),
        sa.UniqueConstraint("email"),
    )
    op.execute(
        """
        INSERT INTO user_new (id, username, name, email, profile, is_temp_password, must_change_password, password)
        SELECT id, username, name, email, profile, is_temp_password, must_change_password, password
        FROM user
        """
    )
    op.drop_table("user")
    op.rename_table("user_new", "user")
    op.create_index("ix_user_username", "user", ["username"], unique=False)
    op.create_index("ix_user_email", "user", ["email"], unique=False)
    op.execute("PRAGMA foreign_keys=ON")


def downgrade():
    # Reintroducing a unique constraint on name can fail if duplicate names exist.
    pass
