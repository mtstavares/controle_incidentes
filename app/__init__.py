import hashlib
import hmac
import logging
import os
import secrets
import uuid
from logging.handlers import RotatingFileHandler

from flask import Flask, abort, g, redirect, render_template, request, session, url_for
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address
from flask_login import LoginManager, current_user
from flask_migrate import Migrate
from flask_sqlalchemy import SQLAlchemy

from config import ProductionConfig


def hash(txt):
    hash_obj = hashlib.sha256(txt.encode("utf-8"))
    return hash_obj.hexdigest()


db = SQLAlchemy()
lm = LoginManager()
migrate = Migrate()
limiter = Limiter(key_func=get_remote_address, default_limits=[])


def _configure_file_logging(app):
    if app.debug or app.testing:
        return

    os.makedirs("logs", exist_ok=True)
    file_handler = RotatingFileHandler(
        "logs/app.log",
        maxBytes=1024 * 1024 * 10,
        backupCount=10,
        encoding="utf-8",
    )
    file_handler.setLevel(logging.INFO)
    file_handler.setFormatter(logging.Formatter(
        "%(asctime)s [%(levelname)s] %(module)s: %(message)s [in %(pathname)s:%(lineno)d]"
    ))
    app.logger.addHandler(file_handler)
    app.logger.setLevel(logging.INFO)


def _content_security_policy():
    # Mantém compatibilidade com Bootstrap CDN, estilos inline legados e scripts inline existentes.
    return "; ".join([
        "default-src 'self'",
        "script-src 'self' 'unsafe-inline' https://cdn.jsdelivr.net",
        "style-src 'self' 'unsafe-inline' https://cdn.jsdelivr.net",
        "font-src 'self' https://cdn.jsdelivr.net data:",
        "img-src 'self' data:",
        "connect-src 'self'",
        "object-src 'none'",
        "base-uri 'self'",
        "form-action 'self'",
        "frame-ancestors 'none'",
    ])


def create_app(config_class=ProductionConfig):
    app = Flask(__name__)
    if hasattr(config_class, "validate"):
        config_class.validate()
    app.config.from_object(config_class)
    app.config.setdefault("JSON_AS_ASCII", False)
    app.config.setdefault("JSONIFY_MIMETYPE", "application/json; charset=utf-8")
    app.config.setdefault("TIMEZONE", "America/Sao_Paulo")
    app.config["SESSION_COOKIE_HTTPONLY"] = app.config.get("SESSION_COOKIE_HTTPONLY", True)
    app.config["SESSION_COOKIE_SAMESITE"] = app.config.get("SESSION_COOKIE_SAMESITE") or "Lax"
    app.config["SESSION_COOKIE_SECURE"] = bool(app.config.get("SESSION_COOKIE_SECURE", False))
    os.environ.setdefault("TIMEZONE", app.config["TIMEZONE"])
    if hasattr(app, "json"):
        app.json.ensure_ascii = False

    os.makedirs(app.instance_path, exist_ok=True)
    os.makedirs("logs", exist_ok=True)

    db.init_app(app)
    migrate.init_app(app, db)
    lm.init_app(app)
    limiter.init_app(app)

    from app.blueprints.main import main_bp
    from app.blueprints.incidente import incidente_bp
    from app.blueprints.users import users_bp
    from app.blueprints.credenciais import credenciais_bp
    from app.blueprints.dashboard import dashboard_bp
    from app.blueprints.admin import admin_bp

    app.register_blueprint(main_bp)
    app.register_blueprint(incidente_bp)
    app.register_blueprint(users_bp)
    app.register_blueprint(credenciais_bp)
    app.register_blueprint(dashboard_bp)
    app.register_blueprint(admin_bp)

    lm.login_view = "users.login"
    lm.login_message = ""

    def get_csrf_token():
        token = session.get("_csrf_token")
        if not token:
            token = secrets.token_urlsafe(32)
            session["_csrf_token"] = token
        return token

    @app.context_processor
    def inject_csrf_token():
        return {"csrf_token": get_csrf_token}

    @app.before_request
    def enforce_csrf_and_password_change():
        g.request_id = request.headers.get("X-Request-ID") or uuid.uuid4().hex
        if request.method in {"POST", "PUT", "PATCH", "DELETE"} and not app.testing:
            sent_token = request.form.get("_csrf_token") or request.headers.get("X-CSRF-Token")
            expected_token = session.get("_csrf_token")
            if not sent_token or not expected_token or not hmac.compare_digest(sent_token, expected_token):
                abort(400)

        endpoint = request.endpoint or ""
        allowed_endpoints = {
            "users.change_password",
            "users.login",
            "main.logout",
            "static",
        }
        if endpoint.endswith(".static"):
            return None
        if (
            current_user.is_authenticated
            and (getattr(current_user, "must_change_password", False) or getattr(current_user, "is_temp_password", False))
            and endpoint not in allowed_endpoints
        ):
            return redirect(url_for("users.change_password"))
        return None

    @app.after_request
    def harden_response_headers(response):
        content_type = response.headers.get("Content-Type", "")
        if content_type.startswith("text/html") and "charset=" not in content_type.lower():
            response.headers["Content-Type"] = "text/html; charset=utf-8"
        elif content_type.startswith("application/json") and "charset=" not in content_type.lower():
            response.headers["Content-Type"] = "application/json; charset=utf-8"

        response.headers.setdefault("X-Request-ID", getattr(g, "request_id", ""))
        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        response.headers.setdefault("X-Frame-Options", "DENY")
        response.headers.setdefault("Referrer-Policy", "strict-origin-when-cross-origin")
        response.headers.setdefault("Permissions-Policy", "camera=(), microphone=(), geolocation=()")
        response.headers.setdefault("Content-Security-Policy", _content_security_policy())
        if current_user.is_authenticated:
            response.headers.setdefault("Cache-Control", "no-store, private")
            response.headers.setdefault("Pragma", "no-cache")
        if request.is_secure:
            response.headers.setdefault("Strict-Transport-Security", "max-age=31536000; includeSubDomains")
        return response

    @app.template_filter("sp_datetime")
    def sp_datetime(value):
        from app.services.timezone_service import format_local_datetime

        return format_local_datetime(value)

    _configure_file_logging(app)

    @app.errorhandler(403)
    def forbidden(error):
        return render_template(
            "errors/error.html",
            title="Acesso negado",
            code=403,
            message="Você não tem permissão para acessar este recurso.",
        ), 403

    @app.errorhandler(404)
    def page_not_found(error):
        return render_template(
            "errors/error.html",
            title="Página não encontrada",
            code=404,
            message="A página solicitada não foi encontrada.",
        ), 404

    @app.errorhandler(500)
    def internal_error(error):
        return render_template(
            "errors/error.html",
            title="Erro interno",
            code=500,
            message="Não foi possível concluir a operação. Tente novamente mais tarde.",
        ), 500

    return app
