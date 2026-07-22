import unittest
from unittest.mock import patch

from app import create_app, db, hash
from app.models import User
from config import ProductionConfig


class TestConfig:
    TESTING = True
    SECRET_KEY = "test-secret"
    SQLALCHEMY_DATABASE_URI = "sqlite:///:memory:"
    SQLALCHEMY_TRACK_MODIFICATIONS = False
    RATELIMIT_ENABLED = False
    WTF_CSRF_ENABLED = False


class SecurityHardeningTest(unittest.TestCase):
    def setUp(self):
        self.app = create_app(TestConfig)
        self.ctx = self.app.app_context()
        self.ctx.push()
        db.create_all()
        db.session.add(User(
            username="admin",
            name="Admin Teste",
            email="admin@test",
            profile="Admin",
            is_temp_password=False,
            must_change_password=False,
            password=hash("admin123"),
        ))
        db.session.commit()
        self.client = self.app.test_client()

    def tearDown(self):
        db.session.remove()
        db.drop_all()
        self.ctx.pop()

    def test_security_headers_are_present(self):
        response = self.client.get("/login")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.headers["X-Content-Type-Options"], "nosniff")
        self.assertEqual(response.headers["X-Frame-Options"], "DENY")
        self.assertEqual(response.headers["Referrer-Policy"], "strict-origin-when-cross-origin")
        self.assertIn("frame-ancestors 'none'", response.headers["Content-Security-Policy"])
        self.assertIn("camera=()", response.headers["Permissions-Policy"])

    def test_authenticated_pages_are_not_cached(self):
        self.client.post("/login", data={"username": "admin", "password": "admin123"})
        response = self.client.get("/")

        self.assertEqual(response.status_code, 200)
        self.assertIn("no-store", response.headers["Cache-Control"])
        self.assertEqual(response.headers["Pragma"], "no-cache")

    def test_session_cookie_is_httponly_and_samesite(self):
        response = self.client.post("/login", data={"username": "admin", "password": "admin123"})
        cookie_headers = response.headers.getlist("Set-Cookie")
        session_cookie = next(header for header in cookie_headers if header.startswith("session="))

        self.assertIn("HttpOnly", session_cookie)
        self.assertIn("SameSite=Lax", session_cookie)

    def test_production_config_fails_without_required_environment(self):
        with patch.dict("os.environ", {}, clear=True):
            with self.assertRaises(ValueError):
                ProductionConfig.validate()


if __name__ == "__main__":
    unittest.main()
