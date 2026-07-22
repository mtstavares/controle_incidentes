import unittest
from datetime import datetime
from io import BytesIO
from unittest.mock import patch

import pandas as pd
from werkzeug.datastructures import FileStorage

from app import create_app, db, hash
from app.models import CredencialComprometida, User
from app.services.credential_service import import_credential_spreadsheet, is_valid_cpf, normalize_cpf


class TestConfig:
    TESTING = True
    SECRET_KEY = "test-secret"
    SQLALCHEMY_DATABASE_URI = "sqlite:///:memory:"
    SQLALCHEMY_TRACK_MODIFICATIONS = False
    RATELIMIT_ENABLED = False
    WTF_CSRF_ENABLED = False


class CompromisedCredentialsTest(unittest.TestCase):
    def setUp(self):
        self.app = create_app(TestConfig)
        self.ctx = self.app.app_context()
        self.ctx.push()
        db.create_all()
        db.session.add(User(
            username="admin",
            name="Admin",
            email="admin@test.com",
            profile="Admin",
            is_temp_password=False,
            must_change_password=False,
            password=hash("admin123"),
        ))
        db.session.add(User(
            username="user",
            name="User",
            email="user@test.com",
            profile="User",
            is_temp_password=False,
            must_change_password=False,
            password=hash("user123"),
        ))
        db.session.commit()
        self.client = self.app.test_client()

    def tearDown(self):
        db.session.remove()
        db.drop_all()
        self.ctx.pop()

    def login(self, username="admin", password="admin123"):
        return self.client.post("/login", data={"username": username, "password": password})

    def fake_upload(self):
        return FileStorage(stream=BytesIO(b"fake excel"), filename="credenciais.xlsx")

    def add_credential(self, *, cpf, nome="Pessoa Teste", email="pessoa@test.com", data_coleta=None):
        record = CredencialComprometida(
            nome=nome,
            nome_busca=nome.lower(),
            cpf=cpf,
            email=email,
            url_origem="https://origem.example/vazamento",
            data_coleta=data_coleta,
            permitiu_acesso=False,
            acesso_ad=False,
            acesso_ms=False,
            situacao_legal="Bloqueado",
            situacao_legal_normalizada="bloqueado",
            observacoes="Observação sintética",
            mensagem_bloqueio="Mensagem sintética",
        )
        db.session.add(record)
        db.session.commit()
        return record

    def valid_dataframe(self):
        return pd.DataFrame([{
            "NOME": " Pessoa Teste ",
            "CPF": "529.982.247-25",
            "EMAIL": " Pessoa@Test.COM ",
            "URL": "https://exemplo.invalid/vazamento",
            "DATA COLETA": "21/07/2026",
            "Permitiu acesso a alguma aplicação?": "SIM",
            "ACESSO AD": "SIM",
            "ACESSO MS": "NAO",
            "Situação legal": "Bloqueado",
            "OBSERVAÇÕES": "<script>alert(1)</script>",
            "MSG BLOQUEIO.": "=BLOQUEAR",
            "SENHA": "SuperSecreta!123",
        }])

    def test_import_ignores_password_column_and_persists_safe_fields(self):
        with patch("app.services.credential_service._read_spreadsheet", return_value=self.valid_dataframe()):
            summary = import_credential_spreadsheet(self.fake_upload(), user_id=1)
            db.session.commit()

        self.assertTrue(summary.ignored_password_column)
        self.assertEqual(summary.imported, 1)
        record = CredencialComprometida.query.one()
        self.assertEqual(record.cpf, "52998224725")
        self.assertEqual(record.email, "pessoa@test.com")
        self.assertTrue(record.acesso_ad)
        self.assertFalse(record.acesso_ms)
        self.assertTrue(record.permitiu_acesso)
        self.assertEqual(record.mensagem_bloqueio, "'=BLOQUEAR")
        self.assertNotIn("SuperSecreta", str(record.__dict__))

    def test_route_import_audits_summary_without_password_content(self):
        with patch("app.services.credential_service._read_spreadsheet", return_value=self.valid_dataframe()):
            summary = import_credential_spreadsheet(self.fake_upload(), user_id=1)
            db.session.commit()

        self.assertEqual(summary.imported, 1)
        self.login()
        response = self.client.get("/credenciais-comprometidas")
        html = response.get_data(as_text=True)
        self.assertEqual(response.status_code, 200)
        self.assertNotIn("SuperSecreta", html)

    def test_manual_import_route_is_not_exposed_to_users(self):
        self.login("user", "user123")
        self.assertEqual(self.client.get("/credenciais-comprometidas").status_code, 200)
        response = self.client.post(
            "/credenciais-comprometidas/importar",
            data={"arquivo": (BytesIO(b"fake excel"), "credenciais.xlsx")},
            content_type="multipart/form-data",
        )
        self.assertEqual(response.status_code, 404)

    def test_api_filters_search_access_period_and_situation(self):
        self.login()
        with patch("app.services.credential_service._read_spreadsheet", return_value=self.valid_dataframe()):
            import_credential_spreadsheet(self.fake_upload(), user_id=1)
            db.session.commit()

        response = self.client.get(
            "/api/credenciais-comprometidas?q=529.982&access=somente_ad&start_date=2026-07-01&end_date=2026-07-31&situacao=bloqueado"
        )
        payload = response.get_json()
        self.assertEqual(response.status_code, 200)
        self.assertEqual(payload["meta"]["total"], 1)
        self.assertEqual(payload["data"][0]["cpf"], "529.982.247-25")
        self.assertEqual(payload["data"][0]["data_coleta"], "21/07/2026")
        self.assertNotIn("SuperSecreta", str(payload))
        self.assertNotIn("url", payload["data"][0])

        invalid = self.client.get("/api/credenciais-comprometidas?start_date=2026-08-01&end_date=2026-07-01")
        self.assertEqual(invalid.status_code, 400)

    def test_duplicate_import_updates_without_duplicate_or_empty_overwrite(self):
        first = self.valid_dataframe()
        second = self.valid_dataframe()
        second.loc[0, "OBSERVAÇÕES"] = ""
        second.loc[0, "MSG BLOQUEIO."] = "Bloqueio atualizado"
        with patch("app.services.credential_service._read_spreadsheet", return_value=first):
            summary = import_credential_spreadsheet(self.fake_upload(), user_id=1)
            db.session.commit()
        self.assertEqual(summary.imported, 1)
        with patch("app.services.credential_service._read_spreadsheet", return_value=second):
            summary = import_credential_spreadsheet(self.fake_upload(), user_id=1)
            db.session.commit()
        self.assertEqual(summary.updated, 1)
        self.assertEqual(CredencialComprometida.query.count(), 1)
        record = CredencialComprometida.query.one()
        self.assertEqual(record.mensagem_bloqueio, "Bloqueio atualizado")
        self.assertEqual(record.observacoes, "<script>alert(1)</script>")

    def test_validation_rejects_invalid_rows_without_interrupting_valid_rows(self):
        df = self.valid_dataframe()
        invalid = df.iloc[0].copy()
        invalid["CPF"] = "111.111.111-11"
        invalid["EMAIL"] = "invalido"
        df = pd.concat([df, pd.DataFrame([invalid])], ignore_index=True)
        with patch("app.services.credential_service._read_spreadsheet", return_value=df):
            summary = import_credential_spreadsheet(self.fake_upload(), user_id=1)
            db.session.commit()
        self.assertEqual(summary.imported, 1)
        self.assertEqual(summary.rejected, 1)
        self.assertEqual(CredencialComprometida.query.count(), 1)

    def test_missing_email_is_persisted_as_not_found(self):
        df = self.valid_dataframe()
        df.loc[0, "EMAIL"] = ""
        with patch("app.services.credential_service._read_spreadsheet", return_value=df):
            summary = import_credential_spreadsheet(self.fake_upload(), user_id=1)
            db.session.commit()

        self.assertEqual(summary.imported, 1)
        self.assertEqual(summary.rejected, 0)
        self.assertEqual(CredencialComprometida.query.one().email, "e-mail não localizado")

    def test_cpf_normalization_and_validation(self):
        self.assertEqual(normalize_cpf("052.998.224-725"), "052998224725")
        self.assertTrue(is_valid_cpf("52998224725"))
        self.assertFalse(is_valid_cpf("11111111111"))

    def test_credentials_dashboard_empty_database_returns_zero_months(self):
        self.login()
        response = self.client.get("/api/dashboard/credenciais?year=2026&month=all")
        payload = response.get_json()

        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(payload["data"]), 12)
        self.assertTrue(all(item["total"] == 0 for item in payload["data"]))
        self.assertEqual(payload["data"][0]["monthName"], "Janeiro")
        self.assertNotIn("cpf", str(payload).lower())
        self.assertNotIn("email", str(payload).lower())

    def test_credentials_dashboard_groups_by_month_and_keeps_zero_months(self):
        self.login()
        self.add_credential(cpf="52998224725", data_coleta=datetime(2026, 1, 10, 8, 30, 0))
        self.add_credential(cpf="16899535009", email="outra@test.com", data_coleta=datetime(2026, 1, 20, 9, 0, 0))
        self.add_credential(cpf="11144477735", email="marco@test.com", data_coleta=datetime(2026, 3, 1, 0, 0, 0))
        self.add_credential(cpf="39053344705", email="outroano@test.com", data_coleta=datetime(2025, 1, 1, 0, 0, 0))

        response = self.client.get("/api/dashboard/credenciais?year=2026&month=all")
        payload = response.get_json()
        totals = {item["month"]: item["total"] for item in payload["data"]}

        self.assertEqual(response.status_code, 200)
        self.assertEqual(totals[1], 2)
        self.assertEqual(totals[2], 0)
        self.assertEqual(totals[3], 1)
        self.assertEqual(sum(totals.values()), 3)

    def test_credentials_dashboard_filters_specific_month(self):
        self.login()
        self.add_credential(cpf="52998224725", data_coleta=datetime(2026, 7, 2, 12, 0, 0))
        self.add_credential(cpf="16899535009", email="agosto@test.com", data_coleta=datetime(2026, 8, 2, 12, 0, 0))

        response = self.client.get("/api/dashboard/credenciais?year=2026&month=7")
        payload = response.get_json()

        self.assertEqual(response.status_code, 200)
        self.assertEqual(payload["data"], [{"month": 7, "monthName": "Julho", "year": 2026, "total": 1}])

    def test_credentials_dashboard_reflects_insert_update_and_delete(self):
        self.login()
        record = self.add_credential(cpf="52998224725", data_coleta=datetime(2026, 5, 1, 0, 0, 0))

        first = self.client.get("/api/dashboard/credenciais?year=2026&month=5").get_json()
        self.assertEqual(first["data"][0]["total"], 1)

        record.data_coleta = datetime(2026, 6, 1, 0, 0, 0)
        db.session.commit()
        may = self.client.get("/api/dashboard/credenciais?year=2026&month=5").get_json()
        june = self.client.get("/api/dashboard/credenciais?year=2026&month=6").get_json()
        self.assertEqual(may["data"][0]["total"], 0)
        self.assertEqual(june["data"][0]["total"], 1)

        db.session.delete(record)
        db.session.commit()
        deleted = self.client.get("/api/dashboard/credenciais?year=2026&month=6").get_json()
        self.assertEqual(deleted["data"][0]["total"], 0)

    def test_credentials_dashboard_rejects_malformed_filters(self):
        self.login()
        self.assertEqual(self.client.get("/api/dashboard/credenciais?year=2026;drop&month=all").status_code, 400)
        self.assertEqual(self.client.get("/api/dashboard/credenciais?year=2026&month=13").status_code, 400)
        self.assertEqual(self.client.get("/api/dashboard/credenciais?year=2026&month=all&sort=cpf").status_code, 400)

    def test_credentials_dashboard_requires_login(self):
        response = self.client.get("/dashboard-credenciais")
        self.assertEqual(response.status_code, 302)
        response = self.client.get("/api/dashboard/credenciais?year=2026&month=all")
        self.assertEqual(response.status_code, 302)


if __name__ == "__main__":
    unittest.main()
