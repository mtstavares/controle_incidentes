import re
from werkzeug.security import check_password_hash, generate_password_hash
from app import hash as legacy_hash
from app import db
from app.models import User


PERFIS_PERMITIDOS = {"Admin", "User", "Viewer"}


def normalizar_usuario(username):
    return (username or "").strip()


def normalizar_nome(name):
    return re.sub(r"\s+", " ", (name or "").strip())


def normalizar_email(email):
    return (email or "").strip().lower()


def validar_dados_usuario(username, name, email, profile, password=None, validar_senha=True):
    errors = {}
    if not username:
        errors["username"] = "O RE ou usuário é obrigatório."
    elif len(username) < 3 or len(username) > 50:
        errors["username"] = "O RE ou usuário deve ter entre 3 e 50 caracteres."
    elif not re.fullmatch(r"[A-Za-z0-9._-]+", username):
        errors["username"] = "Use apenas letras, números, ponto, hífen ou sublinhado no RE."

    if not name:
        errors["name"] = "O nome completo é obrigatório."
    elif len(name) > 150:
        errors["name"] = "O nome completo deve ter no máximo 150 caracteres."

    if not email:
        errors["email"] = "O e-mail é obrigatório."
    elif len(email) > 255:
        errors["email"] = "O e-mail deve ter no máximo 255 caracteres."
    elif not re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", email):
        errors["email"] = "Informe um e-mail válido."

    if profile not in PERFIS_PERMITIDOS:
        errors["profile"] = "Perfil informado é inválido."

    if validar_senha:
        if not password:
            errors["password"] = "A senha temporária é obrigatória."
        elif len(password) < 8 or len(password) > 128:
            errors["password"] = "A senha temporária deve ter entre 8 e 128 caracteres."

    return errors


def username_existente(username):
    return User.query.filter(db.func.lower(User.username) == username.lower()).first()


def email_existente(email):
    return User.query.filter(db.func.lower(User.email) == email.lower()).first()


def gerar_hash_senha(password):
    try:
        return generate_password_hash(password, method="scrypt")
    except ValueError:
        return generate_password_hash(password)


def senha_confere(user, password):
    stored = user.password or ""
    if stored.startswith(("scrypt:", "pbkdf2:", "argon2:")):
        return check_password_hash(stored, password)
    return stored == legacy_hash(password)
