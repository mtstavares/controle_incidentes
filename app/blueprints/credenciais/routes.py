from flask import jsonify, render_template, request
from flask_login import login_required
from sqlalchemy import func

from app import db
from app.blueprints.credenciais import credenciais_bp
from app.models import CredencialComprometida
from app.services.credential_service import (
    apply_credential_filters,
    credential_to_table_dict,
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
