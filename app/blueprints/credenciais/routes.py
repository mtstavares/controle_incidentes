from flask import flash, jsonify, redirect, render_template, request, url_for
from flask_login import current_user, login_required
from sqlalchemy import func

from app import db
from app.blueprints.credenciais import credenciais_bp
from app.models import CredencialComprometida
from app.services.audit_service import AuditAction, registrar_auditoria
from app.services.credential_service import (
    apply_credential_filters,
    build_monthly_import_preview,
    confirm_monthly_import,
    credential_to_table_dict,
    delete_monthly_import_preview,
    load_monthly_import_preview,
    order_credentials,
)


IMPORT_PROFILES = {"Admin", "User"}


def _safe_per_page():
    return min(max(request.args.get("per_page", 20, type=int), 1), 50)


def _base_query():
    return db.session.query(CredencialComprometida).filter(
        CredencialComprometida.deleted_at.is_(None)
    ).with_entities(
        CredencialComprometida.id,
        CredencialComprometida.cpf,
        CredencialComprometida.nome,
        CredencialComprometida.email,
        CredencialComprometida.acesso_ad,
        CredencialComprometida.acesso_ms,
        CredencialComprometida.rds,
        CredencialComprometida.mensagem_bloqueio,
        CredencialComprometida.situacao_legal,
        CredencialComprometida.data_coleta,
    )


def _situacoes_legais():
    rows = (
        db.session.query(
            CredencialComprometida.situacao_legal_normalizada,
            func.min(CredencialComprometida.situacao_legal),
        )
        .filter(CredencialComprometida.deleted_at.is_(None))
        .filter(CredencialComprometida.situacao_legal_normalizada.isnot(None))
        .filter(CredencialComprometida.situacao_legal_normalizada != "")
        .group_by(CredencialComprometida.situacao_legal_normalizada)
        .order_by(func.min(CredencialComprometida.situacao_legal).asc())
        .all()
    )
    return [{"value": row[0], "label": row[1]} for row in rows if row[0] and row[1]]


def _query_credentials():
    query = apply_credential_filters(_base_query(), request.args)
    query, sort, direction = order_credentials(query, request.args)
    pagination = query.paginate(page=max(request.args.get("page", 1, type=int), 1), per_page=_safe_per_page(), error_out=False)
    return pagination, sort, direction


@credenciais_bp.route("/credenciais-comprometidas", methods=["GET"])
@login_required
def listar_credenciais():
    try:
        pagination, sort, direction = _query_credentials()
        error_message = None
    except ValueError as exc:
        pagination = None
        sort = "data_coleta"
        direction = "desc"
        error_message = str(exc)

    return render_template(
        "credenciais/listar.html",
        title="Credenciais comprometidas",
        pagination=pagination,
        credenciais=pagination.items if pagination else [],
        filtros=request.args,
        sort=sort,
        direction=direction,
        situacoes_legais=_situacoes_legais(),
        error_message=error_message,
        can_import=getattr(current_user, "profile", None) in IMPORT_PROFILES,
        import_preview=None,
    )


@credenciais_bp.route("/api/credenciais-comprometidas", methods=["GET"])
@login_required
def listar_credenciais_api():
    try:
        pagination, sort, direction = _query_credentials()
    except ValueError as exc:
        return jsonify({"data": [], "error": {"message": str(exc)}, "meta": {}}), 400

    return jsonify({
        "data": [credential_to_table_dict(item) for item in pagination.items],
        "error": None,
        "meta": {
            "page": pagination.page,
            "pages": pagination.pages,
            "total": pagination.total,
            "hasNext": pagination.has_next,
            "hasPrev": pagination.has_prev,
            "sort": sort,
            "direction": direction,
        },
    })


@credenciais_bp.route("/credenciais-comprometidas/importar/previsualizar", methods=["POST"])
@login_required
def previsualizar_importacao_mensal():
    if getattr(current_user, "profile", None) not in IMPORT_PROFILES:
        registrar_auditoria(
            acao=AuditAction.ACESSO_NEGADO,
            modulo="Credenciais comprometidas",
            entidade="CredencialComprometida",
            descricao="Tentativa de previsualizar importacao mensal sem permissao.",
            resultado="NEGADO",
        )
        flash("Seu perfil não possui permissão para importar credenciais.", "danger")
        return redirect(url_for("credenciais.listar_credenciais"))

    storage = request.files.get("arquivo")
    if not storage:
        flash("Selecione uma planilha mensal para importar.", "danger")
        return redirect(url_for("credenciais.listar_credenciais"))

    try:
        preview = build_monthly_import_preview(storage)
        registrar_auditoria(
            acao=AuditAction.IMPORTAR_CREDENCIAIS,
            modulo="Credenciais comprometidas",
            entidade="CredencialComprometida",
            descricao="Gerou previsualizacao de importacao mensal de credenciais.",
            alteracoes={
                "data_coleta": {
                    "anterior": None,
                    "novo": f"{preview.year:04d}-{preview.month:02d}",
                },
                "permitiu_acesso": {
                    "anterior": None,
                    "novo": f"testadas={preview.total_tested}; validadas={preview.total_validated}",
                },
            },
        )
        pagination, sort, direction = _query_credentials()
        return render_template(
            "credenciais/listar.html",
            title="Credenciais comprometidas",
            pagination=pagination,
            credenciais=pagination.items if pagination else [],
            filtros=request.args,
            sort=sort,
            direction=direction,
            situacoes_legais=_situacoes_legais(),
            error_message=None,
            can_import=True,
            import_preview=preview,
        )
    except ValueError as exc:
        flash(str(exc), "danger")
    except Exception:
        registrar_auditoria(
            acao=AuditAction.IMPORTAR_CREDENCIAIS,
            modulo="Credenciais comprometidas",
            entidade="CredencialComprometida",
            descricao="Falha ao gerar previsualizacao de importacao mensal.",
            resultado="ERRO",
        )
        flash("Não foi possível validar a planilha mensal.", "danger")
    return redirect(url_for("credenciais.listar_credenciais"))


