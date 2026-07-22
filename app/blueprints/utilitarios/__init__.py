from flask import Blueprint


utilitarios_bp = Blueprint(
    "utilitarios",
    __name__,
    template_folder="templates",
    static_folder="static",
)

from app.blueprints.utilitarios import routes
