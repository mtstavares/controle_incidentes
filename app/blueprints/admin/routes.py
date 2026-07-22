import re
import unicodedata

from flask import abort, flash, jsonify, redirect, render_template, request, url_for
from flask_login import current_user
from sqlalchemy.exc import IntegrityError

from app.blueprints.admin import admin_bp
from app import db
from app.models import (
    AuditLog,
    Incidente,
    OrganizationalCommand,
    OrganizationalUnit,
    StatusIncidente,
    TipoIncidente,
    Unidades,
    User,
)
from app.services.authz import admin_required
from app.services.audit_service import AuditAction, registrar_auditoria
from app.services.timezone_service import local_date_bounds_as_utc_naive, parse_iso_date, utc_now
from app.services.user_service import PERFIS_PERMITIDOS

MAX_LIBRARY_NAME_LENGTH = 100


def _normalize_spaces(value):
    return re.sub(r"\s+", " ", (value or "").strip())


def _normalized_key(value):
    normalized = unicodedata.normalize("NFKD", _normalize_spaces(value))
    without_accents = "".join(char for char in normalized if not unicodedata.combining(char))
    return without_accents.casefold()


def _unit_normalized_name(value):
    return _normalize_spaces(value).casefold()


def _validate_library_name(value, label):
    name = _normalize_spaces(value)
    if not name:
        flash(f"{label} é obrigatório.", "danger")
        return None
    if len(name) > MAX_LIBRARY_NAME_LENGTH:
        flash(f"{label} deve ter no máximo {MAX_LIBRARY_NAME_LENGTH} caracteres.", "danger")
        return None
    if any(ord(char) < 32 for char in name):
        flash(f"{label} contém caracteres inválidos.", "danger")
        return None
    return name


def _library_redirect(anchor):
    return redirect(f"{url_for('admin.biblioteca')}#{anchor}")


def _has_duplicate(rows, field_name, name, current_id=None):
    key = _normalized_key(name)
    return any(
        _normalized_key(getattr(row, field_name)) == key and row.id != current_id
        for row in rows
    )


def _status_incident_count(status_name):
    return Incidente.query.filter(Incidente.status_incident == status_name).count()


def _type_incident_count(type_name):
    return Incidente.query.filter(Incidente.incident_type == type_name).count()


def _command_incident_count(command):
    return Incidente.query.filter(
        db.or_(Incidente.command_id == command.id, Incidente.cpa == command.name)
    ).count()


def _unit_incident_count(unit):
    return Incidente.query.filter(
        db.or_(
            Incidente.unit_id == unit.id,
            db.and_(Incidente.command_id == unit.command_id, Incidente.btl == unit.name),
        )
    ).count()


def _sort_commands_query(query):
    return query.order_by(
        OrganizationalCommand.sort_order.is_(None),
        OrganizationalCommand.sort_order.asc(),
        OrganizationalCommand.name.asc(),
    )


def _sort_units_query(query):
    return query.order_by(
        OrganizationalUnit.sort_order.is_(None),
        OrganizationalUnit.sort_order.asc(),
        OrganizationalUnit.name.asc(),
    )


def _next_unit_sort_order(command_id):
    current = db.session.query(db.func.max(OrganizationalUnit.sort_order)).filter_by(command_id=command_id).scalar()
    return (current or 0) + 1


def _audit_library_change(action, entity, entity_id, description, changes=None):
    registrar_auditoria(
        acao=action,
        modulo="Administração - Biblioteca",
        entidade=entity,
        entidade_id=entity_id,
        descricao=description,
        alteracoes=changes,
        commit=False,
        raise_on_error=True,
    )


def _parse_date(value, end=False):
    if not value:
        return None
    try:
        parsed = parse_iso_date(value)
    except ValueError:
        return None
    start_bound, end_bound = local_date_bounds_as_utc_naive(parsed)
    return end_bound if end else start_bound


def _wants_json():
    return request.accept_mimetypes.best == "application/json" or request.headers.get("X-Requested-With") == "XMLHttpRequest"


