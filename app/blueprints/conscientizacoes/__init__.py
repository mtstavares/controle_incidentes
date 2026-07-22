from flask import Blueprint


conscientizacoes_bp = Blueprint(
    "conscientizacoes",
    __name__,
    template_folder="templates",
    static_folder="static",
)

from app.blueprints.conscientizacoes import routes
