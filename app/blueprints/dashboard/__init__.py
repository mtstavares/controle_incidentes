from flask import Blueprint

dashboard_bp = Blueprint('dashboard', __name__, template_folder='templates', static_folder='static')

from app.blueprints.dashboard import routes