def _delete_user_response(message, status_code, category="danger"):
    if _wants_json():
        return jsonify({"message": message}), status_code
    flash(message, category)
    return redirect(url_for("admin.gestao_usuarios"))


@admin_bp.route("/admin/usuarios", methods=["GET"])
@admin_required
def gestao_usuarios():
    termo = request.args.get("q", "").strip()
    page = max(request.args.get("page", 1, type=int), 1)
    per_page = min(max(request.args.get("per_page", 20, type=int), 1), 50)

    query = User.query.filter(User.is_active.is_(True))
    if termo:
        padrao = f"%{termo}%"
        query = query.filter(
            db.or_(
                User.name.ilike(padrao),
                User.username.ilike(padrao),
                User.email.ilike(padrao),
                User.profile.ilike(padrao),
            )
        )

    pagination = query.order_by(User.name.asc(), User.username.asc()).paginate(
        page=page,
        per_page=per_page,
        error_out=False,
    )

    return render_template(
        "admin/usuarios.html",
        title="Gestão de usuários",
        usuarios=pagination.items,
        pagination=pagination,
        q=termo,
        perfis=sorted(PERFIS_PERMITIDOS),
    )


@admin_bp.route("/admin/usuarios/<int:user_id>/perfil", methods=["POST"])
@admin_required
def alterar_perfil_usuario(user_id):
    user = User.query.filter_by(id=user_id, is_active=True).first_or_404()
    novo_perfil = (request.form.get("profile") or "").strip()
    if novo_perfil not in PERFIS_PERMITIDOS:
        flash("Perfil informado é inválido.", "danger")
        abort(400)

    perfil_anterior = user.profile
    if perfil_anterior == novo_perfil:
        flash("Perfil atualizado com sucesso.", "success")
        return redirect(url_for("admin.gestao_usuarios"))

    if perfil_anterior == "Admin" and novo_perfil != "Admin":
        quantidade_admins = User.query.filter_by(profile="Admin").count()
        if quantidade_admins <= 1:
            flash("Não é possível remover o perfil do único administrador do sistema.", "danger")
            return redirect(url_for("admin.gestao_usuarios"))

    user.profile = novo_perfil
    registrar_auditoria(
        acao=AuditAction.ALTERAR_USUARIO,
        modulo="Administração",
        entidade="User",
        entidade_id=user.id,
        descricao=f"Perfil do usuário {user.username} alterado por {current_user.username}.",
        alteracoes={"profile": {"anterior": perfil_anterior, "novo": novo_perfil}},
        commit=False,
        raise_on_error=True,
    )
    db.session.commit()
    flash("Perfil atualizado com sucesso.", "success")
    return redirect(url_for("admin.gestao_usuarios"))


