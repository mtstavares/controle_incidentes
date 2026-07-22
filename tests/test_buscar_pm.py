import base64
import unittest
from datetime import timedelta
from unittest.mock import patch

import requests

from app import create_app, db, hash
from app.models import AuditLog, User
from app.services.buscar_pm_service import clear_pm_cache


class TestConfig:
    TESTING = True
    SECRET_KEY = "test-secret"
    SQLALCHEMY_DATABASE_URI = "sqlite:///:memory:"
    SQLALCHEMY_TRACK_MODIFICATIONS = False
    RATELIMIT_ENABLED = False
    WTF_CSRF_ENABLED = False
    PM_API_BASE_URL = "https://api.test/PolicialMilitar"
    PM_API_TIMEOUT = 2
    PM_SEARCH_CACHE_TTL_SECONDS = 300
    PERMANENT_SESSION_LIFETIME = timedelta(hours=5)
    SESSION_REFRESH_EACH_REQUEST = False


class FakeResponse:
    def __init__(self, status_code=200, payload=None, invalid_json=False):
        self.status_code = status_code
        self.payload = payload or {}
        self.invalid_json = invalid_json

    def json(self):
        if self.invalid_json:
            raise ValueError("invalid json")
        return self.payload


class FakeSession:
    instances = []

    def __init__(self, responses):
        self.responses = responses
        self.calls = []
        FakeSession.instances.append(self)

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def get(self, url, timeout=None, verify=None):
        self.calls.append({"url": url, "timeout": timeout, "verify": verify})
        for key, response in self.responses.items():
            if key in url:
                if isinstance(response, Exception):
                    raise response
                return response
        return FakeResponse(404, {"dados": []})


def dados_resumidos(cpf="12345678909", re_numero="123456", re_digito="7"):
    return {
        "dados": [
            {
                "re": {"numero": re_numero, "digito": re_digito},
                "cpf": {"cpfComDigito": cpf},
                "nomeCompleto": "João da Silva",
                "nomeGuerra": "SILVA",
                "posto": {"sigla": "SD"},
                "situacaoLegal": {"descricao": "Ativo"},
                "dataNascimento": "01/01/1990",
                "opm": {
                    "opmN02Des": "DIRETORIAS",
                    "opmN03Des": "DTIC",
                    "opmN04Des": "DAS",
                    "apelido": "SDSA",
                    "codigo": "123",
                },
            }
        ]
    }


def caracteristicas_payload():
    return {
        "dados": [
            {
                "estatura": "1.80",
                "cabelo": {"cor": "Preto", "tipo": "Liso"},
                "olhos": {"descricao": "Castanhos"},
                "cutis": {"descricaoCutis": "Branca"},
                "tipoSanguineo": {"tipo": "O", "fator": "+"},
            }
        ]
    }


def documentos_payload():
    return {
        "dados": [
            {
                "rg": {"numero": "12345678", "digito": "9", "uf": "SP"},
                "cnh": {"numero": "999", "categoria": "B", "dataExpiracao": "31/12/2030"},
            }
        ]
    }


def contato_payload():
    return {
        "dados": [
            {
                "emails": [{"endereco": "joao@policiamilitar.sp.gov.br"}],
                "telefones": [{"ddd": "11", "numero": "999999999"}],
            }
        ]
    }


def foto_payload():
    image = base64.b64encode(b"fake-jpeg").decode("ascii")
    return {"dados": [{"imagem": image}]}


def cpf_responses():
    return {
        "/cpf/12345678909/dadosResumidos": FakeResponse(payload=dados_resumidos()),
        "/cpf/12345678909/caracteristicaFisica": FakeResponse(payload=caracteristicas_payload()),
        "/cpf/12345678909/documentos": FakeResponse(payload=documentos_payload()),
        "/cpf/12345678909/informacaoContato": FakeResponse(payload=contato_payload()),
        "/cpf/12345678909/pesquisaFoto": FakeResponse(payload=foto_payload()),
    }


