import json

from flask import current_app, g, has_request_context, request
from flask_login import current_user

from app import db
from app.models import AuditLog
from app.services.timezone_service import utc_now


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
    USER_DELETED = "USER_DELETED"
    USER_DELETE_DENIED = "USER_DELETE_DENIED"
    IMPORTAR_CREDENCIAIS = "IMPORTAR_CREDENCIAIS"


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
        "unit_id",
        "command_id",
        "cia",
        "description",
        "description_plain_text",
        "user_id",
        "end_date",
        "created_at",
        "updated_at",
        "deleted_at",
    },
    "User": {
        "username",
        "name",
        "email",
        "profile",
        "is_temp_password",
        "must_change_password",
        "is_active",
        "deleted_by_id",
        "created_at",
        "updated_at",
        "deleted_at",
    },
    "IncidenteObs": {"texto_observacao", "incidente_id", "usuario_id", "created_at", "updated_at", "deleted_at"},
    "IncidentAttachment": {
        "incident_id",
        "original_filename",
        "mime_type",
        "file_size",
        "sha256",
        "uploaded_by_id",
        "uploaded_at",
        "created_at",
        "updated_at",
        "deleted_at",
    },
    "AuditLog": {"id", "timestamp", "request_id", "resultado"},
    "CredencialComprometida": {
        "nome",
        "cpf",
        "email",
        "data_coleta",
        "permitiu_acesso",
        "acesso_ad",
        "acesso_ms",
        "situacao_legal",
        "mensagem_bloqueio",
        "imported_at",
        "imported_by_id",
    },
}

SENSITIVE_KEYS = {
    "password",
    "senha",
    "new_password",
    "confirm_password",
    "csrf_token",
    "_csrf_token",
    "token",
    "cookie",
    "authorization",
    "secret",
    "session",
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


def _safe_request_context():
    if not has_request_context():
        return None, None, None, None, None
    return (
        request.remote_addr,
        request.headers.get("User-Agent"),
        request.endpoint,
        request.method,
        getattr(g, "request_id", None),
    )


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


def _log_structured_event(log):
    payload = {
        "event": "audit",
        "id": log.id,
        "occurred_at": log.timestamp.isoformat().replace("+00:00", "Z"),
        "actor_user_id": log.usuario_id,
        "actor_name": log.usuario_identificacao,
        "action": log.acao,
        "entity_type": log.entidade,
        "entity_id": log.entidade_id,
        "old_values": log.old_values,
        "new_values": log.new_values,
        "source_ip": log.ip_address,
        "user_agent": log.user_agent,
        "request_id": log.request_id,
        "result": log.resultado,
    }
    current_app.logger.info(json.dumps(payload, ensure_ascii=False, default=str))


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
    commit=True,
    raise_on_error=False,
):
    try:
        usuario_id, usuario_identificacao = _safe_user(usuario)
        ip_address, user_agent, endpoint, metodo_http, request_id = _safe_request_context()

        log = AuditLog(
            timestamp=utc_now(),
            request_id=_limit(request_id, 64),
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
        if commit:
            db.session.commit()
            _log_structured_event(log)
        return log
    except Exception as exc:
        db.session.rollback()
        try:
            current_app.logger.exception("Falha ao registrar auditoria: %s", exc)
        except Exception:
            pass
        if raise_on_error:
            raise
        return None