@admin_bp.route("/admin/usuarios/<int:user_id>/excluir", methods=["POST"])
def excluir_usuario(user_id):
    if not current_user.is_authenticated:
        return _delete_user_response("Autenticação necessária.", 401)

    actor = User.query.filter_by(id=current_user.id, is_active=True).with_for_update().first()
    if not actor or actor.profile != "Admin":
        registrar_auditoria(
            acao=AuditAction.USER_DELETE_DENIED,
            modulo="Administração",
            entidade="User",
            entidade_id=user_id,
            descricao="Tentativa não autorizada de exclusão de usuário.",
            resultado="NEGADO",
        )
        return _delete_user_response("Você não possui permissão para excluir usuários.", 403)

    user = User.query.filter_by(id=user_id).with_for_update().first()
    if not user:
        registrar_auditoria(
            acao=AuditAction.USER_DELETE_DENIED,
            modulo="Administração",
            entidade="User",
            entidade_id=user_id,
            descricao=f"Tentativa de excluir usuário inexistente: {user_id}.",
            resultado="NEGADO",
        )
        return _delete_user_response("Usuário não encontrado.", 404)

    if user.id == actor.id:
        registrar_auditoria(
            acao=AuditAction.USER_DELETE_DENIED,
            modulo="Administração",
            entidade="User",
            entidade_id=user.id,
            descricao="Administrador tentou excluir a própria conta.",
            alteracoes={"profile": {"anterior": user.profile, "novo": user.profile}},
            resultado="NEGADO",
        )
        return _delete_user_response("Você não pode excluir a própria conta.", 400)

    if not user.is_active or user.deleted_at is not None:
        registrar_auditoria(
            acao=AuditAction.USER_DELETE_DENIED,
            modulo="Administração",
            entidade="User",
            entidade_id=user.id,
            descricao=f"Tentativa de excluir usuário já excluído: {user.username}.",
            alteracoes={"is_active": {"anterior": user.is_active, "novo": user.is_active}},
            resultado="NEGADO",
        )
        return _delete_user_response("Usuário já está excluído.", 409)

    if user.profile == "Admin":
        registrar_auditoria(
            acao=AuditAction.USER_DELETE_DENIED,
            modulo="Administração",
            entidade="User",
            entidade_id=user.id,
            descricao=f"Tentativa de excluir administrador: {user.username}.",
            alteracoes={"profile": {"anterior": user.profile, "novo": user.profile}},
            resultado="NEGADO",
        )
        return _delete_user_response("Administradores não podem ser excluídos.", 403)

    if user.profile not in {"User", "Viewer"}:
        registrar_auditoria(
            acao=AuditAction.USER_DELETE_DENIED,
            modulo="Administração",
            entidade="User",
            entidade_id=user.id,
            descricao=f"Tentativa de excluir usuário com perfil não permitido: {user.profile}.",
            alteracoes={"profile": {"anterior": user.profile, "novo": user.profile}},
            resultado="NEGADO",
        )
        return _delete_user_response("Perfil do usuário não permite exclusão.", 403)

    old_profile = user.profile
    old_active = user.is_active
    try:
        user.is_active = False
        user.deleted_at = utc_now()
        user.deleted_by_id = actor.id
        registrar_auditoria(
            acao=AuditAction.USER_DELETED,
            modulo="Administração",
            entidade="User",
            entidade_id=user.id,
            descricao=f"Usuário excluído logicamente: {user.username}.",
            alteracoes={
                "profile": {"anterior": old_profile, "novo": old_profile},
                "is_active": {"anterior": old_active, "novo": False},
                "deleted_by_id": {"anterior": None, "novo": actor.id},
                "deleted_at": {"anterior": None, "novo": user.deleted_at.isoformat()},
            },
            commit=False,
            raise_on_error=True,
        )
        db.session.commit()
    except Exception:
        db.session.rollback()
        return _delete_user_response("Não foi possível excluir o usuário.", 500)
    return _delete_user_response("Usuário excluído com sucesso.", 200, category="success")


@admin_bp.route("/admin/biblioteca", methods=["GET"])
@admin_required
def biblioteca():
    termo = request.args.get("q", "").strip()
    like_term = f"%{termo}%" if termo else None

    status_query = StatusIncidente.query
    types_query = TipoIncidente.query
    commands_query = OrganizationalCommand.query
    units_query = OrganizationalUnit.query.join(OrganizationalCommand)

    if like_term:
        status_query = status_query.filter(StatusIncidente.status.ilike(like_term))
        types_query = types_query.filter(TipoIncidente.tipo_incidente.ilike(like_term))
        commands_query = commands_query.filter(OrganizationalCommand.name.ilike(like_term))
        units_query = units_query.filter(
            db.or_(
                OrganizationalUnit.name.ilike(like_term),
                OrganizationalCommand.name.ilike(like_term),
            )
        )

    statuses = status_query.order_by(StatusIncidente.status.asc()).all()
    incident_types = types_query.order_by(TipoIncidente.tipo_incidente.asc()).all()
    commands = _sort_commands_query(commands_query).all()
    units = _sort_units_query(units_query).all()
    all_commands = _sort_commands_query(OrganizationalCommand.query.filter_by(active=True)).all()

    status_counts = {item.id: _status_incident_count(item.status) for item in statuses}
    type_counts = {item.id: _type_incident_count(item.tipo_incidente) for item in incident_types}
    command_unit_counts = {
        item.id: OrganizationalUnit.query.filter_by(command_id=item.id, active=True).count()
        for item in commands
    }
    command_incident_counts = {item.id: _command_incident_count(item) for item in commands}
    unit_incident_counts = {item.id: _unit_incident_count(item) for item in units}

    registrar_auditoria(
        acao=AuditAction.VISUALIZAR,
        modulo="Administração - Biblioteca",
        entidade="Biblioteca",
        descricao="Consulta à Biblioteca administrativa.",
    )

    return render_template(
        "admin/biblioteca.html",
        title="Biblioteca",
        q=termo,
        statuses=statuses,
        incident_types=incident_types,
        commands=commands,
        units=units,
        all_commands=all_commands,
        status_counts=status_counts,
        type_counts=type_counts,
        command_unit_counts=command_unit_counts,
        command_incident_counts=command_incident_counts,
        unit_incident_counts=unit_incident_counts,
    )


