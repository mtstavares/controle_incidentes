from app.services.permissions import permission_required


def admin_required(func):
    return permission_required(
        "admin.access",
        modulo="Administracao",
        descricao="Tentativa de acesso administrativo nao autorizado.",
    )(func)
