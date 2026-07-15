from datetime import datetime, time
from flask import abort, flash, redirect, render_template, request, url_for
from flask_login import current_user
from app.blueprints.admin import admin_bp
from app import db
from app.models import AuditLog, User
from app.services.authz import admin_required
from app.services.audit_service import AuditAction, registrar_auditoria
from app.services.user_service import PERFIS_PERMITIDOS


def _parse_date(value, end=False):
    if not value:
        return None


@admin_bp.route("/admin/usuarios", methods=["GET"])
@admin_required
def gestao_usuarios():
    termo = request.args.get("q", "").strip()
    page = max(request.args.get("page", 1, type=int), 1)
    per_page = min(max(request.args.get("per_page", 20, type=int), 1), 50)

    query = User.query
    if termo:
        padrao = f"%{termo}%"
        query = query.filter(
            db.or_(
                User.name.ilike(padrao),
                User.username.ilike(padrao),
                User.email.ilike(padrao),
                User.profile.ilike(padrao),
            )
        )

    pagination = query.order_by(User.name.asc(), User.username.asc()).paginate(
        page=page,
        per_page=per_page,
        error_out=False,
    )

    return render_template(
        "admin/usuarios.html",
        title="Gestão de usuários",
        usuarios=pagination.items,
        pagination=pagination,
        q=termo,
        perfis=sorted(PERFIS_PERMITIDOS),
    )


@admin_bp.route("/admin/usuarios/<int:user_id>/perfil", methods=["POST"])
@admin_required
def alterar_perfil_usuario(user_id):
    user = User.query.get_or_404(user_id)
    novo_perfil = (request.form.get("profile") or "").strip()
    if novo_perfil not in PERFIS_PERMITIDOS:
        flash("Perfil informado é inválido.", "danger")
        abort(400)

    perfil_anterior = user.profile
    if perfil_anterior == novo_perfil:
        flash("Perfil atualizado com sucesso.", "success")
        return redirect(url_for("admin.gestao_usuarios"))

    if perfil_anterior == "Admin" and novo_perfil != "Admin":
        quantidade_admins = User.query.filter_by(profile="Admin").count()
        if quantidade_admins <= 1:
            flash("Não é possível remover o perfil do único administrador do sistema.", "danger")
            return redirect(url_for("admin.gestao_usuarios"))

    user.profile = novo_perfil
    db.session.commit()
    registrar_auditoria(
        acao=AuditAction.ALTERAR_USUARIO,
        modulo="Administração",
        entidade="User",
        entidade_id=user.id,
        descricao=f"Perfil do usuário {user.username} alterado por {current_user.username}.",
        alteracoes={"profile": {"anterior": perfil_anterior, "novo": novo_perfil}},
    )
    flash("Perfil atualizado com sucesso.", "success")
    return redirect(url_for("admin.gestao_usuarios"))
    try:
        parsed = datetime.strptime(value, "%Y-%m-%d").date()
        return datetime.combine(parsed, time.max if end else time.min)
    except ValueError:
        return None


@admin_bp.route("/admin/logs-auditoria", methods=["GET"])
@admin_required
def audit_logs():
    page = max(request.args.get("page", 1, type=int), 1)
    per_page = min(max(request.args.get("per_page", 50, type=int), 1), 100)
    query = AuditLog.query

    start_date = _parse_date(request.args.get("start_date"))
    end_date = _parse_date(request.args.get("end_date"), end=True)
    usuario = request.args.get("usuario", "").strip()
    acao = request.args.get("acao", "").strip()
    modulo = request.args.get("modulo", "").strip()
    resultado = request.args.get("resultado", "").strip()
    entidade = request.args.get("entidade", "").strip()
    entidade_id = request.args.get("entidade_id", "").strip()

    if start_date:
        query = query.filter(AuditLog.timestamp >= start_date)
    if end_date:
        query = query.filter(AuditLog.timestamp <= end_date)
    if usuario:
        query = query.filter(AuditLog.usuario_identificacao.ilike(f"%{usuario}%"))
    if acao:
        query = query.filter(AuditLog.acao == acao)
    if modulo:
        query = query.filter(AuditLog.modulo.ilike(f"%{modulo}%"))
    if resultado:
        query = query.filter(AuditLog.resultado == resultado)
    if entidade:
        query = query.filter(AuditLog.entidade == entidade)
    if entidade_id:
        query = query.filter(AuditLog.entidade_id == entidade_id)

    pagination = query.order_by(AuditLog.timestamp.desc()).paginate(
        page=page,
        per_page=per_page,
        error_out=False,
    )

    registrar_auditoria(
        acao=AuditAction.VISUALIZAR,
        modulo="Administração",
        entidade="AuditLog",
        descricao="Consulta aos logs de auditoria.",
    )

    return render_template(
        "admin/audit_logs.html",
        title="Logs de auditoria",
        logs=pagination.items,
        pagination=pagination,
        filtros=request.args,
        pagination_args={k: v for k, v in request.args.items() if k != "page"},
        action_options=[
            AuditAction.LOGIN,
            AuditAction.LOGOUT,
            AuditAction.LOGIN_FALHOU,
            AuditAction.CRIAR,
            AuditAction.EDITAR,
            AuditAction.EXCLUIR,
            AuditAction.VISUALIZAR,
            AuditAction.ALTERAR_SENHA,
            AuditAction.CRIAR_USUARIO,
            AuditAction.ALTERAR_USUARIO,
            AuditAction.ADICIONAR_OBSERVACAO,
            AuditAction.EXCLUIR_OBSERVACAO,
            AuditAction.ACESSO_NEGADO,
        ],
    )


@admin_bp.route("/admin/logs-auditoria/<int:log_id>", methods=["GET"])
@admin_required
def audit_log_detail(log_id):
    audit_log = AuditLog.query.get_or_404(log_id)
    registrar_auditoria(
        acao=AuditAction.VISUALIZAR,
        modulo="Administração",
        entidade="AuditLog",
        entidade_id=audit_log.id,
        descricao="Visualização detalhada de log de auditoria.",
    )
    return render_template("admin/audit_log_detail.html", title="Detalhe do log", log=audit_log)