@admin_bp.route("/admin/biblioteca/status/criar", methods=["POST"])
@admin_required
def biblioteca_status_criar():
    name = _validate_library_name(request.form.get("name"), "Nome do status")
    if not name:
        return _library_redirect("status")
    if _has_duplicate(StatusIncidente.query.all(), "status", name):
        flash("Já existe um status cadastrado com esse nome.", "danger")
        return _library_redirect("status")

    status = StatusIncidente(status=name, desc_status="")
    try:
        db.session.add(status)
        db.session.flush()
        _audit_library_change(
            AuditAction.CRIAR,
            "StatusIncidente",
            status.id,
            f"Status de incidente criado: {name}.",
            {"status": {"anterior": None, "novo": name}},
        )
        db.session.commit()
        flash("Status criado com sucesso.", "success")
    except IntegrityError:
        db.session.rollback()
        flash("Não foi possível criar o status. Verifique duplicidade.", "danger")
    return _library_redirect("status")


@admin_bp.route("/admin/biblioteca/status/<int:status_id>/editar", methods=["POST"])
@admin_required
def biblioteca_status_editar(status_id):
    status = StatusIncidente.query.get_or_404(status_id)
    name = _validate_library_name(request.form.get("name"), "Nome do status")
    if not name:
        return _library_redirect("status")
    if _has_duplicate(StatusIncidente.query.all(), "status", name, current_id=status.id):
        flash("Já existe um status cadastrado com esse nome.", "danger")
        return _library_redirect("status")
    old_name = status.status
    if old_name == name:
        flash("Status atualizado com sucesso.", "success")
        return _library_redirect("status")

    try:
        status.status = name
        Incidente.query.filter_by(status_incident=old_name).update(
            {Incidente.status_incident: name},
            synchronize_session=False,
        )
        _audit_library_change(
            AuditAction.EDITAR,
            "StatusIncidente",
            status.id,
            f"Status de incidente renomeado de {old_name} para {name}.",
            {"status": {"anterior": old_name, "novo": name}},
        )
        db.session.commit()
        flash("Status atualizado com sucesso.", "success")
    except IntegrityError:
        db.session.rollback()
        flash("Não foi possível atualizar o status. Verifique duplicidade.", "danger")
    return _library_redirect("status")


@admin_bp.route("/admin/biblioteca/status/<int:status_id>/excluir", methods=["POST"])
@admin_required
def biblioteca_status_excluir(status_id):
    status = StatusIncidente.query.get_or_404(status_id)
    linked_count = _status_incident_count(status.status)
    if linked_count:
        flash("Não é possível excluir um status vinculado a incidentes.", "danger")
        return _library_redirect("status")
    try:
        db.session.delete(status)
        _audit_library_change(
            AuditAction.EXCLUIR,
            "StatusIncidente",
            status_id,
            f"Status de incidente excluído: {status.status}.",
            {"status": {"anterior": status.status, "novo": None}},
        )
        db.session.commit()
        flash("Status excluído com sucesso.", "success")
    except IntegrityError:
        db.session.rollback()
        flash("Não foi possível excluir o status.", "danger")
    return _library_redirect("status")


