# app/__init__.py

from flask import Flask, abort, g, redirect, render_template, request, session, url_for
from flask_sqlalchemy import SQLAlchemy
from flask_migrate import Migrate
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address
from config import DevelopmentConfig, ProductionConfig # Importando as classes de configuração do arquivo config.py
from flask_login import LoginManager, current_user, set_login_view # Importando o gerenciador de login
import hashlib
import hmac
import logging
from logging.handlers import RotatingFileHandler
import os
import secrets
import uuid
from flask_login import LoginManager, set_login_view # Importando o gerenciador de login
import hashlib

def hash(txt):
    hash_obj = hashlib.sha256(txt.encode('utf-8'))
    return hash_obj.hexdigest()
    
db = SQLAlchemy()
lm = LoginManager()
migrate = Migrate()
limiter = Limiter(key_func=get_remote_address, default_limits=[])

logging.basicConfig(level=logging.INFO, filename='app.log', format='%(asctime)s - %(levelname)s - %(message)s')
def create_app(config_class=ProductionConfig):
    
    app = Flask(__name__) # Criando uma instância do Flask
    if hasattr(config_class, "validate"):
        config_class.validate()
    app.config.from_object(config_class) # Carregando a configuração da classe fornecida > Desenvolvimento ou Produção
    app.config.setdefault("JSON_AS_ASCII", False)
    app.config.setdefault("JSONIFY_MIMETYPE", "application/json; charset=utf-8")
    app.config.setdefault("TIMEZONE", "America/Sao_Paulo")
    os.environ.setdefault("TIMEZONE", app.config["TIMEZONE"])
    if hasattr(app, "json"):
        app.json.ensure_ascii = False
    
    os.makedirs(app.instance_path, exist_ok=True)
    os.makedirs('logs', exist_ok=True)
    
    db.init_app(app) # Inicializando o SQLAlchemy com a aplicação Flask
    migrate.init_app(app, db)
    lm.init_app(app) # Inicializando o LoginManager com a aplicação Flask  
    limiter.init_app(app)
    # Carregando os blueprints
    from app.blueprints.main import main_bp # Importando o blueprint principal
    app.register_blueprint(main_bp) # Registrando o blueprint principal
    
    from app.blueprints.incidente import incidente_bp # Importando o blueprint análise
    app.register_blueprint(incidente_bp) # Registrando o blueprint análise com prefixo de URL
    
    from app.blueprints.users import users_bp # Importando o blueprint usuários
    app.register_blueprint(users_bp) # Registrando o blueprint usuários com prefixo de URL
    
    from app.blueprints.credenciais import credenciais_bp
    app.register_blueprint(credenciais_bp)
    
    from app.blueprints.dashboard import dashboard_bp
    app.register_blueprint(dashboard_bp)
    
    from app.blueprints.admin import admin_bp
    app.register_blueprint(admin_bp)
    
    lm.login_view = 'users.login' # Definindo a rota de login
    lm.login_message = "" # Evita mensagem inicial ao abrir a tela de login sem sessao
    
        
    #Implementar postertiormente manipuladores de erro personalizados
    # @app.errorhandler(404)
    # def page_not_found(e):
    #     return render_template('404.html'), 404
    
    
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
    def require_password_change():
        g.request_id = request.headers.get("X-Request-ID") or uuid.uuid4().hex
        if request.method in {"POST", "PUT", "PATCH", "DELETE"} and not app.testing:
            sent_token = request.form.get("_csrf_token") or request.headers.get("X-CSRF-Token")
            expected_token = session.get("_csrf_token")
            if not sent_token or not expected_token or not hmac.compare_digest(sent_token, expected_token):
                abort(400)
        
        allowed_endpoints = {
            "users.change_password",
            "users.login",
            "main.logout",
            "static",
        }
        endpoint = request.endpoint or ""
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
    def enforce_utf8_charset(response):
        content_type = response.headers.get("Content-Type", "")
        if content_type.startswith("text/html") and "charset=" not in content_type.lower():
            response.headers["Content-Type"] = "text/html; charset=utf-8"
        elif content_type.startswith("application/json") and "charset=" not in content_type.lower():
            response.headers["Content-Type"] = "application/json; charset=utf-8"
        response.headers.setdefault("X-Request-ID", getattr(g, "request_id", ""))
        return response

    @app.template_filter("sp_datetime")
    def sp_datetime(value):
        from app.services.timezone_service import format_local_datetime

        return format_local_datetime(value)
    
    # ====================================================================
    # CONFIGURAÇÃO DO LOGGING
    # ====================================================================

    if not app.debug and not app.testing:
        # Define o nível mínimo de log para o manipulador de arquivo
        log_level = logging.INFO
        
        # 1. Cria o manipulador de arquivo (Handler)
        # Rotaciona o arquivo quando ele atinge 10 MB e mantém 10 arquivos de backup
        file_handler = RotatingFileHandler(
            'logs/app.log', 
            maxBytes=1024 * 1024 * 10, # 10 MB
            backupCount=10,
            encoding='utf-8'
        )
        file_handler.setLevel(log_level)
        
        # 2. Define o formato da mensagem de log
        formatter = logging.Formatter(
            '%(asctime)s [%(levelname)s] %(module)s: %(message)s [in %(pathname)s:%(lineno)d]'
        )
        file_handler.setFormatter(formatter)
        
        # 3. Adiciona o manipulador ao logger da aplicação
        app.logger.addHandler(file_handler)
        app.logger.setLevel(log_level)
        
        # Opcional: Configurar o log para o console (útil em ambientes de produção)
        # if app.config['ENV'] == 'production':
        #     # Configuração do console para DEBUG/INFO
        #     stream_handler = logging.StreamHandler()
        #     stream_handler.setFormatter(formatter)
        #     stream_handler.setLevel(logging.INFO)
        #     app.logger.addHandler(stream_handler)

    # Cria a pasta de logs se ela não existir
    if not os.path.exists('logs'):
        os.mkdir('logs')
        
    # ====================================================================
    
    
    @app.errorhandler(403)
    def forbidden(error):
        return render_template('errors/error.html', title='Acesso negado', code=403, message='Você não tem permissão para acessar este recurso.'), 403
    
    @app.errorhandler(404)
    def page_not_found(error):
        return render_template('errors/error.html', title='Página não encontrada', code=404, message='A página solicitada não foi encontrada.'), 404
    
    @app.errorhandler(500)
    def internal_error(error):
        return render_template('errors/error.html', title='Erro interno', code=500, message='Não foi possível concluir a operação. Tente novamente mais tarde.'), 500
    
    return app # Retornando a instância da aplicação Flask