class BuscarPMTest(unittest.TestCase):
    def setUp(self):
        FakeSession.instances = []
        clear_pm_cache()
        self.app = create_app(TestConfig)
        self.ctx = self.app.app_context()
        self.ctx.push()
        db.create_all()
        for username, profile in [("admin", "Admin"), ("user", "User"), ("viewer", "Viewer")]:
            db.session.add(
                User(
                    username=username,
                    name=f"{profile} Teste",
                    email=f"{username}@test.local",
                    profile=profile,
                    is_temp_password=False,
                    must_change_password=False,
                    password=hash(f"{username}123"),
                )
            )
        db.session.commit()
        self.client = self.app.test_client()

    def tearDown(self):
        clear_pm_cache()
        db.session.remove()
        db.drop_all()
        self.ctx.pop()

    def login(self, username):
        response = self.client.post(
            "/login",
            data={"username": username, "password": f"{username}123"},
            follow_redirects=True,
        )
        self.assertEqual(response.status_code, 200)

    def patch_session(self, responses):
        return patch("app.services.buscar_pm_service.requests.Session", lambda: FakeSession(responses))

    def test_navbar_and_page_are_available_for_authenticated_users(self):
        self.login("viewer")
        response = self.client.get("/utilitarios/buscar-pm")
        html = response.get_data(as_text=True)
        self.assertEqual(response.status_code, 200)
        self.assertIn("Utilitários", html)
        self.assertIn("Buscar PM", html)
        self.assertIn("Digite o CPF ou RE do Policial Militar", html)

    def test_admin_can_search_by_cpf_and_view_photo(self):
        self.login("admin")
        with self.patch_session(cpf_responses()):
            response = self.client.post(
                "/utilitarios/buscar-pm",
                data={"query": "123.456.789-09"},
                follow_redirects=True,
            )

        html = response.get_data(as_text=True)
        self.assertEqual(response.status_code, 200)
        self.assertIn("João da Silva", html)
        self.assertIn("data:image/jpeg;base64,", html)
        self.assertIn("joao@policiamilitar.sp.gov.br", html)
        self.assertEqual(len(FakeSession.instances), 1)
        self.assertEqual(len(FakeSession.instances[0].calls), 5)
        self.assertTrue(all(call["verify"] is True for call in FakeSession.instances[0].calls))
        self.assertIsNotNone(AuditLog.query.filter_by(entidade="ConsultaPM", resultado="SUCESSO").first())

    def test_user_can_search_by_re_and_reuses_cpf_flow(self):
        responses = cpf_responses()
        responses["/re/123456/dadosResumidos"] = FakeResponse(payload=dados_resumidos())
        self.login("user")
        with self.patch_session(responses):
            response = self.client.post("/utilitarios/buscar-pm", data={"query": "123456"}, follow_redirects=True)

        self.assertEqual(response.status_code, 200)
        calls = [call["url"] for call in FakeSession.instances[0].calls]
        self.assertIn("https://api.test/PolicialMilitar/re/123456/dadosResumidos", calls[0])
        self.assertTrue(any("/cpf/12345678909/dadosResumidos" in url for url in calls))

    def test_viewer_is_blocked_in_backend_without_calling_api(self):
        self.login("viewer")
        with self.patch_session(cpf_responses()):
            response = self.client.post("/utilitarios/buscar-pm", data={"query": "12345678909"})

        html = response.get_data(as_text=True)
        self.assertEqual(response.status_code, 403)
        self.assertIn("Seu perfil possui apenas permissão de visualização", html)
        self.assertEqual(FakeSession.instances, [])
        self.assertIsNotNone(AuditLog.query.filter_by(entidade="ConsultaPM", resultado="NEGADO").first())

    def test_invalid_input_does_not_call_api(self):
        self.login("admin")
        with self.patch_session(cpf_responses()):
            response = self.client.post("/utilitarios/buscar-pm", data={"query": "abc123"})

        self.assertEqual(response.status_code, 400)
        self.assertIn("CPF ou RE inválido", response.get_data(as_text=True))
        self.assertEqual(FakeSession.instances, [])

    def test_photo_absence_is_rendered_without_failing(self):
        responses = cpf_responses()
        responses["/cpf/12345678909/pesquisaFoto"] = FakeResponse(404, {"dados": []})
        self.login("admin")
        with self.patch_session(responses):
            response = self.client.post("/utilitarios/buscar-pm", data={"query": "12345678909"})

        self.assertEqual(response.status_code, 200)
        html = response.get_data(as_text=True)
        self.assertIn("Sem foto", html)
        self.assertNotIn("data:image/jpeg;base64", html)

    def test_timeout_is_handled_without_stack_trace(self):
        responses = {
            "/cpf/12345678909/dadosResumidos": requests.Timeout("timeout"),
        }
        self.login("admin")
        with self.patch_session(responses):
            response = self.client.post("/utilitarios/buscar-pm", data={"query": "12345678909"})

        self.assertEqual(response.status_code, 200)
        html = response.get_data(as_text=True)
        self.assertIn("Tempo limite excedido", html)
        self.assertNotIn("Traceback", html)

    def test_certificate_error_has_specific_message(self):
        responses = {
            "/cpf/12345678909/dadosResumidos": requests.exceptions.SSLError("certificate verify failed"),
        }
        self.login("admin")
        with self.patch_session(responses):
            response = self.client.post("/utilitarios/buscar-pm", data={"query": "12345678909"})

        html = response.get_data(as_text=True)
        self.assertEqual(response.status_code, 200)
        self.assertIn("Não foi possível validar o certificado", html)
        self.assertIsNotNone(AuditLog.query.filter_by(entidade="ConsultaPM", resultado="ERRO_CERTIFICADO").first())

    def test_connection_error_has_specific_message(self):
        responses = {
            "/cpf/12345678909/dadosResumidos": requests.exceptions.ConnectionError("name resolution failed"),
        }
        self.login("admin")
        with self.patch_session(responses):
            response = self.client.post("/utilitarios/buscar-pm", data={"query": "12345678909"})

        html = response.get_data(as_text=True)
        self.assertEqual(response.status_code, 200)
        self.assertIn("Não foi possível conectar", html)
        self.assertIsNotNone(AuditLog.query.filter_by(entidade="ConsultaPM", resultado="ERRO_CONEXAO").first())

    def test_cache_avoids_repeated_cpf_queries(self):
        self.login("admin")
        with self.patch_session(cpf_responses()):
            first = self.client.post("/utilitarios/buscar-pm", data={"query": "12345678909"})
            second = self.client.post("/utilitarios/buscar-pm", data={"query": "12345678909"})

        self.assertEqual(first.status_code, 200)
        self.assertEqual(second.status_code, 200)
        self.assertEqual(len(FakeSession.instances), 2)
        self.assertEqual(len(FakeSession.instances[0].calls), 5)
        self.assertEqual(len(FakeSession.instances[1].calls), 0)