@admin_bp.route("/admin/biblioteca/tipo/criar", methods=["POST"])
@admin_required
def biblioteca_tipo_criar():
    name = _validate_library_name(request.form.get("name"), "Nome do tipo de incidente")
    if not name:
        return _library_redirect("tipos")
    if _has_duplicate(TipoIncidente.query.all(), "tipo_incidente", name):
        flash("Já existe um tipo de incidente cadastrado com esse nome.", "danger")
        return _library_redirect("tipos")

    incident_type = TipoIncidente(tipo_incidente=name, desc_incidente="")
    try:
        db.session.add(incident_type)
        db.session.flush()
        _audit_library_change(
            AuditAction.CRIAR,
            "TipoIncidente",
            incident_type.id,
            f"Tipo de incidente criado: {name}.",
            {"tipo_incidente": {"anterior": None, "novo": name}},
        )
        db.session.commit()
        flash("Tipo de incidente criado com sucesso.", "success")
    except IntegrityError:
        db.session.rollback()
        flash("Não foi possível criar o tipo. Verifique duplicidade.", "danger")
    return _library_redirect("tipos")


@admin_bp.route("/admin/biblioteca/tipo/<int:type_id>/editar", methods=["POST"])
@admin_required
def biblioteca_tipo_editar(type_id):
    incident_type = TipoIncidente.query.get_or_404(type_id)
    name = _validate_library_name(request.form.get("name"), "Nome do tipo de incidente")
    if not name:
        return _library_redirect("tipos")
    if _has_duplicate(TipoIncidente.query.all(), "tipo_incidente", name, current_id=incident_type.id):
        flash("Já existe um tipo de incidente cadastrado com esse nome.", "danger")
        return _library_redirect("tipos")
    old_name = incident_type.tipo_incidente
    if old_name == name:
        flash("Tipo de incidente atualizado com sucesso.", "success")
        return _library_redirect("tipos")

    try:
        incident_type.tipo_incidente = name
        Incidente.query.filter_by(incident_type=old_name).update(
            {Incidente.incident_type: name},
            synchronize_session=False,
        )
        _audit_library_change(
            AuditAction.EDITAR,
            "TipoIncidente",
            incident_type.id,
            f"Tipo de incidente renomeado de {old_name} para {name}.",
            {"tipo_incidente": {"anterior": old_name, "novo": name}},
        )
        db.session.commit()
        flash("Tipo de incidente atualizado com sucesso.", "success")
    except IntegrityError:
        db.session.rollback()
        flash("Não foi possível atualizar o tipo. Verifique duplicidade.", "danger")
    return _library_redirect("tipos")


@admin_bp.route("/admin/biblioteca/tipo/<int:type_id>/excluir", methods=["POST"])
@admin_required
def biblioteca_tipo_excluir(type_id):
    incident_type = TipoIncidente.query.get_or_404(type_id)
    linked_count = _type_incident_count(incident_type.tipo_incidente)
    if linked_count:
        flash("Não é possível excluir um tipo vinculado a incidentes.", "danger")
        return _library_redirect("tipos")
    try:
        db.session.delete(incident_type)
        _audit_library_change(
            AuditAction.EXCLUIR,
            "TipoIncidente",
            type_id,
            f"Tipo de incidente excluído: {incident_type.tipo_incidente}.",
            {"tipo_incidente": {"anterior": incident_type.tipo_incidente, "novo": None}},
        )
        db.session.commit()
        flash("Tipo de incidente excluído com sucesso.", "success")
    except IntegrityError:
        db.session.rollback()
        flash("Não foi possível excluir o tipo de incidente.", "danger")
    return _library_redirect("tipos")


