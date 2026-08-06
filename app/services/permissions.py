from functools import wraps

from flask import abort
from flask_login import current_user, login_required

from app.services.audit_service import AuditAction, registrar_auditoria


ROLE_PERMISSIONS = {
    "Admin": {
        "admin.access",
        "incident.create",
        "incident.edit",
        "incident.delete",
        "incident.comment.create",
        "incident.comment.delete.any",
        "incident.attachment.delete",
        "utility.buscar_pm.search",
        "utility.buscar_ip.search",
        "credential.import",
        "awareness.view",
        "awareness.manage",
    },
    "User": {
        "incident.create",
        "incident.edit",
        "incident.comment.create",
        "incident.attachment.delete",
        "utility.buscar_pm.search",
        "utility.buscar_ip.search",
        "credential.import",
        "awareness.view",
        "awareness.manage",
    },
    "Viewer": {
        "awareness.view",
    },
}


def can(user, permission):
    if not permission or not getattr(user, "is_authenticated", False):
        return False
    if not getattr(user, "is_active", False):
        return False
    profile = getattr(user, "profile", None)
    return permission in ROLE_PERMISSIONS.get(profile, set())


def can_current_user(permission):
    return can(current_user, permission)


def deny_access(*, modulo="Autorizacao", entidade=None, entidade_id=None, descricao=None):
    registrar_auditoria(
        acao=AuditAction.ACESSO_NEGADO,
        modulo=modulo,
        entidade=entidade,
        entidade_id=entidade_id,
        descricao=descricao or "Tentativa de acesso nao autorizado.",
        resultado="NEGADO",
    )
    abort(403)


def require_permission(permission, *, modulo="Autorizacao", entidade=None, entidade_id=None, descricao=None):
    if can_current_user(permission):
        return
    deny_access(modulo=modulo, entidade=entidade, entidade_id=entidade_id, descricao=descricao)


def permission_required(permission, *, modulo="Autorizacao", entidade=None, entidade_id=None, descricao=None):
    def decorator(func):
        @wraps(func)
        @login_required
        def wrapper(*args, **kwargs):
            require_permission(
                permission,
                modulo=modulo,
                entidade=entidade,
                entidade_id=entidade_id,
                descricao=descricao,
            )
            return func(*args, **kwargs)

        return wrapper

    return decorator
