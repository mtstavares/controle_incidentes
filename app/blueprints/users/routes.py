from flask import current_app, flash, redirect, render_template, request, session, url_for
from flask_login import current_user, login_required, login_user
from sqlalchemy.exc import IntegrityError
from app import db, limiter, lm
from app.blueprints.users import users_bp
from app.models import User
from app.services.audit_service import AuditAction, registrar_auditoria
from app.services.authz import admin_required
from app.services.user_service import (
    email_ativo_existente,
    gerar_hash_senha,
    normalizar_email,
    normalizar_nome,
    normalizar_usuario,
    senha_confere,
    usuario_inativo_por_username_ou_email,
    username_ativo_existente,
    validar_dados_usuario,
)


@lm.user_loader
def user_loader(id):
    return db.session.query(User).filter_by(id=id, is_active=True).first()


def allowed_edit_profile(profile):
    return getattr(profile, "is_active", True) and profile.profile in ["Admin", "User"]


@users_bp.route("/register", methods=["GET", "POST"])
@admin_required
def register():
    form_data = {}
    if request.method == "GET":
        return render_template("users/register_user.html", title="Registro de usuário", form_data=form_data, errors={})

    username = normalizar_usuario(request.form.get("username"))
    name = normalizar_nome(request.form.get("name"))
    email = normalizar_email(request.form.get("email"))
    profile = (request.form.get("profile") or "").strip()
    password = request.form.get("password") or ""
    form_data = {"username": username, "name": name, "email": email, "profile": profile}

    errors = validar_dados_usuario(username, name, email, profile, password)
    if username and username_ativo_existente(username):
        errors["username"] = "Já existe um usuário cadastrado com esse RE."
    if email and email_ativo_existente(email):
        errors["email"] = "Já existe um usuário cadastrado com esse e-mail."

    if errors:
        for message in errors.values():
            flash(message, "danger")
        return render_template("users/register_user.html", title="Registro de usuário", form_data=form_data, errors=errors)

    inactive_matches = usuario_inativo_por_username_ou_email(username, email)
    if len({user.id for user in inactive_matches}) > 1:
        flash("O RE e o e-mail informados pertencem a cadastros inativos diferentes. Revise os dados antes de continuar.", "danger")
        return render_template("users/register_user.html", title="Registro de usuário", form_data=form_data, errors={})

    if inactive_matches:
        user = inactive_matches[0]
        previous_values = {
            "username": user.username,
            "name": user.name,
            "email": user.email,
            "profile": user.profile,
            "is_active": user.is_active,
            "is_temp_password": user.is_temp_password,
            "must_change_password": user.must_change_password,
            "deleted_at": user.deleted_at.isoformat() if user.deleted_at else None,
            "deleted_by_id": user.deleted_by_id,
        }
        user.username = username
        user.name = name
        user.email = email
        user.profile = profile
        user.is_active = True
        user.is_temp_password = True
        user.must_change_password = True
        user.deleted_at = None
        user.deleted_by_id = None
        user.password = gerar_hash_senha(password)

        try:
            registrar_auditoria(
                acao=AuditAction.ALTERAR_USUARIO,
                modulo="Administração",
                entidade="User",
                entidade_id=user.id,
                descricao=f"Usuário reativado: {username}",
                alteracoes={
                    "username": {"anterior": previous_values["username"], "novo": username},
                    "name": {"anterior": previous_values["name"], "novo": name},
                    "email": {"anterior": previous_values["email"], "novo": email},
                    "profile": {"anterior": previous_values["profile"], "novo": profile},
                    "is_active": {"anterior": previous_values["is_active"], "novo": True},
                    "is_temp_password": {"anterior": previous_values["is_temp_password"], "novo": True},
                    "must_change_password": {"anterior": previous_values["must_change_password"], "novo": True},
                    "deleted_at": {"anterior": previous_values["deleted_at"], "novo": None},
                    "deleted_by_id": {"anterior": previous_values["deleted_by_id"], "novo": None},
                },
                commit=False,
                raise_on_error=True,
            )
            db.session.commit()
        except IntegrityError as exc:
            db.session.rollback()
            current_app.logger.exception("Falha de integridade ao reativar usuário: %s", exc)
            flash("Não foi possível reativar o usuário. O RE ou e-mail já está cadastrado.", "danger")
            return render_template("users/register_user.html", title="Registro de usuário", form_data=form_data, errors={})

        current_app.logger.info("%s reativou o usuário: %s", current_user.username, username)
        flash("Usuário reativado com sucesso. A troca de senha será exigida no próximo acesso.", "success")
        return redirect(url_for("admin.gestao_usuarios"))

    new_user = User(
        username=username,
        name=name,
        email=email,
        profile=profile,
        is_temp_password=True,
        must_change_password=True,
        password=gerar_hash_senha(password),
    )
    try:
        db.session.add(new_user)
        db.session.flush()
        registrar_auditoria(
            acao=AuditAction.CRIAR_USUARIO,
            modulo="Administração",
            entidade="User",
            entidade_id=new_user.id,
            descricao=f"Usuário criado: {username}",
            alteracoes={
                "username": {"anterior": None, "novo": username},
                "name": {"anterior": None, "novo": name},
                "email": {"anterior": None, "novo": email},
                "profile": {"anterior": None, "novo": profile},
                "is_temp_password": {"anterior": None, "novo": True},
                "must_change_password": {"anterior": None, "novo": True},
            },
            commit=False,
            raise_on_error=True,
        )
        db.session.commit()
    except IntegrityError as exc:
        db.session.rollback()
        current_app.logger.exception("Falha de integridade ao criar usuário: %s", exc)
        flash("Não foi possível criar o usuário. O RE ou e-mail já está cadastrado.", "danger")
        return render_template("users/register_user.html", title="Registro de usuário", form_data=form_data, errors={})

    current_app.logger.info("%s cadastrou o usuário: %s", current_user.username, username)
    flash("Usuário criado com sucesso.", "success")
    return redirect(url_for("admin.gestao_usuarios"))