@admin_bp.route("/admin/biblioteca/cpa/criar", methods=["POST"])
@admin_required
def biblioteca_cpa_criar():
    name = _validate_library_name(request.form.get("name"), "Nome do CPA/Grande Comando")
    if not name:
        return _library_redirect("cpas")
    if _has_duplicate(OrganizationalCommand.query.all(), "name", name):
        flash("Já existe um CPA/Grande Comando cadastrado com esse nome.", "danger")
        return _library_redirect("cpas")

    sort_order = (db.session.query(db.func.max(OrganizationalCommand.sort_order)).scalar() or 0) + 1
    command = OrganizationalCommand(name=name, active=True, sort_order=sort_order)
    try:
        db.session.add(command)
        db.session.flush()
        _audit_library_change(
            AuditAction.CRIAR,
            "OrganizationalCommand",
            command.id,
            f"CPA/Grande Comando criado: {name}.",
            {"name": {"anterior": None, "novo": name}},
        )
        db.session.commit()
        flash("CPA/Grande Comando criado com sucesso.", "success")
    except IntegrityError:
        db.session.rollback()
        flash("Não foi possível criar o CPA/Grande Comando. Verifique duplicidade.", "danger")
    return _library_redirect("cpas")


@admin_bp.route("/admin/biblioteca/cpa/<int:command_id>/editar", methods=["POST"])
@admin_required
def biblioteca_cpa_editar(command_id):
    command = OrganizationalCommand.query.get_or_404(command_id)
    name = _validate_library_name(request.form.get("name"), "Nome do CPA/Grande Comando")
    if not name:
        return _library_redirect("cpas")
    if _has_duplicate(OrganizationalCommand.query.all(), "name", name, current_id=command.id):
        flash("Já existe um CPA/Grande Comando cadastrado com esse nome.", "danger")
        return _library_redirect("cpas")
    old_name = command.name
    if old_name == name:
        flash("CPA/Grande Comando atualizado com sucesso.", "success")
        return _library_redirect("cpas")

    try:
        command.name = name
        Incidente.query.filter(db.or_(Incidente.command_id == command.id, Incidente.cpa == old_name)).update(
            {Incidente.cpa: name},
            synchronize_session=False,
        )
        Unidades.query.filter_by(cpa=old_name).update(
            {Unidades.cpa: name},
            synchronize_session=False,
        )
        _audit_library_change(
            AuditAction.EDITAR,
            "OrganizationalCommand",
            command.id,
            f"CPA/Grande Comando renomeado de {old_name} para {name}.",
            {"name": {"anterior": old_name, "novo": name}},
        )
        db.session.commit()
        flash("CPA/Grande Comando atualizado com sucesso.", "success")
    except IntegrityError:
        db.session.rollback()
        flash("Não foi possível atualizar o CPA/Grande Comando.", "danger")
    return _library_redirect("cpas")


@admin_bp.route("/admin/biblioteca/cpa/<int:command_id>/excluir", methods=["POST"])
@admin_required
def biblioteca_cpa_excluir(command_id):
    command = OrganizationalCommand.query.get_or_404(command_id)
    if OrganizationalUnit.query.filter_by(command_id=command.id).count() or _command_incident_count(command):
        flash("Não é possível excluir um CPA/Grande Comando com batalhões ou incidentes vinculados.", "danger")
        return _library_redirect("cpas")
    try:
        db.session.delete(command)
        _audit_library_change(
            AuditAction.EXCLUIR,
            "OrganizationalCommand",
            command_id,
            f"CPA/Grande Comando excluído: {command.name}.",
            {"name": {"anterior": command.name, "novo": None}},
        )
        db.session.commit()
        flash("CPA/Grande Comando excluído com sucesso.", "success")
    except IntegrityError:
        db.session.rollback()
        flash("Não foi possível excluir o CPA/Grande Comando.", "danger")
    return _library_redirect("cpas")


