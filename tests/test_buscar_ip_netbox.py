import unittest
from datetime import timedelta
from unittest.mock import patch

import requests

from app import create_app, db, hash
from app.models import AuditLog, User
from app.services.netbox_service import clear_netbox_cache


class TestConfig:
    TESTING = True
    SECRET_KEY = "test-secret"
    SQLALCHEMY_DATABASE_URI = "sqlite:///:memory:"
    SQLALCHEMY_TRACK_MODIFICATIONS = False
    RATELIMIT_ENABLED = False
    WTF_CSRF_ENABLED = False
    NETBOX_API_BASE_URL = "https://netbox.test/api"
    NETBOX_API_TOKEN = "token-test"
    NETBOX_API_TIMEOUT = 2
    NETBOX_API_VERIFY_TLS = False
    NETBOX_SEARCH_CACHE_TTL_SECONDS = 300
    INTERNAL_API_BASE_URLS = {"netbox": NETBOX_API_BASE_URL}
    INTERNAL_API_TIMEOUTS = {"netbox": NETBOX_API_TIMEOUT}
    INTERNAL_API_VERIFY_TLS = {"netbox": NETBOX_API_VERIFY_TLS}
    INTERNAL_API_TOKENS = {"netbox": NETBOX_API_TOKEN}
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

    def close(self):
        pass

    def get(self, url, params=None, headers=None, timeout=None, verify=None):
        self.calls.append({"url": url, "params": params, "headers": headers, "timeout": timeout, "verify": verify})
        for key, response in self.responses.items():
            if key in url:
                if isinstance(response, Exception):
                    raise response
                return response
        return FakeResponse(404, {"results": []})


def netbox_responses():
    return {
        "/ipam/ip-addresses/": FakeResponse(
            payload={
                "count": 1,
                "results": [
                    {
                        "address": "10.61.9.44/24",
                        "status": {"label": "Ativo"},
                        "dns_name": "host.local",
                        "description": "Servidor de teste",
                        "role": {"name": "Servidor"},
                        "tenant": {"name": "PMESP"},
                        "vrf": {"name": "Intranet"},
                        "assigned_object": {"name": "eth0", "device": {"display": "SW-01"}},
                    }
                ],
            }
        ),
        "/ipam/prefixes/": FakeResponse(
            payload={
                "count": 1,
                "results": [
                    {
                        "prefix": "10.61.9.0/24",
                        "status": {"label": "Ativo"},
                        "site": {"name": "PMESP"},
                        "vlan": {"display": "Front_Intranet (418)"},
                        "description": "Front_Intranet",
                        "tenant": {"name": "PMESP"},
                        "vrf": {"name": "Intranet"},
                    }
                ],
            }
        ),
    }


class BuscarIPNetBoxTest(unittest.TestCase):
    def setUp(self):
        FakeSession.instances = []
        clear_netbox_cache()
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
        clear_netbox_cache()
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
        return patch("app.services.internal_api.requests.Session", lambda: FakeSession(responses))

    def test_navbar_and_page_are_available_for_authenticated_users(self):
        self.login("viewer")
        response = self.client.get("/utilitarios/buscar-ip")
        html = response.get_data(as_text=True)
        self.assertEqual(response.status_code, 200)
        self.assertIn("Buscar IP", html)
        self.assertIn("Digite o IP para consultar no NetBox", html)

    def test_admin_can_search_ip(self):
        self.login("admin")
        with self.patch_session(netbox_responses()):
            response = self.client.post("/utilitarios/buscar-ip", data={"query": "10.61.9.44"})

        html = response.get_data(as_text=True)
        self.assertEqual(response.status_code, 200)
        self.assertIn("10.61.9.44/24", html)
        self.assertIn("10.61.9.0/24", html)
        self.assertIn("Front_Intranet", html)
        self.assertNotIn("netbox.test", html)
        self.assertNotIn("token-test", html)
        self.assertEqual(len(FakeSession.instances[0].calls), 2)
        self.assertTrue(all(call["headers"]["Authorization"] == "Token token-test" for call in FakeSession.instances[0].calls))
        self.assertTrue(all(call["verify"] is False for call in FakeSession.instances[0].calls))
        self.assertIsNotNone(AuditLog.query.filter_by(entidade="ConsultaNetBox", resultado="SUCESSO").first())

    def test_user_can_search_ip(self):
        self.login("user")
        with self.patch_session(netbox_responses()):
            response = self.client.post("/utilitarios/buscar-ip", data={"query": "10.61.9.44"})

        self.assertEqual(response.status_code, 200)

    def test_viewer_is_blocked_without_calling_netbox(self):
        self.login("viewer")
        with self.patch_session(netbox_responses()):
            response = self.client.post("/utilitarios/buscar-ip", data={"query": "10.61.9.44"})

        html = response.get_data(as_text=True)
        self.assertEqual(response.status_code, 403)
        self.assertIn("permissão de visualização", html)
        self.assertEqual(FakeSession.instances, [])
        self.assertIsNotNone(AuditLog.query.filter_by(entidade="ConsultaNetBox", resultado="NEGADO").first())

    def test_invalid_ip_does_not_call_netbox(self):
        self.login("admin")
        with self.patch_session(netbox_responses()):
            response = self.client.post("/utilitarios/buscar-ip", data={"query": "10.999.9.44"})

        self.assertEqual(response.status_code, 400)
        self.assertIn("IP inválido", response.get_data(as_text=True))
        self.assertEqual(FakeSession.instances, [])

    def test_empty_result_is_rendered(self):
        responses = {
            "/ipam/ip-addresses/": FakeResponse(payload={"count": 0, "results": []}),
            "/ipam/prefixes/": FakeResponse(payload={"count": 0, "results": []}),
        }
        self.login("admin")
        with self.patch_session(responses):
            response = self.client.post("/utilitarios/buscar-ip", data={"query": "10.61.9.44"})

        self.assertEqual(response.status_code, 200)
        self.assertIn("Nenhum registro encontrado", response.get_data(as_text=True))

    def test_timeout_is_handled_without_stack_trace(self):
        responses = {"/ipam/ip-addresses/": requests.Timeout("timeout")}
        self.login("admin")
        with self.patch_session(responses):
            response = self.client.post("/utilitarios/buscar-ip", data={"query": "10.61.9.44"})

        html = response.get_data(as_text=True)
        self.assertEqual(response.status_code, 200)
        self.assertIn("Tempo limite excedido", html)
        self.assertNotIn("Traceback", html)

    def test_cache_avoids_repeated_ip_queries(self):
        self.login("admin")
        with self.patch_session(netbox_responses()):
            first = self.client.post("/utilitarios/buscar-ip", data={"query": "10.61.9.44"})
            second = self.client.post("/utilitarios/buscar-ip", data={"query": "10.61.9.44"})

        self.assertEqual(first.status_code, 200)
        self.assertEqual(second.status_code, 200)
        self.assertEqual(len(FakeSession.instances), 1)
        self.assertEqual(len(FakeSession.instances[0].calls), 2)
