import time

from flask import flash, render_template, request
from flask_login import current_user, login_required

from app import limiter
from app.blueprints.utilitarios import utilitarios_bp
from app.services.audit_service import AuditAction, registrar_auditoria
from app.services.buscar_pm_service import (
    BuscarPMError,
    BuscarPMValidationError,
    buscar_pm as consultar_pm,
    mask_query,
    normalize_query,
)


VIEWER_BLOCK_MESSAGE = (
    "Seu perfil possui apenas permissão de visualização. A consulta de policiais militares "
    "é permitida apenas para usuários Admin e User."
)


def _rate_limit_key():
    if current_user and current_user.is_authenticated:
        return f"user:{current_user.id}"
    return request.remote_addr or "anonimo"


def _can_search():
    return getattr(current_user, "profile", None) in {"Admin", "User"}


def _audit_search(query_kind, query_value, result, elapsed_ms):
    registrar_auditoria(
        acao="BUSCAR_PM",
        modulo="Utilitários - Buscar PM",
        entidade="ConsultaPM",
        entidade_id=mask_query(query_value),
        descricao=f"Consulta de policial militar por {query_kind} concluída com resultado {result}.",
        alteracoes={
            "query_kind": {"novo": query_kind},
            "query_masked": {"novo": mask_query(query_value)},
            "elapsed_ms": {"novo": elapsed_ms},
        },
        resultado=result,
    )


@utilitarios_bp.route("/utilitarios/buscar-pm", methods=["GET", "POST"])
@login_required
@limiter.limit("20 per minute", key_func=_rate_limit_key, methods=["POST"])
def buscar_pm():
    result = None
    query_value = ""

    if request.method == "POST":
        started_at = time.perf_counter()
        query_value = request.form.get("query", "")
        try:
            query = normalize_query(query_value)
        except BuscarPMValidationError as exc:
            flash(exc.message, "danger")
            return render_template("utilitarios/buscar_pm.html", title="Buscar PM", result=None, query=query_value), 400

        if not _can_search():
            elapsed_ms = int((time.perf_counter() - started_at) * 1000)
            _audit_search(query.kind, query.value, "NEGADO", elapsed_ms)
            flash(VIEWER_BLOCK_MESSAGE, "warning")
            return render_template("utilitarios/buscar_pm.html", title="Buscar PM", result=None, query=query_value), 403

        try:
            result = consultar_pm(query.value)
            elapsed_ms = int((time.perf_counter() - started_at) * 1000)
            _audit_search(query.kind, query.value, "SUCESSO", elapsed_ms)
            flash("Consulta realizada com sucesso.", "success")
        except BuscarPMError as exc:
            elapsed_ms = int((time.perf_counter() - started_at) * 1000)
            _audit_search(query.kind, query.value, exc.audit_result, elapsed_ms)
            flash(exc.message, "danger")
        except Exception:
            elapsed_ms = int((time.perf_counter() - started_at) * 1000)
            _audit_search(query.kind, query.value, "ERRO_INTERNO", elapsed_ms)
            flash("Não foi possível realizar a consulta no momento.", "danger")

    return render_template("utilitarios/buscar_pm.html", title="Buscar PM", result=result, query=query_value)