@admin_bp.route("/admin/biblioteca/unidade/criar", methods=["POST"])
@admin_required
def biblioteca_unidade_criar():
    name = _validate_library_name(request.form.get("name"), "Nome do Batalhão/Unidade")
    command_id = request.form.get("command_id", type=int)
    command = OrganizationalCommand.query.filter_by(id=command_id, active=True).first()
    if not name or not command:
        if not command:
            flash("Selecione um CPA/Grande Comando válido.", "danger")
        return _library_redirect("unidades")

    normalized_name = _unit_normalized_name(name)
    if OrganizationalUnit.query.filter_by(command_id=command.id, normalized_name=normalized_name).first():
        flash("Já existe um Batalhão/Unidade com esse nome neste CPA/Grande Comando.", "danger")
        return _library_redirect("unidades")

    unit = OrganizationalUnit(
        command_id=command.id,
        name=name,
        normalized_name=normalized_name,
        active=True,
        sort_order=_next_unit_sort_order(command.id),
    )
    try:
        db.session.add(unit)
        db.session.add(Unidades(cpa=command.name, btl=name))
        db.session.flush()
        _audit_library_change(
            AuditAction.CRIAR,
            "OrganizationalUnit",
            unit.id,
            f"Batalhão/Unidade criado: {name} em {command.name}.",
            {"name": {"anterior": None, "novo": name}, "command_id": {"anterior": None, "novo": command.id}},
        )
        db.session.commit()
        flash("Batalhão/Unidade criado com sucesso.", "success")
    except IntegrityError:
        db.session.rollback()
        flash("Não foi possível criar o Batalhão/Unidade. Verifique duplicidade.", "danger")
    return _library_redirect("unidades")


@admin_bp.route("/admin/biblioteca/unidade/<int:unit_id>/editar", methods=["POST"])
@admin_required
def biblioteca_unidade_editar(unit_id):
    unit = OrganizationalUnit.query.get_or_404(unit_id)
    name = _validate_library_name(request.form.get("name"), "Nome do Batalhão/Unidade")
    command_id = request.form.get("command_id", type=int)
    command = OrganizationalCommand.query.filter_by(id=command_id, active=True).first()
    if not name or not command:
        if not command:
            flash("Selecione um CPA/Grande Comando válido.", "danger")
        return _library_redirect("unidades")

    normalized_name = _unit_normalized_name(name)
    duplicate = OrganizationalUnit.query.filter_by(
        command_id=command.id,
        normalized_name=normalized_name,
    ).filter(OrganizationalUnit.id != unit.id).first()
    if duplicate:
        flash("Já existe um Batalhão/Unidade com esse nome neste CPA/Grande Comando.", "danger")
        return _library_redirect("unidades")

    old_name = unit.name
    old_command_id = unit.command_id
    old_command_name = unit.command.name if unit.command else ""
    if old_name == name and old_command_id == command.id:
        flash("Batalhão/Unidade atualizado com sucesso.", "success")
        return _library_redirect("unidades")

    try:
        unit.name = name
        unit.normalized_name = normalized_name
        unit.command_id = command.id
        Incidente.query.filter(db.or_(Incidente.unit_id == unit.id, Incidente.btl == old_name)).update(
            {Incidente.unit_id: unit.id, Incidente.command_id: command.id, Incidente.cpa: command.name, Incidente.btl: name},
            synchronize_session=False,
        )
        legacy = Unidades.query.filter_by(cpa=old_command_name, btl=old_name).first()
        if legacy:
            legacy.cpa = command.name
            legacy.btl = name
        elif not Unidades.query.filter_by(cpa=command.name, btl=name).first():
            db.session.add(Unidades(cpa=command.name, btl=name))
        _audit_library_change(
            AuditAction.EDITAR,
            "OrganizationalUnit",
            unit.id,
            f"Batalhão/Unidade atualizado: {old_name} para {name}.",
            {
                "name": {"anterior": old_name, "novo": name},
                "command_id": {"anterior": old_command_id, "novo": command.id},
            },
        )
        db.session.commit()
        flash("Batalhão/Unidade atualizado com sucesso.", "success")
    except IntegrityError:
        db.session.rollback()
        flash("Não foi possível atualizar o Batalhão/Unidade.", "danger")
    return _library_redirect("unidades")