@credenciais_bp.route("/credenciais-comprometidas/importar/confirmar", methods=["POST"])
@login_required
def confirmar_importacao_mensal():
    if getattr(current_user, "profile", None) not in IMPORT_PROFILES:
        registrar_auditoria(
            acao=AuditAction.ACESSO_NEGADO,
            modulo="Credenciais comprometidas",
            entidade="CredencialComprometida",
            descricao="Tentativa de confirmar importacao mensal sem permissao.",
            resultado="NEGADO",
        )
        flash("Seu perfil não possui permissão para importar credenciais.", "danger")
        return redirect(url_for("credenciais.listar_credenciais"))

    token = request.form.get("preview_token")
    try:
        batch = confirm_monthly_import(token, user_id=current_user.id)
        registrar_auditoria(
            acao=AuditAction.IMPORTAR_CREDENCIAIS,
            modulo="Credenciais comprometidas",
            entidade="CredencialComprometida",
            entidade_id=str(batch.id),
            descricao="Importou planilha mensal de credenciais comprometidas.",
            alteracoes={
                "data_coleta": {
                    "anterior": None,
                    "novo": f"{batch.ano_referencia:04d}-{batch.mes_referencia:02d}",
                },
                "permitiu_acesso": {
                    "anterior": None,
                    "novo": (
                        f"testadas={batch.total_testado}; validadas={batch.total_validado}; "
                        f"sem_acesso={batch.total_nao_validado}"
                    ),
                },
            },
        )
        flash("Planilha mensal importada com sucesso.", "success")
    except ValueError as exc:
        flash(str(exc), "danger")
    except Exception:
        db.session.rollback()
        registrar_auditoria(
            acao=AuditAction.IMPORTAR_CREDENCIAIS,
            modulo="Credenciais comprometidas",
            entidade="CredencialComprometida",
            descricao="Falha ao confirmar importacao mensal.",
            resultado="ERRO",
        )
        flash("Não foi possível importar a planilha mensal.", "danger")
    return redirect(url_for("credenciais.listar_credenciais"))


@credenciais_bp.route("/credenciais-comprometidas/importar/cancelar", methods=["POST"])
@login_required
def cancelar_importacao_mensal():
    if getattr(current_user, "profile", None) not in IMPORT_PROFILES:
        registrar_auditoria(
            acao=AuditAction.ACESSO_NEGADO,
            modulo="Credenciais comprometidas",
            entidade="CredencialComprometida",
            descricao="Tentativa de cancelar importacao mensal sem permissao.",
            resultado="NEGADO",
        )
        flash("Seu perfil não possui permissão para importar credenciais.", "danger")
        return redirect(url_for("credenciais.listar_credenciais"))

    token = request.form.get("preview_token")
    try:
        preview = load_monthly_import_preview(token)
        delete_monthly_import_preview(token)
        registrar_auditoria(
            acao=AuditAction.IMPORTAR_CREDENCIAIS,
            modulo="Credenciais comprometidas",
            entidade="CredencialComprometida",
            descricao="Cancelou previsualizacao de importacao mensal de credenciais.",
            alteracoes={
                "data_coleta": {
                    "anterior": f"{preview.year:04d}-{preview.month:02d}",
                    "novo": None,
                },
            },
            resultado="CANCELADO",
        )
        flash("Importação mensal cancelada.", "info")
    except ValueError:
        flash("Pré-visualização de importação não encontrada ou expirada.", "warning")
    except Exception:
        registrar_auditoria(
            acao=AuditAction.IMPORTAR_CREDENCIAIS,
            modulo="Credenciais comprometidas",
            entidade="CredencialComprometida",
            descricao="Falha ao cancelar previsualizacao de importacao mensal.",
            resultado="ERRO",
        )
        flash("Não foi possível cancelar a importação mensal.", "danger")
    return redirect(url_for("credenciais.listar_credenciais"))
