# app/blueprints/main/routes.py

from flask import render_template, redirect, url_for, current_app
from app.blueprints.main import main_bp #importando a instância do blueprint main
from flask_login import login_required, logout_user, current_user
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
    
    
    
    
