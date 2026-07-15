from flask import Blueprint

credenciais_bp = Blueprint('credenciais', __name__, template_folder='templates', static_folder='static')

from app.blueprints.credenciais import routes
