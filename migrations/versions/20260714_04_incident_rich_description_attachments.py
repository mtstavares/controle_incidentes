"""incident rich description attachments

Revision ID: 20260714_04
Revises: 20260714_03
Create Date: 2026-07-14
"""

from alembic import op
import sqlalchemy as sa


revision = "20260714_04"
down_revision = "20260714_03"
branch_labels = None
depends_on = None


TIPOS_INCIDENTE = [
    "RequisiÃ§Ãµes automatizadas",
    "TransferÃªncia de arquivo malicioso",
    "Bloqueio de acesso a VPN",
    "Phishing",
    "Comando e Controle",
    "Incidente envolvendo VPN corporativa",
    "Criptomining",
    "Malware",
    "Ativador KMS",
    "Tentativa de intrusÃ£o",
    "Comprometimento de Credenciais",
    "Quebra de Confidencialidade",
    "Brute Force",
]


def upgrade():
    with op.batch_alter_table("incidente") as batch_op:
        batch_op.add_column(sa.Column("message_number", sa.String(length=100), nullable=True))
        batch_op.add_column(sa.Column("description_plain_text", sa.Text(), nullable=True))
        batch_op.create_index("ix_incidente_message_number", ["message_number"])

    op.execute("UPDATE incidente SET description_plain_text = description WHERE description_plain_text IS NULL")

    op.create_table(
        "incident_attachments",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("incident_id", sa.Integer(), nullable=False),
        sa.Column("original_filename", sa.String(length=255), nullable=False),
        sa.Column("stored_filename", sa.String(length=255), nullable=False),
        sa.Column("mime_type", sa.String(length=150), nullable=False),
        sa.Column("file_size", sa.BigInteger(), nullable=False),
        sa.Column("sha256", sa.String(length=64), nullable=False),
        sa.Column("uploaded_by_id", sa.Integer(), nullable=False),
        sa.Column("uploaded_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("scanner_status", sa.String(length=30), nullable=False, server_default="PENDENTE"),
        sa.ForeignKeyConstraint(["incident_id"], ["incidente.id"]),
        sa.ForeignKeyConstraint(["uploaded_by_id"], ["user.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("stored_filename"),
    )
    op.create_index("ix_incident_attachments_incident_id", "incident_attachments", ["incident_id"])
    op.create_index("ix_incident_attachments_sha256", "incident_attachments", ["sha256"])
    op.create_index("ix_incident_attachments_uploaded_by_id", "incident_attachments", ["uploaded_by_id"])

    connection = op.get_bind()
    for tipo in TIPOS_INCIDENTE:
        exists = connection.execute(
            sa.text("SELECT id FROM tipo_incidente WHERE lower(tipo_incidente) = lower(:tipo) LIMIT 1"),
            {"tipo": tipo},
        ).first()
        if not exists:
            connection.execute(
                sa.text("INSERT INTO tipo_incidente (tipo_incidente, desc_incidente) VALUES (:tipo, '')"),
                {"tipo": tipo},
            )


def downgrade():
    op.drop_index("ix_incident_attachments_uploaded_by_id", table_name="incident_attachments")
    op.drop_index("ix_incident_attachments_sha256", table_name="incident_attachments")
    op.drop_index("ix_incident_attachments_incident_id", table_name="incident_attachments")
    op.drop_table("incident_attachments")
    with op.batch_alter_table("incidente") as batch_op:
        batch_op.drop_index("ix_incidente_message_number")
        batch_op.drop_column("description_plain_text")
        batch_op.drop_column("message_number")
