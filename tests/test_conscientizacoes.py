import io
import shutil
import tempfile
import unittest
from pathlib import Path

from app import create_app, db, hash
from app.models import AuditLog, ConscientizacaoCampanha, User


PNG_1X1 = (
    b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01"
    b"\x00\x00\x00\x01\x08\x06\x00\x00\x00\x1f\x15\xc4\x89"
    b"\x00\x00\x00\nIDATx\x9cc\xf8\x0f\x00\x01\x01\x01\x00"
    b"\x18\xdd\x8d\xb0\x00\x00\x00\x00IEND\xaeB`\x82"
)


class TestConfig:
    TESTING = True
    SECRET_KEY = "test-secret"
    SQLALCHEMY_DATABASE_URI = "sqlite:///:memory:"
    SQLALCHEMY_TRACK_MODIFICATIONS = False
    RATELIMIT_ENABLED = False
    WTF_CSRF_ENABLED = False
    MAX_AWARENESS_IMAGE_SIZE = 1024 * 1024


class ConscientizacoesTest(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.mkdtemp()
        TestConfig.AWARENESS_UPLOAD_FOLDER = str(Path(self.temp_dir) / "conscientizacoes")
        self.app = create_app(TestConfig)
        self.ctx = self.app.app_context()
        self.ctx.push()
        db.create_all()
        for username, profile in [
            ("admin", "Admin"),
            ("user", "User"),
            ("viewer", "Viewer"),
        ]:
            db.session.add(User(
                username=username,
                name=f"{profile} Teste",
                email=f"{username}@test.local",
                profile=profile,
                is_temp_password=False,
                must_change_password=False,
                password=hash(f"{username}123"),
            ))
        db.session.commit()
        self.client = self.app.test_client()

    def tearDown(self):
        db.session.remove()
        db.drop_all()
        self.ctx.pop()
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def login(self, username):
        response = self.client.post(
            "/login",
            data={"username": username, "password": f"{username}123"},
            follow_redirects=True,
        )
        self.assertEqual(response.status_code, 200)

    def image_file(self, filename="campanha.png", content=PNG_1X1, mime="image/png"):
        return (io.BytesIO(content), filename, mime)

    def create_campaign(self, title="Campanha válida", date="2026-07-22", filename="campanha.png"):
        return self.client.post(
            "/conscientizacoes",
            data={
                "titulo": title,
                "data_publicacao": date,
                "imagem": self.image_file(filename=filename),
            },
            content_type="multipart/form-data",
            follow_redirects=True,
        )

    def test_admin_and_user_can_create_campaign(self):
        self.login("admin")
        response = self.create_campaign(title="Phishing çã")
        self.assertEqual(response.status_code, 200)
        self.assertIn("Phishing çã".encode("utf-8"), response.data)

        self.client.get("/logout")
        self.login("user")
        response = self.create_campaign(title="Campanha do usuário", filename="usuario.png")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(ConscientizacaoCampanha.query.count(), 2)

    def test_viewer_can_view_but_cannot_create_edit_or_delete(self):
        self.login("admin")
        self.create_campaign()
        campaign = ConscientizacaoCampanha.query.first()
        self.client.get("/logout")

        self.login("viewer")
        response = self.client.get("/conscientizacoes")
        self.assertEqual(response.status_code, 200)
        self.assertIn(b"Campanha v", response.data)
        self.assertNotIn(b"Nova campanha", response.data)

        create_response = self.create_campaign(title="Negada", filename="negada.png")
        self.assertEqual(create_response.status_code, 403)
        edit_response = self.client.post(
            f"/conscientizacoes/{campaign.id}/editar",
            data={"titulo": "Editada", "data_publicacao": "2026-07-23"},
        )
        self.assertEqual(edit_response.status_code, 403)
        delete_response = self.client.post(f"/conscientizacoes/{campaign.id}/excluir")
        self.assertEqual(delete_response.status_code, 403)

    def test_required_fields_and_invalid_files_are_rejected(self):
        self.login("admin")
        empty_title = self.client.post(
            "/conscientizacoes",
            data={"titulo": " ", "data_publicacao": "2026-07-22", "imagem": self.image_file()},
            content_type="multipart/form-data",
            follow_redirects=True,
        )
        self.assertIn("Informe o título".encode("utf-8"), empty_title.data)

        missing_image = self.client.post(
            "/conscientizacoes",
            data={"titulo": "Sem imagem", "data_publicacao": "2026-07-22"},
            follow_redirects=True,
        )
        self.assertIn("Selecione uma imagem".encode("utf-8"), missing_image.data)

        bad_extension = self.client.post(
            "/conscientizacoes",
            data={
                "titulo": "Formato ruim",
                "data_publicacao": "2026-07-22",
                "imagem": self.image_file(filename="arquivo.gif", mime="image/gif"),
            },
            content_type="multipart/form-data",
            follow_redirects=True,
        )
        self.assertIn("Formato de imagem não permitido".encode("utf-8"), bad_extension.data)

        renamed_payload = self.client.post(
            "/conscientizacoes",
            data={
                "titulo": "Renomeado",
                "data_publicacao": "2026-07-22",
                "imagem": self.image_file(filename="../../evil.jpg", content=b"<?php echo 1; ?>", mime="image/jpeg"),
            },
            content_type="multipart/form-data",
            follow_redirects=True,
        )
        self.assertIn("Imagem inválida ou corrompida".encode("utf-8"), renamed_payload.data)
        self.assertEqual(ConscientizacaoCampanha.query.count(), 0)

    def test_file_above_limit_is_rejected(self):
        self.app.config["MAX_AWARENESS_IMAGE_SIZE"] = 10
        self.login("admin")
        response = self.create_campaign()
        self.assertIn("A imagem excede o limite permitido".encode("utf-8"), response.data)
        self.assertEqual(ConscientizacaoCampanha.query.count(), 0)

    def test_listing_order_and_expand_markup(self):
        self.login("admin")
        self.create_campaign(title="Mais antiga", date="2026-01-01")
        self.create_campaign(title="Mais recente", date="2026-07-22", filename="recente.png")

        response = self.client.get("/conscientizacoes")
        self.assertEqual(response.status_code, 200)
        self.assertLess(response.data.index(b"Mais recente"), response.data.index(b"Mais antiga"))
        self.assertIn(b"data-awareness-open=\"awareness-view-", response.data)

    def test_admin_can_edit_replace_image_and_delete_campaign(self):
        self.login("admin")
        self.create_campaign()
        campaign = ConscientizacaoCampanha.query.first()
        old_file = campaign.imagem_arquivo
        old_path = Path(self.app.config["AWARENESS_UPLOAD_FOLDER"]) / old_file
        self.assertTrue(old_path.exists())

        edit_response = self.client.post(
            f"/conscientizacoes/{campaign.id}/editar",
            data={
                "titulo": "Campanha editada",
                "data_publicacao": "2026-07-23",
                "imagem": self.image_file(filename="nova.png"),
            },
            content_type="multipart/form-data",
            follow_redirects=True,
        )
        self.assertEqual(edit_response.status_code, 200)
        db.session.refresh(campaign)
        self.assertEqual(campaign.titulo, "Campanha editada")
        self.assertNotEqual(campaign.imagem_arquivo, old_file)
        self.assertFalse(old_path.exists())
        self.assertTrue((Path(self.app.config["AWARENESS_UPLOAD_FOLDER"]) / campaign.imagem_arquivo).exists())

        image_response = self.client.get(f"/conscientizacoes/{campaign.id}/imagem")
        self.assertEqual(image_response.status_code, 200)
        self.assertEqual(image_response.headers["X-Content-Type-Options"], "nosniff")
        image_response.close()

        new_path = Path(self.app.config["AWARENESS_UPLOAD_FOLDER"]) / campaign.imagem_arquivo
        delete_response = self.client.post(f"/conscientizacoes/{campaign.id}/excluir", follow_redirects=True)
        self.assertEqual(delete_response.status_code, 200)
        self.assertEqual(ConscientizacaoCampanha.query.count(), 0)
        self.assertFalse(new_path.exists())

    def test_audit_logs_do_not_store_binary_or_absolute_path(self):
        self.login("admin")
        self.create_campaign(title="Auditoria")
        campaign = ConscientizacaoCampanha.query.first()
        self.client.post(f"/conscientizacoes/{campaign.id}/excluir", follow_redirects=True)

        logs = AuditLog.query.filter_by(entidade="ConscientizacaoCampanha").all()
        self.assertGreaterEqual(len(logs), 2)
        serialized = str([(log.descricao, log.alteracoes) for log in logs])
        self.assertIn("Auditoria", serialized)
        self.assertNotIn(self.temp_dir, serialized)
        self.assertNotIn(str(PNG_1X1[:10]), serialized)

    def test_related_routes_still_load(self):
        self.login("admin")
        for path in ["/", "/credenciais-comprometidas", "/dashboard-credenciais"]:
            response = self.client.get(path)
            self.assertLess(response.status_code, 500)


if __name__ == "__main__":
    unittest.main()