@users_bp.route("/login", methods=["GET", "POST"])
@limiter.limit("5 per minute")
def login():
    if request.method == "GET":
        return render_template("users/login.html", title="Login de usuário")

    username = normalizar_usuario(request.form.get("username"))
    password = request.form.get("password") or ""
    user = db.session.query(User).filter(db.func.lower(User.username) == username.lower()).first()

    if not user or not user.is_active or not senha_confere(user, password):
        registrar_auditoria(
            acao=AuditAction.LOGIN_FALHOU,
            modulo="Autenticação",
            descricao=f"Tentativa de login malsucedida para usuário: {username}",
            resultado="FALHA",
            usuario=None,
        )
        current_app.logger.info("%s tentou logar com usuário ou senha incorreta.", username)
        flash("Nome de usuário ou senha incorretos.", "danger")
        return redirect(url_for("users.login"))

    session.permanent = True
    login_user(user)
    registrar_auditoria(
        acao=AuditAction.LOGIN,
        modulo="Autenticação",
        descricao=f"Login bem-sucedido para usuário: {user.username}",
        usuario=user,
    )

    if user.must_change_password or user.is_temp_password:
        current_app.logger.info("%s logou com senha temporária.", user.username)
        return redirect(url_for("users.change_password"))

    current_app.logger.info("%s logou no sistema.", user.username)
    return redirect(url_for("main.home"))


@users_bp.route("/change_password", methods=["GET", "POST"])
@login_required
def change_password():
    if request.method == "POST":
        new_password = request.form.get("new_password") or ""
        confirm_password = request.form.get("confirm_password") or ""

        if not new_password or new_password != confirm_password:
            flash("As senhas devem ser iguais.", "danger")
            return redirect(url_for("users.change_password"))
        if len(new_password) < 8 or len(new_password) > 128:
            flash("A senha deve ter entre 8 e 128 caracteres.", "danger")
            return redirect(url_for("users.change_password"))

        temp_password_before = current_user.is_temp_password
        must_change_before = current_user.must_change_password
        current_user.password = gerar_hash_senha(new_password)
        current_user.is_temp_password = False
        current_user.must_change_password = False
        registrar_auditoria(
            acao=AuditAction.ALTERAR_SENHA,
            modulo="Autenticação",
            entidade="User",
            entidade_id=current_user.id,
            descricao="Usuário alterou a própria senha.",
            alteracoes={
                "is_temp_password": {"anterior": temp_password_before, "novo": False},
                "must_change_password": {"anterior": must_change_before, "novo": False},
            },
            commit=False,
            raise_on_error=True,
        )
        db.session.commit()
        current_app.logger.info("%s alterou sua senha.", current_user.username)
        flash("Senha alterada com sucesso!", "success")
        return redirect(url_for("main.home"))

    return render_template("users/change_psw.html", title="Alteração de senha")
