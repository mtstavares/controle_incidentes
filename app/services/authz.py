from functools import wraps
from flask import abort
from flask_login import current_user, login_required
from app.services.audit_service import AuditAction, registrar_auditoria


def admin_required(func):
    @wraps(func)
    @login_required
    def wrapper(*args, **kwargs):
        if not getattr(current_user, "is_active", False) or current_user.profile != "Admin":
            registrar_auditoria(
                acao=AuditAction.ACESSO_NEGADO,
                modulo="Administração",
                descricao="Tentativa de acesso administrativo não autorizado.",
                resultado="NEGADO",
            )
            abort(403)
        return func(*args, **kwargs)

    return wrapper
