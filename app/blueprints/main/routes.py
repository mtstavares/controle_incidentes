# app/blueprints/main/routes.py

from flask import current_app, redirect, render_template, send_from_directory, url_for
from flask_login import current_user, login_required, logout_user

from app.blueprints.main import main_bp  #importando a instância do blueprint main
from app.services.audit_service import AuditAction, registrar_auditoria


@main_bp.route('/')
@main_bp.route('/home')
@login_required
def home():
    #Rota página inicial
    return render_template('main/home.html', title='Página Inicial')
                           
@main_bp.route('/about')
def about():
    #Rota página sobre
    return render_template('main/about.html', title='Sobre')

@main_bp.route("/manifest.webmanifest")
def webmanifest():
    response = send_from_directory(
        current_app.static_folder,
        "manifest.webmanifest",
        mimetype="application/manifest+json",
        max_age=3600,
    )
    response.headers["Cache-Control"] = "public, max-age=3600"
    return response


@main_bp.route("/service-worker.js")
def service_worker():
    response = send_from_directory(
        current_app.static_folder,
        "js/service-worker.js",
        mimetype="application/javascript",
        max_age=0,
    )
    response.headers["Cache-Control"] = "no-cache"
    response.headers["Service-Worker-Allowed"] = "/"
    return response


@main_bp.route('/logout')
@login_required
def logout():
    # Rota para logout do usuário
    registrar_auditoria(
        acao=AuditAction.LOGOUT,
        modulo="Autenticação",
        descricao=f"Logout do usuário: {current_user.username}",
    )
    current_app.logger.info(f" {current_user.username} deslogou.")
    logout_user()
    return redirect(url_for('main.home'))
    
    
    
    