@admin_bp.route("/admin/biblioteca/unidade/<int:unit_id>/excluir", methods=["POST"])
@admin_required
def biblioteca_unidade_excluir(unit_id):
    unit = OrganizationalUnit.query.get_or_404(unit_id)
    unit_name = unit.name
    command_name = unit.command.name if unit.command else ""
    if _unit_incident_count(unit):
        flash("Não é possível excluir um Batalhão/Unidade vinculado a incidentes.", "danger")
        return _library_redirect("unidades")
    try:
        db.session.delete(unit)
        legacy = Unidades.query.filter_by(cpa=command_name, btl=unit_name).first()
        if legacy:
            db.session.delete(legacy)
        _audit_library_change(
            AuditAction.EXCLUIR,
            "OrganizationalUnit",
            unit_id,
            f"Batalhão/Unidade excluído: {unit_name} em {command_name}.",
            {"name": {"anterior": unit_name, "novo": None}, "command": {"anterior": command_name, "novo": None}},
        )
        db.session.commit()
        flash("Batalhão/Unidade excluído com sucesso.", "success")
    except IntegrityError:
        db.session.rollback()
        flash("Não foi possível excluir o Batalhão/Unidade.", "danger")
    return _library_redirect("unidades")


@admin_bp.route("/admin/logs-auditoria", methods=["GET"])
@admin_required
def audit_logs():
    page = max(request.args.get("page", 1, type=int), 1)
    per_page = min(max(request.args.get("per_page", 50, type=int), 1), 100)
    query = AuditLog.query

    start_date = _parse_date(request.args.get("start_date"))
    end_date = _parse_date(request.args.get("end_date"), end=True)
    usuario = request.args.get("usuario", "").strip()
    acao = request.args.get("acao", "").strip()
    resultado = request.args.get("resultado", "").strip()

    if start_date:
        query = query.filter(AuditLog.timestamp >= start_date)
    if end_date:
        query = query.filter(AuditLog.timestamp <= end_date)
    if usuario:
        query = query.filter(AuditLog.usuario_identificacao.ilike(f"%{usuario}%"))
    if acao:
        query = query.filter(AuditLog.acao == acao)
    if resultado:
        query = query.filter(AuditLog.resultado == resultado)

    pagination = query.order_by(AuditLog.timestamp.desc()).paginate(
        page=page,
        per_page=per_page,
        error_out=False,
    )
    filtros = {
        "start_date": request.args.get("start_date", ""),
        "end_date": request.args.get("end_date", ""),
        "usuario": usuario,
        "acao": acao,
        "resultado": resultado,
        "per_page": str(per_page),
    }
    pagination_args = {key: value for key, value in filtros.items() if value}

    registrar_auditoria(
        acao=AuditAction.VISUALIZAR,
        modulo="Administração",
        entidade="AuditLog",
        descricao="Consulta aos logs de auditoria.",
    )

    return render_template(
        "admin/audit_logs.html",
        title="Logs de auditoria",
        logs=pagination.items,
        pagination=pagination,
        filtros=filtros,
        pagination_args=pagination_args,
        action_options=[
            AuditAction.LOGIN,
            AuditAction.LOGOUT,
            AuditAction.LOGIN_FALHOU,
            AuditAction.CRIAR,
            AuditAction.EDITAR,
            AuditAction.EXCLUIR,
            AuditAction.VISUALIZAR,
            AuditAction.ALTERAR_SENHA,
            AuditAction.CRIAR_USUARIO,
            AuditAction.ALTERAR_USUARIO,
            AuditAction.ADICIONAR_OBSERVACAO,
            AuditAction.EXCLUIR_OBSERVACAO,
            AuditAction.ACESSO_NEGADO,
            AuditAction.IMPORTAR_CREDENCIAIS,
        ],
    )


@admin_bp.route("/admin/logs-auditoria/<int:log_id>", methods=["GET"])
@admin_required
def audit_log_detail(log_id):
    audit_log = AuditLog.query.get_or_404(log_id)
    registrar_auditoria(
        acao=AuditAction.VISUALIZAR,
        modulo="Administração",
        entidade="AuditLog",
        entidade_id=audit_log.id,
        descricao="Visualização detalhada de log de auditoria.",
    )
    return render_template("admin/audit_log_detail.html", title="Detalhe do log", log=audit_log)
