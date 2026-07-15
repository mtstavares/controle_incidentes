from flask import render_template
from flask_login import login_required
from app.blueprints.credenciais import credenciais_bp


@credenciais_bp.route("/credenciais-comprometidas", methods=["GET"])
@login_required
def listar_credenciais():
    return render_template(
        "credenciais/listar.html",
        title="Credenciais comprometidas",
    )
