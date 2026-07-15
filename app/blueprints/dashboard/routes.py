from flask import render_template
from flask_login import login_required
from app.blueprints.dashboard import dashboard_bp


@dashboard_bp.route("/dashboard-credenciais", methods=["GET"])
@login_required
def dashboard_credenciais():
    return render_template(
        "dashboard/credenciais.html",
        title="Dashboard de credenciais comprometidas",
    )
