from flask import current_app, flash, jsonify, redirect, render_template, request, url_for
from flask_login import current_user, login_required
from sqlalchemy import func

from app import db
from app.blueprints.credenciais import credenciais_bp
from app.models import CredencialComprometida
from app.services.audit_service import AuditAction, registrar_auditoria
from app.services.authz import admin_required
from app.services.credential_service import (
    apply_credential_filters,
    credential_to_table_dict,
    import_credential_spreadsheet,
    order_credentials,
)


def _safe_per_page():
    return min(max(request.args.get("per_page", 20, type=int), 1), 50)


def _base_query():
    return db.session.query(CredencialComprometida).with_entities(
        CredencialComprometida.id,
        CredencialComprometida.cpf,
        CredencialComprometida.nome,
        CredencialComprometida.email,
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


@credenciais_bp.route("/credenciais-comprometidas/importar", methods=["POST"])
@admin_required
def importar_credenciais():
    upload = request.files.get("arquivo")
    if not upload:
        flash("Selecione uma planilha Excel para importar.", "danger")
        return redirect(url_for("credenciais.listar_credenciais"))

    try:
        summary = import_credential_spreadsheet(upload, user_id=current_user.id)
        registrar_auditoria(
            acao=AuditAction.IMPORTAR_CREDENCIAIS,
            modulo="Credenciais comprometidas",
            entidade="CredencialComprometida",
            descricao="Importação de credenciais comprometidas concluída.",
            alteracoes={
                "total_linhas": {"anterior": None, "novo": summary.total_rows},
                "importadas": {"anterior": None, "novo": summary.imported},
                "atualizadas": {"anterior": None, "novo": summary.updated},
                "rejeitadas": {"anterior": None, "novo": summary.rejected},
                "coluna_senha_ignorada": {"anterior": None, "novo": summary.ignored_password_column},
                "erros": {"anterior": None, "novo": summary.errors[:20]},
            },
            commit=False,
            raise_on_error=True,
        )
        db.session.commit()
        flash(
            f"Importação concluída: {summary.imported} importadas, {summary.updated} atualizadas e {summary.rejected} rejeitadas.",
            "success",
        )
    except ValueError as exc:
        db.session.rollback()
        registrar_auditoria(
            acao=AuditAction.IMPORTAR_CREDENCIAIS,
            modulo="Credenciais comprometidas",
            entidade="CredencialComprometida",
            descricao="Importação de credenciais comprometidas rejeitada por validação.",
            alteracoes={"erro": {"anterior": None, "novo": str(exc)}},
            resultado="FALHA",
        )
        flash(str(exc), "danger")
    except Exception as exc:
        db.session.rollback()
        current_app.logger.exception("Falha inesperada ao importar credenciais: %s", exc)
        flash("Não foi possível importar a planilha de credenciais.", "danger")

    return redirect(url_for("credenciais.listar_credenciais"))
