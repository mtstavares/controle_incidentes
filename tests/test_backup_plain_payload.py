import json
import sys
import tempfile
import unittest
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from cryptography.hazmat.primitives.ciphers.aead import AESGCM  # noqa: E402

from app import create_app, db  # noqa: E402
from app.models import IncidentAttachment, Incidente, User  # noqa: E402
from app.services import backup_service  # noqa: E402
from config import Config  # noqa: E402


class TestConfig(Config):
    TESTING = True
    SECRET_KEY = "test"
    SQLALCHEMY_DATABASE_URI = "sqlite:///:memory:"
    DIVCIBER_BACKUP_HMAC_KEY = "h" * 48
    DIVCIBER_BACKUP_ENCRYPTION_KEY = "0" * 64


class BackupPlainPayloadTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.app = create_app(TestConfig)
        with self.app.app_context():
            db.create_all()
            backup_service._ensure_layout(self.root)

    def tearDown(self):
        with self.app.app_context():
            db.session.remove()
            db.drop_all()
        self.tmp.cleanup()

    def test_new_backup_package_uses_open_payload_zip(self):
        payload_bytes = b"payload zip bytes"
        manifest = {"backup_uid": "abc", "backup_type": "COMPLETO"}

        with self.app.app_context():
            package_path = backup_service._build_package(self.root, "COMPLETO", "01JAN26_120000", manifest, payload_bytes)
            restored = backup_service._decrypt_payload(package_path)

        with zipfile.ZipFile(package_path, "r") as package:
            names = set(package.namelist())

        self.assertEqual(restored, payload_bytes)
        self.assertIn("PAYLOAD.zip", names)
        self.assertIn("MANIFESTO.json", names)
        self.assertIn("MANIFESTO.hmac", names)
        self.assertNotIn("PAYLOAD.enc", names)
        self.assertNotIn("NONCE.bin", names)

    def test_collect_sources_groups_incident_uploads_by_incident_id(self):
        instance_root = self.root / "instance"
        incident_uploads = instance_root / "uploads" / "incidents"
        awareness_uploads = instance_root / "uploads" / "conscientizacoes"
        incident_uploads.mkdir(parents=True)
        awareness_uploads.mkdir(parents=True)
        incident_bytes = b"incident image"
        (incident_uploads / "stored-1.png").write_bytes(incident_bytes)
        (awareness_uploads / "campaign-1.jpg").write_bytes(b"campaign image")
        snapshot_db = self.root / "snapshot.db"
        snapshot_db.write_bytes(b"db")

        with self.app.app_context():
            user = User(username="admin", name="Admin", email="admin@example.local", profile="Admin", password="hash")
            db.session.add(user)
            db.session.flush()
            incident = Incidente(
                message_number="RDS1",
                incident_type="Teste",
                report_number="001",
                ticket_number=None,
                cpa="CPA/M-1",
                btl="1BPM",
                description="Teste",
                start_date=datetime(2026, 1, 1),
                status_incident="Encerrado",
                user_id=user.id,
            )
            db.session.add(incident)
            db.session.flush()
            incident_id = incident.id
            db.session.add(IncidentAttachment(
                incident_id=incident_id,
                original_filename="Evidencia Principal.png",
                stored_filename="stored-1.png",
                mime_type="image/png",
                file_size=14,
                sha256=backup_service._sha256_bytes(incident_bytes),
                uploaded_by_id=user.id,
                uploaded_at=datetime.now(timezone.utc),
            ))
            db.session.commit()

            with patch.object(backup_service, "_instance_root", return_value=instance_root.resolve()), patch.object(
                backup_service,
                "_snapshot_database",
                return_value=snapshot_db,
            ):
                sources = backup_service._collect_sources(self.root, "01JAN26_120000")

        logical_paths = {logical for logical, _path, _description in sources}

        self.assertIn("database/divciber.db", logical_paths)
        self.assertIn(f"uploads/incidents/ID{incident_id}/stored-1.png", logical_paths)
        self.assertIn("uploads/conscientizacoes/campaign-1.jpg", logical_paths)
        self.assertFalse(any(path.startswith("uploads/visualizacao/") for path in logical_paths))

    def test_collect_sources_links_legacy_renamed_files_by_sha256(self):
        instance_root = self.root / "instance"
        incident_uploads = instance_root / "uploads" / "incidents"
        incident_uploads.mkdir(parents=True)
        legacy_bytes = b"legacy renamed evidence"
        (incident_uploads / "legacy-physical-name.png").write_bytes(legacy_bytes)
        snapshot_db = self.root / "snapshot.db"
        snapshot_db.write_bytes(b"db")

        with self.app.app_context():
            user = User(username="admin", name="Admin", email="admin@example.local", profile="Admin", password="hash")
            db.session.add(user)
            db.session.flush()
            incident = Incidente(
                message_number="RDS1",
                incident_type="Teste",
                report_number="001",
                ticket_number=None,
                cpa="CPA/M-1",
                btl="1BPM",
                description="Teste",
                start_date=datetime(2026, 1, 1),
                status_incident="Encerrado",
                user_id=user.id,
            )
            db.session.add(incident)
            db.session.flush()
            incident_id = incident.id
            db.session.add(IncidentAttachment(
                incident_id=incident_id,
                original_filename="Evidencia antiga.png",
                stored_filename="missing-current-name.png",
                mime_type="image/png",
                file_size=len(legacy_bytes),
                sha256=backup_service._sha256_bytes(legacy_bytes),
                uploaded_by_id=user.id,
                uploaded_at=datetime.now(timezone.utc),
            ))
            db.session.commit()

            with patch.object(backup_service, "_instance_root", return_value=instance_root.resolve()), patch.object(
                backup_service,
                "_snapshot_database",
                return_value=snapshot_db,
            ):
                sources = backup_service._collect_sources(self.root, "01JAN26_120000")

        logical_paths = {logical for logical, _path, _description in sources}

        self.assertIn(f"uploads/incidents/ID{incident_id}/legacy-physical-name.png", logical_paths)
        self.assertNotIn("uploads/incidents/orfaos/legacy-physical-name.png", logical_paths)

    def test_restore_upload_root_flattens_incident_id_folders(self):
        source = self.root / "restore" / "uploads" / "incidents"
        (source / "ID100").mkdir(parents=True)
        (source / "ID100" / "stored-1.png").write_bytes(b"1")
        (source / "ID100" / "stored-2.pdf").write_bytes(b"2")
        target = self.root / "target" / "uploads" / "incidents"

        backup_service._restore_upload_root(source, target, flatten=True)

        self.assertTrue((target / "stored-1.png").is_file())
        self.assertTrue((target / "stored-2.pdf").is_file())
        self.assertFalse((target / "ID100").exists())

    def test_scheduler_lock_allows_only_one_process_leader(self):
        app = type("App", (), {"instance_path": str(self.root / "instance-lock")})()

        first_fd = backup_service._try_acquire_scheduler_lock(app)
        self.assertIsNotNone(first_fd)
        try:
            second_fd = backup_service._try_acquire_scheduler_lock(app)
            self.assertIsNone(second_fd)
        finally:
            backup_service._release_scheduler_lock(app, first_fd)

        third_fd = backup_service._try_acquire_scheduler_lock(app)
        self.assertIsNotNone(third_fd)
        backup_service._release_scheduler_lock(app, third_fd)

    def test_scheduler_lock_replaces_stale_pid_file(self):
        app = type("App", (), {"instance_path": str(self.root / "instance-stale-lock")})()
        lock_path = Path(app.instance_path) / backup_service.SCHEDULER_LOCK_FILE_NAME
        lock_path.parent.mkdir(parents=True)
        lock_path.write_text("999999999 0", encoding="ascii")

        with patch.object(backup_service, "_pid_is_running", return_value=False):
            fd = backup_service._try_acquire_scheduler_lock(app)

        self.assertIsNotNone(fd)
        backup_service._release_scheduler_lock(app, fd)
        self.assertFalse(lock_path.exists())

    def test_legacy_encrypted_payload_still_reads(self):
        payload_bytes = b"legacy payload bytes"
        manifest = {
            "backup_uid": "legacy",
            "backup_type": "COMPLETO",
            "payload_sha256": backup_service._sha256_bytes(payload_bytes),
        }
        nonce = b"1" * 12
        with self.app.app_context():
            encrypted = AESGCM(backup_service._encryption_key()).encrypt(
                nonce,
                payload_bytes,
                backup_service._manifest_aad(manifest),
            )
            manifest["encrypted_payload_sha256"] = backup_service._sha256_bytes(encrypted)
            manifest_hmac = backup_service.hmac.new(
                backup_service._hmac_key(),
                backup_service._canonical_json(manifest),
                backup_service.hashlib.sha256,
            ).hexdigest()

        package_path = self.root / "legacy.zip"
        with zipfile.ZipFile(package_path, "w", compression=zipfile.ZIP_DEFLATED) as package:
            package.writestr("MANIFESTO.json", json.dumps(manifest, ensure_ascii=False, indent=2))
            package.writestr("MANIFESTO.hmac", manifest_hmac)
            package.writestr("NONCE.bin", nonce)
            package.writestr("PAYLOAD.enc", encrypted)

        with self.app.app_context():
            restored = backup_service._decrypt_payload(package_path)

        self.assertEqual(restored, payload_bytes)


if __name__ == "__main__":
    unittest.main()
