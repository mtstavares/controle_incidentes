from datetime import datetime, timezone
from flask import current_app, has_request_context, request
from flask_login import current_user
from app import db
from app.models import AuditLog


class AuditAction:
    LOGIN = "LOGIN"
    LOGOUT = "LOGOUT"
    LOGIN_FALHOU = "LOGIN_FALHOU"
    CRIAR = "CRIAR"
    EDITAR = "EDITAR"
    EXCLUIR = "EXCLUIR"
    VISUALIZAR = "VISUALIZAR"
    ALTERAR_SENHA = "ALTERAR_SENHA"
    CRIAR_USUARIO = "CRIAR_USUARIO"
    ALTERAR_USUARIO = "ALTERAR_USUARIO"
    DESATIVAR_USUARIO = "DESATIVAR_USUARIO"
    ADICIONAR_OBSERVACAO = "ADICIONAR_OBSERVACAO"
    EXCLUIR_OBSERVACAO = "EXCLUIR_OBSERVACAO"
    EXPORTAR = "EXPORTAR"
    ACESSO_NEGADO = "ACESSO_NEGADO"
    UPLOAD_ANEXO = "UPLOAD_ANEXO"
    DOWNLOAD_ANEXO = "DOWNLOAD_ANEXO"
    EXCLUIR_ANEXO = "EXCLUIR_ANEXO"


AUDITABLE_FIELDS = {
    "Incidente": {
        "status_incident",
        "start_date",
        "incident_type",
        "message_number",
        "report_number",
        "ticket_number",
        "btl",
        "cpa",
        "cia",
        "description",
        "description_plain_text",
        "user_id",
        "end_date",
    },
    "User": {"username", "name", "email", "profile", "is_temp_password", "must_change_password"},
    "IncidenteObs": {"texto_observacao", "incidente_id", "usuario_id"},
    "IncidentAttachment": {
        "incident_id",
        "original_filename",
        "mime_type",
        "file_size",
        "sha256",
        "uploaded_by_id",
    },
}

SENSITIVE_KEYS = {
    "password",
    "senha",
    "new_password",
    "confirm_password",
    "csrf_token",
    "token",
    "cookie",
    "authorization",
    "secret",
}


def _limit(value, max_length):
    if value is None:
        return None
    text = str(value)
    return text[:max_length]


def _safe_user(usuario=None):
    user = usuario
    if user is None and current_user and not current_user.is_anonymous:
        user = current_user
    if user and not getattr(user, "is_anonymous", False):
        identity = getattr(user, "username", None) or getattr(user, "name", None) or f"user:{user.id}"
        return user.id, _limit(identity, 255)
    return None, "anonimo"


def filtrar_alteracoes(entidade, alteracoes):
    if not alteracoes:
        return None
    allowed = AUDITABLE_FIELDS.get(entidade, set())
    safe_changes = {}
    for key, value in alteracoes.items():
        key_lower = str(key).lower()
        if key not in allowed or key_lower in SENSITIVE_KEYS:
            continue
        if isinstance(value, dict):
            anterior = value.get("anterior")
            novo = value.get("novo")
        else:
            anterior = None
            novo = value
        safe_changes[key] = {
            "anterior": _limit(anterior, 1000),
            "novo": _limit(novo, 1000),
        }
    return safe_changes or None


def montar_alteracoes(entidade, anteriores, novos):
    changes = {}
    for key in AUDITABLE_FIELDS.get(entidade, set()):
        old_value = anteriores.get(key)
        new_value = novos.get(key)
        if str(old_value or "") != str(new_value or ""):
            changes[key] = {"anterior": old_value, "novo": new_value}
    return filtrar_alteracoes(entidade, changes)


def registrar_auditoria(
    *,
    acao,
    modulo,
    descricao,
    entidade=None,
    entidade_id=None,
    alteracoes=None,
    resultado="SUCESSO",
    usuario=None,
):
    try:
        usuario_id, usuario_identificacao = _safe_user(usuario)
        ip_address = None
        user_agent = None
        endpoint = None
        metodo_http = None
        if has_request_context():
            # Em produção, use ProxyFix apenas quando o proxy reverso for confiável.
            ip_address = request.remote_addr
            user_agent = request.headers.get("User-Agent")
            endpoint = request.endpoint
            metodo_http = request.method

        log = AuditLog(
            timestamp=datetime.now(timezone.utc),
            usuario_id=usuario_id,
            usuario_identificacao=usuario_identificacao,
            acao=_limit(acao, 50),
            modulo=_limit(modulo, 100),
            entidade=_limit(entidade, 100),
            entidade_id=_limit(entidade_id, 100),
            descricao=_limit(descricao, 500) or "Evento de auditoria",
            alteracoes=filtrar_alteracoes(entidade, alteracoes),
            ip_address=_limit(ip_address, 45),
            user_agent=_limit(user_agent, 500),
            endpoint=_limit(endpoint, 255),
            metodo_http=_limit(metodo_http, 10),
            resultado=_limit(resultado, 30) or "SUCESSO",
        )
        db.session.add(log)
        db.session.commit()
    except Exception as exc:
        db.session.rollback()
        try:
            current_app.logger.exception("Falha ao registrar auditoria: %s", exc)
        except Exception:
            pass
