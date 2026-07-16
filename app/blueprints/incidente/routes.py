# app/blueprints/analise/routes.py

from flask import abort, jsonify, render_template, url_for, flash, redirect, request, current_app, send_file
from app.blueprints.incidente import incidente_bp
from app.models import Incidente, User, IncidenteObs, Unidades, StatusIncidente, TipoIncidente, IncidentAttachment, OrganizationalCommand, OrganizationalUnit
from app import db
from flask_login import login_required, current_user
from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo
from sqlalchemy import String, cast, or_
from urllib.parse import urlsplit
from types import SimpleNamespace
import plotly.express as px
from app.blueprints.users.routes import allowed_edit_profile
from app.services.audit_service import AuditAction, montar_alteracoes, registrar_auditoria
from app.services.attachment_service import (
    AttachmentValidationError,
    delete_attachment_file,
    resolve_attachment_path,
    save_incident_attachments,
)
from app.services.content_sanitizer import SanitizationError, sanitize_incident_description

MAX_SEARCH_LENGTH = 200
INCIDENTS_PER_PAGE = 10
SAO_PAULO_TZ = ZoneInfo("America/Sao_Paulo")
TIPOS_INCIDENTE_PERMITIDOS = {
    "Requisições automatizadas",
    "Transferência de arquivo malicioso",
    "Bloqueio de acesso a VPN",
    "Phishing",
    "Comando e Controle",
    "Incidente envolvendo VPN corporativa",
    "Criptomining",
    "Malware",
    "Ativador KMS",
    "Tentativa de intrusão",
    "Comprometimento de Credenciais",
    "Quebra de Confidencialidade",
    "Brute Force",
}


def _sort_units_query(query):
    return query.order_by(
        OrganizationalUnit.sort_order.is_(None),
        OrganizationalUnit.sort_order.asc(),
        OrganizationalUnit.name.asc(),
    )


def _organizational_form_options():
    commands = OrganizationalCommand.query.filter_by(active=True).order_by(
        OrganizationalCommand.sort_order.is_(None),
        OrganizationalCommand.sort_order.asc(),
        OrganizationalCommand.name.asc(),
    ).all()
    units = _sort_units_query(
        OrganizationalUnit.query.filter_by(active=True)
    ).all()
    return commands, units


def _resolve_incident_organization():
    command_id = request.form.get("command_id", type=int)
    unit_id = request.form.get("unit_id", type=int)

    if not command_id or not unit_id:
        cpa = request.form.get("cpa", "").strip()
        btl = request.form.get("btl", "").strip()
        if cpa and btl:
            command = OrganizationalCommand.query.filter_by(name=cpa, active=True).first()
            unit = None
            if command:
                unit = OrganizationalUnit.query.filter_by(command_id=command.id, name=btl, active=True).first()
            if command and unit:
                return command, unit
        abort(400, description="CPA/Grande Comando e Batalhão/Unidade são obrigatórios.")

    command = OrganizationalCommand.query.filter_by(id=command_id, active=True).first()
    unit = OrganizationalUnit.query.filter_by(id=unit_id, active=True).first()
    if not command or not unit:
        abort(400, description="CPA/Grande Comando ou Batalhão/Unidade inválido.")
    if unit.command_id != command.id:
        abort(400, description="O Batalhão/Unidade selecionado não pertence ao CPA/Grande Comando informado.")
    return command, unit


def _hydrate_incident_organization(incident):
    if not incident or (getattr(incident, "command_id", None) and getattr(incident, "unit_id", None)):
        return incident
    command = OrganizationalCommand.query.filter_by(name=getattr(incident, "cpa", None), active=True).first()
    if not command:
        return incident
    unit = OrganizationalUnit.query.filter_by(
        command_id=command.id,
        name=getattr(incident, "btl", None),
        active=True,
    ).first()
    if unit:
        incident.command_id = command.id
        incident.unit_id = unit.id
    return incident


@incidente_bp.route("/api/organizational-commands/<int:command_id>/units", methods=["GET"])
@login_required
def api_organizational_command_units(command_id):
    command = OrganizationalCommand.query.filter_by(id=command_id, active=True).first_or_404()
    units = _sort_units_query(
        OrganizationalUnit.query.filter_by(command_id=command.id, active=True)
    ).all()
    return jsonify({
        "command": {"id": command.id, "name": command.name},
        "units": [{"id": unit.id, "name": unit.name} for unit in units],
    })
TIPOS_INCIDENTE_FORM = sorted(TIPOS_INCIDENTE_PERMITIDOS)


def _mojibake_variant(value):
    return value.encode("utf-8").decode("latin-1")


LEGACY_TEXT_VARIANTS = {
    "Tentativa de intrusão": [_mojibake_variant("Tentativa de intrusão")],
    "Requisições automatizadas": [_mojibake_variant("Requisições automatizadas")],
    "Transferência de arquivo malicioso": [_mojibake_variant("Transferência de arquivo malicioso")],
    "Em Análise": [_mojibake_variant("Em Análise")],
    "Em Mitigação": [_mojibake_variant("Em Mitigação")],
}
SORTABLE_INCIDENT_FIELDS = {
    "start_date": Incidente.start_date,
    "incident_type": Incidente.incident_type,
    "report_number": Incidente.report_number,
}


def _is_safe_internal_path(path):
    if not path:
        return False
    parsed = urlsplit(path)
    return not parsed.scheme and not parsed.netloc and path.startswith("/") and not path.startswith("//")


def _ensure_incident_owner_or_admin(incident, action):
    """Prevents BOLA/IDOR: users can mutate only their own incidents."""
    if current_user.profile == "Admin" or incident.user_id == current_user.id:
        return
    registrar_auditoria(
        acao=AuditAction.ACESSO_NEGADO,
        modulo="Incidentes de segurança",
        entidade="Incidente",
        entidade_id=incident.id,
        descricao=f"Tentativa negada de {action} incidente de outro usuário.",
        resultado="NEGADO",
    )
    abort(403)


def _today_local_date():
    return datetime.now(SAO_PAULO_TZ).date()


def _parse_registration_date(value):
    try:
        parsed = datetime.strptime((value or "").strip(), "%Y-%m-%d")
    except ValueError as exc:
        raise ValueError("Formato de data inválido.") from exc
    return parsed.replace(hour=0, minute=0, second=0, microsecond=0)


def _get_incident_types_for_form(current_value=None):
    values = list(TIPOS_INCIDENTE_FORM)
    if current_value and current_value not in values:
        values.append(current_value)
    return values


def _filter_values_with_legacy(value):
    values = [value]
    values.extend(LEGACY_TEXT_VARIANTS.get(value, []))
    return values


def _incident_draft_from_form(description=None, description_plain_text=""):
    registration_date = (request.form.get("registration_date") or request.form.get("start_data_hora", "")[:10]).strip()
    start_date = None
    if registration_date:
        try:
            start_date = _parse_registration_date(registration_date)
        except ValueError:
            start_date = None
    return SimpleNamespace(
        id=None,
        status_incident=request.form.get("status_incidente", "").strip(),
        start_date=start_date,
        incident_type=request.form.get("incident_type", "").strip(),
        report_number=request.form.get("report_number", "").strip(),
        message_number=request.form.get("message_number", "").strip(),
        ticket_number=request.form.get("ticket_number", "").strip(),
        btl=request.form.get("btl", "").strip(),
        cpa=request.form.get("cpa", "").strip(),
        command_id=request.form.get("command_id", type=int),
        unit_id=request.form.get("unit_id", type=int),
        cia=request.form.get("cia", "").strip(),
        description=description if description is not None else request.form.get("description", ""),
        description_plain_text=description_plain_text,
        attachments=[],
    )


def _apply_form_to_incident_like(target, *, description, description_plain_text):
    target.status_incident = request.form.get("status_incidente", "").strip()
    registration_date = (request.form.get("registration_date") or request.form.get("start_data_hora", "")[:10]).strip()
    target.start_date = _parse_registration_date(registration_date) if registration_date else None
    target.incident_type = request.form.get("incident_type", "").strip()
    target.report_number = request.form.get("report_number", "").strip()
    target.message_number = request.form.get("message_number", "").strip() or None
    target.ticket_number = request.form.get("ticket_number", "").strip() or None
    target.btl = request.form.get("btl", "").strip()
    target.cpa = request.form.get("cpa", "").strip()
    target.command_id = request.form.get("command_id", type=int)
    target.unit_id = request.form.get("unit_id", type=int)
    target.cia = request.form.get("cia", "").strip()
    target.description = description
    target.description_plain_text = description_plain_text
    return target


def _incident_edit_draft(original_incident, description=None, description_plain_text=""):
    draft = _incident_draft_from_form(description=description, description_plain_text=description_plain_text)
    draft.id = original_incident.id
    draft.attachments = list(original_incident.attachments or [])
    return draft


def _render_incident_form_response(*, title, unidades, status_incident_list, incidents_types, data_atual, incident=None, edit_mode=False, status_code=200, commands=None, organizational_units=None):
    if commands is None or organizational_units is None:
        commands, organizational_units = _organizational_form_options()
    return render_template(
        "incidente/new_incident.html",
        title=title,
        incident=_hydrate_incident_organization(incident),
        edit_mode=edit_mode,
        unidades=unidades,
        commands=commands or [],
        organizational_units=organizational_units or [],
        status_incident_list=status_incident_list,
        incidents_types=incidents_types,
        data_atual=data_atual,
    ), status_code


def _is_inline_attachment(attachment):
    return attachment.mime_type in {"application/pdf", "image/png", "image/jpeg", "image/webp"}


def _format_incident_durations(incidentes):
    now = datetime.now()
    incidentes_com_tempo = []
    for inc in incidentes:
        start_date_aware = inc.start_date.replace()

        if inc.end_date:
            end_date_aware = inc.end_date.replace()
            duracao = end_date_aware - start_date_aware
        else:
            duracao = now - start_date_aware

        inc.tempo_aberto_formatado = format_timedelta(duracao)
        incidentes_com_tempo.append(inc)
    return incidentes_com_tempo


def _build_incidents_query():
    status_filter = request.args.get('status_filter')
    direction_filter = request.args.get('direction', 'desc')
    sort_by = request.args.get('sort_by', 'start_date')
    termo = request.args.get('q', '').strip()

    if len(termo) > MAX_SEARCH_LENGTH:
        abort(400)

    query = Incidente.query

    if status_filter and status_filter != 'todos':
        query = query.filter(Incidente.status_incident == status_filter)

    if termo:
        padrao = f"%{termo}%"
        query = query.filter(or_(
            cast(Incidente.id, String).ilike(padrao),
            Incidente.incident_type.ilike(padrao),
            Incidente.report_number.ilike(padrao),
            Incidente.message_number.ilike(padrao),
            Incidente.ticket_number.ilike(padrao),
            Incidente.status_incident.ilike(padrao),
            Incidente.cpa.ilike(padrao),
            Incidente.btl.ilike(padrao),
            Incidente.cia.ilike(padrao),
            Incidente.description_plain_text.ilike(padrao),
            cast(Incidente.start_date, String).ilike(padrao),
            cast(Incidente.end_date, String).ilike(padrao),
            Incidente.autor.has(or_(
                User.name.ilike(padrao),
                User.username.ilike(padrao),
                User.email.ilike(padrao),
            )),
            Incidente.obs_incidente.any(or_(
                IncidenteObs.texto_observacao.ilike(padrao),
                cast(IncidenteObs.data_observacao, String).ilike(padrao),
                IncidenteObs.autor_obs.has(or_(
                    User.name.ilike(padrao),
                    User.username.ilike(padrao),
                    User.email.ilike(padrao),
                )),
            )),
            Incidente.attachments.any(IncidentAttachment.original_filename.ilike(padrao)),
        ))

    sort_column = SORTABLE_INCIDENT_FIELDS.get(sort_by, Incidente.start_date)
    if direction_filter == 'asc':
        query = query.order_by(db.asc(sort_column))
    else:
        direction_filter = 'desc'
        query = query.order_by(db.desc(sort_column))

    return query, {
        "status_filter": status_filter,
        "direction_filter": direction_filter,
        "sort_by": sort_by if sort_by in SORTABLE_INCIDENT_FIELDS else "start_date",
        "q": termo,
    }


def _render_incident_list_context():
    page = request.args.get('page', 1, type=int)
    query, filters = _build_incidents_query()
    pagination = query.paginate(page=page, per_page=INCIDENTS_PER_PAGE, error_out=False)
    incidentes = _format_incident_durations(pagination.items)
    return incidentes, pagination, filters




# Função auxiliar para formatar timedelta em uma string legível ##  PASSAR ESSA FUNÇÃO PARA UM ARQUIVO UTILIDADES
def format_timedelta(td):
    """Formata um objeto timedelta para uma string legível (Dias, Horas, Minutos)."""
    if not td:
        return "N/A"

    dias_somente = max(0, int(td.total_seconds()) // 86400)
    return f"{dias_somente} dia" if dias_somente == 1 else f"{dias_somente} dias"

    total_segundos = int(td.total_seconds())
    dias, resto = divmod(total_segundos, 86400)
    horas, resto = divmod(resto, 3600)
    minutos, _ = divmod(resto, 60)

    tempo_formatado = []
    if dias > 0:
        tempo_formatado.append(f"{dias}d")
    if horas > 0:
        tempo_formatado.append(f"{horas}h")
    if minutos > 0:
        tempo_formatado.append(f"{minutos}m")

    return " ".join(tempo_formatado) if tempo_formatado else "1m"



################################################################################
#=================================ROTAS INCIDENTE========================
################################################################################





#=================================LISTAR INCIDENTES=================================
@incidente_bp.route("/incidentes", methods=['GET'])
@login_required
def incidents_list():
    incidentes, pagination, filters = _render_incident_list_context()
    total_incidents = Incidente.query.count()
    open_incidents = Incidente.query.filter(Incidente.status_incident != 'Encerrado').count()
    closed_incidents = Incidente.query.filter(Incidente.status_incident == 'Encerrado').count()
    status_options = db.session.query(Incidente.status_incident).distinct().all()

    return render_template('incidente/incidentes.html',
                           title="Incidentes de segurança",
                           incidentes=incidentes,
                           pagination=pagination,
                           total_incidents=total_incidents,
                           open_incidents=open_incidents,
                           closed_incidents=closed_incidents,
                           status_options=status_options,
                           direction_filter=filters["direction_filter"],
                           sort_by=filters["sort_by"],
                           status_filter=filters["status_filter"],
                           q=filters["q"])


@incidente_bp.route("/incidentes/pesquisa", methods=['GET'])
@login_required
def search_incidents():
    incidentes, pagination, filters = _render_incident_list_context()
    return render_template(
        'incidente/_incident_list.html',
        incidentes=incidentes,
        pagination=pagination,
        status_filter=filters["status_filter"],
        direction_filter=filters["direction_filter"],
        sort_by=filters["sort_by"],
        q=filters["q"],
    )


    #recebendo parametros de filtro da URL
    status_filter = request.args.get('status_filter')
    direction_filter = request.args.get('direction', 'desc') # Padrão decrescente pela data de criação
    sort_by = request.args.get('sort_by', 'start_date') # Padrão ordenação pela data de início
    page = request.args.get('page', 1, type=int)
    per_page = 10

    query = Incidente.query

    if status_filter and status_filter != 'todos':
        query = query.filter(Incidente.status_incident == status_filter)

    if sort_by:
        if direction_filter == 'desc':
            query = query.order_by(db.desc(getattr(Incidente, sort_by)))
        else:
            query = query.order_by(db.asc(getattr(Incidente, sort_by)))


    total_incidents = Incidente.query.count()
    open_incidents = Incidente.query.filter(Incidente.status_incident != 'Encerrado').count()
    closed_incidents = Incidente.query.filter(Incidente.status_incident == 'Encerrado').count()
    pagination = query.paginate(page=page, per_page=per_page, error_out=False)
    incidentes = pagination.items



    # Define o momento atual para calcular o tempo aberto
    now = datetime.now()
    print(f"Now UTC: {now}")
    # Itera sobre os incidentes para calcular e anexar o tempo aberto
    incidentes_com_tempo = []
    for inc in incidentes:
        # 1. Torna a data de abertura CONSCIENTE (aware) de UTC
        #    Isso resolve o "offset-naive" do start_date
        start_date_aware = inc.start_date.replace()

        # Se o incidente foi fechado, calcula a duração total (fechamento - abertura)
        if inc.end_date:
            # 2. Torna a data de fechamento CONSCIENTE (aware) de UTC
            end_date_aware = inc.end_date.replace()

            # Agora a subtração é válida: aware - aware
            duracao = end_date_aware - start_date_aware

        # Se o incidente está aberto, calcula a duração até o momento atual (now - abertura)
        else:
            # Subtração válida: aware - aware
            duracao = now - start_date_aware

        # Anexa a string formatada ao objeto incidente
        inc.tempo_aberto_formatado = format_timedelta(duracao)
        incidentes_com_tempo.append(inc)

    # Para o filtro de status no HTML
    status_options = db.session.query(Incidente.status_incident).distinct().all()

    return render_template('incidente/incidentes.html',
                           title="Incidentes de segurança",
                           incidentes = incidentes_com_tempo,
                           pagination=pagination,
                           total_incidents=total_incidents,
                           open_incidents=open_incidents,
                           closed_incidents=closed_incidents,
                           status_options=status_options,
                           direction_filter=direction_filter,
                           sort_by=sort_by,
                           status_filter=status_filter)


#=================================REGISTRAR NOVO INCIDENTE=================================
@incidente_bp.route("/incidente/new", methods=['GET', 'POST'])

@login_required
def new_incident():
    if allowed_edit_profile(current_user): # função para verificar permissão do usuário para edição
        data_atual = _today_local_date()
        unidades = Unidades.query.all()
        commands, organizational_units = _organizational_form_options()
        status_incident_list = StatusIncidente.query.all()
        incidents_types = _get_incident_types_for_form()

        if request.method == 'POST':
            status_incident = request.form.get('status_incidente', '').strip()
            registration_date = (request.form.get('registration_date') or request.form.get('start_data_hora', '')[:10]).strip()
            incident_type = request.form.get('incident_type', '').strip()
            report_number = request.form.get('report_number', '').strip()
            message_number = request.form.get('message_number', '').strip()
            ticket_number = request.form.get('ticket_number', '').strip()
            cia = request.form.get('cia', '').strip()
            raw_description = request.form.get('description', '')
            user_id = current_user.id

            if incident_type not in TIPOS_INCIDENTE_PERMITIDOS:
                flash('Tipo de incidente informado é inválido.', 'danger')
                return _render_incident_form_response(
                    title="Registro de Incidente",
                    unidades=unidades,
                    status_incident_list=status_incident_list,
                    incidents_types=_get_incident_types_for_form(incident_type),
                    data_atual=data_atual,
                    incident=_incident_draft_from_form(),
                    status_code=400,
                )

            try:
                start_date = _parse_registration_date(registration_date)
                description, description_plain_text = sanitize_incident_description(raw_description)
            except (ValueError, SanitizationError) as exc:
                flash(str(exc), 'danger')
                return _render_incident_form_response(
                    title="Registro de Incidente",
                    unidades=unidades,
                    status_incident_list=status_incident_list,
                    incidents_types=_get_incident_types_for_form(incident_type),
                    data_atual=data_atual,
                    incident=_incident_draft_from_form(),
                    status_code=400,
                )

            try:
                command, unit = _resolve_incident_organization()
            except Exception as exc:
                flash(getattr(exc, "description", "O Batalhão/Unidade selecionado não pertence ao CPA/Grande Comando informado."), 'danger')
                return _render_incident_form_response(
                    title="Registro de Incidente",
                    unidades=unidades,
                    commands=commands,
                    organizational_units=organizational_units,
                    status_incident_list=status_incident_list,
                    incidents_types=_get_incident_types_for_form(incident_type),
                    data_atual=data_atual,
                    incident=_incident_draft_from_form(description=description, description_plain_text=description_plain_text),
                    status_code=400,
                )

            cpa = command.name
            btl = unit.name

            if not all([status_incident, registration_date, incident_type, report_number, btl, cpa, description_plain_text]):
                missing_labels = []
                if not status_incident:
                    missing_labels.append("Status")
                if not registration_date:
                    missing_labels.append("Data de registro")
                if not incident_type:
                    missing_labels.append("Tipo de incidente")
                if not report_number:
                    missing_labels.append("Nº relatório")
                if not btl:
                    missing_labels.append("Batalhão/unidade")
                if not cpa:
                    missing_labels.append("CPA/Grande comando")
                if not description_plain_text:
                    missing_labels.append("Descrição")
                flash("Preencha os campos obrigatórios: " + ", ".join(missing_labels) + ".", 'danger')
                return _render_incident_form_response(
                    title="Registro de Incidente",
                    unidades=unidades,
                    status_incident_list=status_incident_list,
                    incidents_types=_get_incident_types_for_form(incident_type),
                    data_atual=data_atual,
                    incident=_incident_draft_from_form(description=description, description_plain_text=description_plain_text),
                    status_code=400,
                )

            new_incident = Incidente(
                status_incident=status_incident,
                start_date=start_date,
                incident_type=incident_type,
                report_number=report_number,
                message_number=message_number or None,
                ticket_number=ticket_number or None,
                btl=btl,
                cpa=cpa,
                command_id=command.id,
                unit_id=unit.id,
                cia=cia,
                description=description,
                description_plain_text=description_plain_text,
                user_id=user_id,
            )

            saved_attachments = []
            try:
                db.session.add(new_incident)
                db.session.flush()
                saved_attachments = save_incident_attachments(request.files.getlist('incident_attachments'), new_incident, current_user)
                for attachment in saved_attachments:
                    db.session.add(attachment)
                registrar_auditoria(
                    acao=AuditAction.CRIAR,
                    modulo="Incidentes de segurança",
                    entidade="Incidente",
                    entidade_id=new_incident.id,
                    descricao=f"Incidente criado: {new_incident.report_number}",
                    alteracoes={
                        "status_incident": {"anterior": None, "novo": status_incident},
                        "start_date": {"anterior": None, "novo": start_date.date()},
                        "incident_type": {"anterior": None, "novo": incident_type},
                        "report_number": {"anterior": None, "novo": report_number},
                        "message_number": {"anterior": None, "novo": message_number},
                        "ticket_number": {"anterior": None, "novo": ticket_number},
                        "btl": {"anterior": None, "novo": btl},
                        "cpa": {"anterior": None, "novo": cpa},
                        "unit_id": {"anterior": None, "novo": unit.id},
                        "command_id": {"anterior": None, "novo": command.id},
                        "cia": {"anterior": None, "novo": cia},
                        "description": {"anterior": None, "novo": "[descrição sanitizada]"},
                        "user_id": {"anterior": None, "novo": user_id},
                    },
                    commit=False,
                    raise_on_error=True,
                )
                for attachment in saved_attachments:
                    registrar_auditoria(
                        acao=AuditAction.UPLOAD_ANEXO,
                        modulo="Incidentes de segurança",
                        entidade="IncidentAttachment",
                        entidade_id=attachment.id,
                        descricao=f"Anexo enviado para incidente {new_incident.id}: {attachment.original_filename}",
                        alteracoes={
                            "incident_id": {"anterior": None, "novo": new_incident.id},
                            "original_filename": {"anterior": None, "novo": attachment.original_filename},
                            "mime_type": {"anterior": None, "novo": attachment.mime_type},
                            "file_size": {"anterior": None, "novo": attachment.file_size},
                            "sha256": {"anterior": None, "novo": attachment.sha256},
                            "uploaded_by_id": {"anterior": None, "novo": user_id},
                        },
                        commit=False,
                        raise_on_error=True,
                    )
                db.session.commit()
            except AttachmentValidationError as exc:
                db.session.rollback()
                flash(str(exc), 'danger')
                return _render_incident_form_response(
                    title="Registro de Incidente",
                    unidades=unidades,
                    status_incident_list=status_incident_list,
                    incidents_types=_get_incident_types_for_form(incident_type),
                    data_atual=data_atual,
                    incident=_incident_draft_from_form(description=description, description_plain_text=description_plain_text),
                    status_code=400,
                )
            except Exception as exc:
                db.session.rollback()
                for attachment in saved_attachments:
                    delete_attachment_file(attachment)
                current_app.logger.exception("Falha ao criar incidente: %s", exc)
                flash('Não foi possível registrar o incidente.', 'danger')
                return _render_incident_form_response(
                    title="Registro de Incidente",
                    unidades=unidades,
                    status_incident_list=status_incident_list,
                    incidents_types=_get_incident_types_for_form(incident_type),
                    data_atual=data_atual,
                    incident=_incident_draft_from_form(description=description, description_plain_text=description_plain_text),
                    status_code=500,
                )
            flash('Incidente registrado com sucesso.', 'success')
            return redirect(url_for('incidente.incidents_list'))

        return _render_incident_form_response(
            title="Registro de Incidente",
            unidades=unidades,
            status_incident_list=status_incident_list,
            incidents_types=incidents_types,
            data_atual=data_atual,
        )
    else:
        flash('Acesso negado: Você não tem permissão para registrar um novo incidente.', 'danger')
        return redirect(url_for('incidente.incidents_list'))


#=================================EDITAR INCIDENTE=================================
@incidente_bp.route("/incidente/<int:incident_id>/edit", methods=['GET', 'POST'])
@login_required
def edit_incident(incident_id): # Rota para editar um incidente
    if not allowed_edit_profile(current_user):
        current_app.logger.info(f"Usuario {current_user.id} tentou editar o incidente {incident_id}. Sem permissão. {current_user.profile}")
        flash('Acesso negado: Você não tem permissão para editar este incidente.', 'danger')
        return redirect(url_for('incidente.incident_view', incident_id=incident_id))

    incident = Incidente.query.get_or_404(incident_id)
    _ensure_incident_owner_or_admin(incident, "editar")
    data_atual = _today_local_date()
    unidades = Unidades.query.all()
    commands, organizational_units = _organizational_form_options()
    status_incident_list = StatusIncidente.query.all()
    incidents_types = _get_incident_types_for_form(incident.incident_type)

    if request.method == 'POST':
        original_data = {
            'status_incident': incident.status_incident,
            'start_date': incident.start_date.strftime('%Y-%m-%d') if incident.start_date else '',
            'incident_type': incident.incident_type,
            'report_number': incident.report_number,
            'message_number': incident.message_number,
            'ticket_number': incident.ticket_number,
            'btl': incident.btl,
            'cpa': incident.cpa,
            'unit_id': incident.unit_id,
            'command_id': incident.command_id,
            'cia': incident.cia,
            'description': incident.description,
            'description_plain_text': incident.description_plain_text,
        }
        status_incident = request.form.get('status_incidente', '').strip()
        registration_date = (request.form.get('registration_date') or request.form.get('start_data_hora', '')[:10]).strip()
        incident_type = request.form.get('incident_type', '').strip()
        report_number = request.form.get('report_number', '').strip()
        message_number = request.form.get('message_number', '').strip()
        ticket_number = request.form.get('ticket_number', '').strip()
        cia = request.form.get('cia', '').strip()
        raw_description = request.form.get('description', '')

        if incident_type not in TIPOS_INCIDENTE_PERMITIDOS and incident_type != original_data['incident_type']:
            flash('Tipo de incidente informado é inválido.', 'danger')
            return _render_incident_form_response(
                title="Editar Incidente",
                unidades=unidades,
                status_incident_list=status_incident_list,
                incidents_types=_get_incident_types_for_form(incident_type),
                data_atual=data_atual,
                incident=_incident_edit_draft(incident),
                edit_mode=True,
                status_code=400,
            )
        try:
            start_date = _parse_registration_date(registration_date)
            description, description_plain_text = sanitize_incident_description(raw_description)
        except (ValueError, SanitizationError) as exc:
            flash(str(exc), 'danger')
            return _render_incident_form_response(
                title="Editar Incidente",
                unidades=unidades,
                status_incident_list=status_incident_list,
                incidents_types=_get_incident_types_for_form(incident_type),
                data_atual=data_atual,
                incident=_incident_edit_draft(incident),
                edit_mode=True,
                status_code=400,
            )
        try:
            command, unit = _resolve_incident_organization()
        except Exception as exc:
            flash(getattr(exc, "description", "O Batalhão/Unidade selecionado não pertence ao CPA/Grande Comando informado."), 'danger')
            return _render_incident_form_response(
                title="Editar Incidente",
                unidades=unidades,
                commands=commands,
                organizational_units=organizational_units,
                status_incident_list=status_incident_list,
                incidents_types=_get_incident_types_for_form(incident_type),
                data_atual=data_atual,
                incident=_incident_edit_draft(incident, description=description, description_plain_text=description_plain_text),
                edit_mode=True,
                status_code=400,
            )

        cpa = command.name
        btl = unit.name

        if not all([status_incident, registration_date, incident_type, report_number, btl, cpa, description_plain_text]):
            missing_labels = []
            if not status_incident:
                missing_labels.append("Status")
            if not registration_date:
                missing_labels.append("Data de registro")
            if not incident_type:
                missing_labels.append("Tipo de incidente")
            if not report_number:
                missing_labels.append("Nº relatório")
            if not btl:
                missing_labels.append("Batalhão/unidade")
            if not cpa:
                missing_labels.append("CPA/Grande comando")
            if not description_plain_text:
                missing_labels.append("Descrição")
            flash("Preencha os campos obrigatórios: " + ", ".join(missing_labels) + ".", 'danger')
            return _render_incident_form_response(
                title="Editar Incidente",
                unidades=unidades,
                status_incident_list=status_incident_list,
                incidents_types=_get_incident_types_for_form(incident_type),
                data_atual=data_atual,
                incident=_incident_edit_draft(incident, description=description, description_plain_text=description_plain_text),
                edit_mode=True,
                status_code=400,
            )

        incident.status_incident = status_incident
        incident.start_date = start_date
        incident.incident_type = incident_type
        incident.report_number = report_number
        incident.message_number = message_number or None
        incident.ticket_number = ticket_number or None
        incident.btl = btl
        incident.cpa = cpa
        incident.command_id = command.id
        incident.unit_id = unit.id
        incident.cia = cia
        incident.description = description
        incident.description_plain_text = description_plain_text
        if status_incident == 'Encerrado' and original_data['status_incident'] != 'Encerrado':
            incident.end_date = datetime.now()

        saved_attachments = []
        try:
            saved_attachments = save_incident_attachments(request.files.getlist('incident_attachments'), incident, current_user)
            for attachment in saved_attachments:
                db.session.add(attachment)
            audit_changes = montar_alteracoes(
                "Incidente",
                original_data,
                {
                    'status_incident': incident.status_incident,
                    'start_date': incident.start_date.strftime('%Y-%m-%d') if incident.start_date else '',
                    'incident_type': incident.incident_type,
                    'report_number': incident.report_number,
                    'message_number': incident.message_number,
                    'ticket_number': incident.ticket_number,
                    'btl': incident.btl,
                    'cpa': incident.cpa,
                    'unit_id': incident.unit_id,
                    'command_id': incident.command_id,
                    'cia': incident.cia,
                    'description': "[alterado]" if original_data['description'] != incident.description else original_data['description'],
                    'description_plain_text': "[alterado]" if original_data['description_plain_text'] != incident.description_plain_text else original_data['description_plain_text'],
                }
            )
            registrar_auditoria(
                acao=AuditAction.EDITAR,
                modulo="Incidentes de segurança",
                entidade="Incidente",
                entidade_id=incident.id,
                descricao=f"Incidente editado: {incident.report_number}",
                alteracoes=audit_changes,
                commit=False,
                raise_on_error=True,
            )
            for attachment in saved_attachments:
                registrar_auditoria(
                    acao=AuditAction.UPLOAD_ANEXO,
                    modulo="Incidentes de segurança",
                    entidade="IncidentAttachment",
                    entidade_id=attachment.id,
                    descricao=f"Anexo enviado para incidente {incident.id}: {attachment.original_filename}",
                    alteracoes={
                        "incident_id": {"anterior": None, "novo": incident.id},
                        "original_filename": {"anterior": None, "novo": attachment.original_filename},
                        "mime_type": {"anterior": None, "novo": attachment.mime_type},
                        "file_size": {"anterior": None, "novo": attachment.file_size},
                        "sha256": {"anterior": None, "novo": attachment.sha256},
                        "uploaded_by_id": {"anterior": None, "novo": current_user.id},
                    },
                    commit=False,
                    raise_on_error=True,
                )
            db.session.commit()
        except AttachmentValidationError as exc:
            form_incident = _incident_edit_draft(incident, description=description, description_plain_text=description_plain_text)
            db.session.rollback()
            flash(str(exc), 'danger')
            return _render_incident_form_response(
                title="Editar Incidente",
                unidades=unidades,
                status_incident_list=status_incident_list,
                incidents_types=_get_incident_types_for_form(incident_type),
                data_atual=data_atual,
                incident=form_incident,
                edit_mode=True,
                status_code=400,
            )
        except Exception as exc:
            form_incident = _incident_edit_draft(incident, description=description, description_plain_text=description_plain_text)
            db.session.rollback()
            for attachment in saved_attachments:
                delete_attachment_file(attachment)
            current_app.logger.exception("Falha ao editar incidente: %s", exc)
            flash('Não foi possível editar o incidente.', 'danger')
            return _render_incident_form_response(
                title="Editar Incidente",
                unidades=unidades,
                status_incident_list=status_incident_list,
                incidents_types=_get_incident_types_for_form(incident_type),
                data_atual=data_atual,
                incident=form_incident,
                edit_mode=True,
                status_code=500,
            )

        flash('Incidente editado com sucesso!', 'success')
        return redirect(url_for('incidente.incident_view', incident_id=incident_id))

    return _render_incident_form_response(
        title="Editar Incidente",
        incident=incident,
        edit_mode=True,
        unidades=unidades,
        status_incident_list=status_incident_list,
        incidents_types=incidents_types,
        data_atual=data_atual,
    )

#================================EXCLUIR INCIDENTE=================================
@incidente_bp.route("/incidente/delete/<int:incident_id>", methods=['POST'])
@login_required
def delete_incident(incident_id):
    if not allowed_edit_profile(current_user):
        abort(403)

    incident = Incidente.query.get_or_404(incident_id)
    _ensure_incident_owner_or_admin(incident, "excluir")
    report_number = incident.report_number

    try:
        for attachment in list(incident.attachments or []):
            delete_attachment_file(attachment)
        db.session.delete(incident)
        registrar_auditoria(
            acao=AuditAction.EXCLUIR,
            modulo="Incidentes de seguran?a",
            entidade="Incidente",
            entidade_id=incident_id,
            descricao=f"Incidente exclu?do: {report_number}",
            commit=False,
            raise_on_error=True,
        )
        db.session.commit()
    except Exception:
        db.session.rollback()
        current_app.logger.exception("Falha ao excluir incidente %s", incident_id)
        flash("N?o foi poss?vel excluir o incidente.", "danger")
        return redirect(url_for("incidente.incident_view", incident_id=incident_id))

    flash("Incidente exclu?do com sucesso!", "success")
    return redirect(url_for("incidente.incidents_list"))




#=================================PESQUISAR INCIDENTE=================================
@incidente_bp.route("/incidente/pesquisar", methods=['GET'])
@login_required
def search_incident():
    # Rota para pesquisar incidentes

    termo = request.args.get('termo', '') # Pega o termo de pesquisa do formulário

    # Se não houver termo de pesquisa, redireciona para a lista de incidentes
    if not termo:
        return redirect(url_for('incidente.incidents_list'))

    query = Incidente.query
    search_terms = f"%{termo}%"

    filters = [
        Incidente.incident_type.ilike(search_terms),
        Incidente.report_number.ilike(search_terms),
        Incidente.ticket_number.ilike(search_terms),
        Incidente.btl.ilike(search_terms),
        Incidente.cpa.ilike(search_terms),
        Incidente.cia.ilike(search_terms),
        Incidente.description.ilike(search_terms),
    ]

    resultados = query.filter(or_(*filters)).all()

    status_options = db.session.query(Incidente.status_incident).distinct().all()
    return render_template(
        'incidente/incidentes.html',
        title=f"Incidentes de segurança - pesquisa: {termo}",
        incidentes=resultados,
        pagination=None,
        total_incidents=len(resultados),
        open_incidents=len([inc for inc in resultados if inc.status_incident != 'Encerrado']),
        closed_incidents=len([inc for inc in resultados if inc.status_incident == 'Encerrado']),
        status_options=status_options,
        direction_filter='desc',
        sort_by='start_date',
        status_filter='todos',
        termo=termo
    )


################################################################################
#===============================OBSERVA??ES DO INCIDENTE========================
################################################################################


#=================================ADD OBSERVA??O=================================
@incidente_bp.route("/incidente/<int:incident_id>/add_obs", methods=['POST'])
@login_required
def add_obs(incident_id):
    if allowed_edit_profile(current_user):
        # Rota para adicionar observação ao incidente
        texto_observacao = request.form['texto_observacao']
        user_id = current_user.id # Usuário logado
        data_observacao = datetime.now(timezone.utc) # Data e hora real gerada pelo backend

        # Adicionando e comitando no banco de dados
        new_obs = IncidenteObs(incidente_id=incident_id, usuario_id=user_id, texto_observacao=texto_observacao, data_observacao=data_observacao)
        db.session.add(new_obs)
        db.session.flush()
        registrar_auditoria(
            acao=AuditAction.ADICIONAR_OBSERVACAO,
            modulo="Incidentes de segurança",
            entidade="IncidenteObs",
            entidade_id=new_obs.id,
            descricao=f"Observação adicionada ao incidente {incident_id}.",
            alteracoes={
                "texto_observacao": {"anterior": None, "novo": texto_observacao},
                "incidente_id": {"anterior": None, "novo": incident_id},
                "usuario_id": {"anterior": None, "novo": user_id},
            },
            commit=False,
            raise_on_error=True,
        )
        db.session.commit()
        flash('Observação adicionada com sucesso!', 'success')
        return redirect(url_for('incidente.incident_view', incident_id=incident_id))
    else:
        flash('Acesso negado: Você não tem permissão para inserir uma observação.', 'danger')
        return redirect(url_for('incidente.incident_view', incident_id=incident_id))

#=================================EXCLUIR OBSERVA??O=================================
@incidente_bp.route("/incidente/<int:incident_id>/delete_obs/<int:obs_id>", methods=['POST'])
@login_required
def delete_obs(incident_id, obs_id):
    # Rota para excluir observação
    obs = IncidenteObs.query.get_or_404(obs_id)
    if obs.autor_obs != current_user and current_user.profile != 'Admin':
        flash('Acesso negado: Você não tem permissão para excluir esta observação.', 'danger')
        return redirect(url_for('incidente.incident_view', incident_id=incident_id))

    obs_id_value = obs.id
    db.session.delete(obs)
    registrar_auditoria(
        acao=AuditAction.EXCLUIR_OBSERVACAO,
        modulo="Incidentes de segurança",
        entidade="IncidenteObs",
        entidade_id=obs_id_value,
        descricao=f"Observação excluída do incidente {incident_id}.",
        commit=False,
        raise_on_error=True,
    )
    db.session.commit()
    flash('Observação excluida com sucesso!', 'success')
    return redirect(url_for('incidente.incident_view', incident_id=incident_id))


#=================================VIEW DO INCIDENTE=================================
@incidente_bp.route("/incidente/<int:incident_id>", methods=['GET'])
@login_required
def incident_view(incident_id):
    # Rota para visualizar detalhes de um incidente específico
    incidente = Incidente.query.get_or_404(incident_id)
    return_to = request.args.get("return_to", "")
    return_url = return_to if _is_safe_internal_path(return_to) else url_for('incidente.incidents_list')
    return render_template('incidente/incidente_view.html', title="Detalhes do Incidente", incidente=incidente, return_url=return_url, is_inline_attachment=_is_inline_attachment)


@incidente_bp.route("/incidentes/<int:incident_id>/anexos/<int:attachment_id>", methods=['GET'])
@login_required
def open_attachment(incident_id, attachment_id):
    Incidente.query.get_or_404(incident_id)
    attachment = IncidentAttachment.query.filter_by(id=attachment_id, incident_id=incident_id).first_or_404()
    if not _is_inline_attachment(attachment):
        return redirect(url_for('incidente.download_attachment', incident_id=incident_id, attachment_id=attachment_id))
    path = resolve_attachment_path(attachment)
    registrar_auditoria(
        acao=AuditAction.DOWNLOAD_ANEXO,
        modulo="Incidentes de segurança",
        entidade="IncidentAttachment",
        entidade_id=attachment.id,
        descricao=f"Anexo aberto no navegador: {attachment.original_filename}",
    )
    response = send_file(path, mimetype=attachment.mime_type, as_attachment=False, download_name=attachment.original_filename)
    response.headers["X-Content-Type-Options"] = "nosniff"
    return response


@incidente_bp.route("/incidentes/<int:incident_id>/anexos/<int:attachment_id>/download", methods=['GET'])
@login_required
def download_attachment(incident_id, attachment_id):
    Incidente.query.get_or_404(incident_id)
    attachment = IncidentAttachment.query.filter_by(id=attachment_id, incident_id=incident_id).first_or_404()
    path = resolve_attachment_path(attachment)
    registrar_auditoria(
        acao=AuditAction.DOWNLOAD_ANEXO,
        modulo="Incidentes de segurança",
        entidade="IncidentAttachment",
        entidade_id=attachment.id,
        descricao=f"Anexo baixado: {attachment.original_filename}",
    )
    response = send_file(path, mimetype=attachment.mime_type, as_attachment=True, download_name=attachment.original_filename)
    response.headers["X-Content-Type-Options"] = "nosniff"
    return response


@incidente_bp.route("/incidentes/<int:incident_id>/anexos/<int:attachment_id>/delete", methods=['POST'])
@login_required
def delete_attachment(incident_id, attachment_id):
    if not allowed_edit_profile(current_user):
        abort(403)
    Incidente.query.get_or_404(incident_id)
    attachment = IncidentAttachment.query.filter_by(id=attachment_id, incident_id=incident_id).first_or_404()
    original_filename = attachment.original_filename
    delete_attachment_file(attachment)
    db.session.delete(attachment)
    registrar_auditoria(
        acao=AuditAction.EXCLUIR_ANEXO,
        modulo="Incidentes de segurança",
        entidade="IncidentAttachment",
        entidade_id=attachment_id,
        descricao=f"Anexo excluído do incidente {incident_id}: {original_filename}",
        commit=False,
        raise_on_error=True,
    )
    db.session.commit()
    flash('Anexo excluído com sucesso.', 'success')
    return redirect(url_for('incidente.incident_view', incident_id=incident_id))



#####################################################################################################
#=================================DASHBOARD=================================
#####################################################################################################

# app/blueprints/incidente/routes.py

def _current_month_range():
    today = _today_local_date()
    start = today.replace(day=1)
    if start.month == 12:
        next_month = start.replace(year=start.year + 1, month=1)
    else:
        next_month = start.replace(month=start.month + 1)
    end = next_month - timedelta(days=1)
    return start, end, next_month


def _parse_dashboard_date(value, field_name):
    if not value:
        return None
    try:
        return datetime.strptime(value, "%Y-%m-%d").date()
    except ValueError:
        abort(400, description=f"{field_name} inválida.")


def _dashboard_filter_options():
    cpa_rows = db.session.query(Unidades.cpa).filter(Unidades.cpa.isnot(None)).distinct().order_by(Unidades.cpa.asc()).all()
    cpas = [row[0] for row in cpa_rows if row[0]]
    unidades = Unidades.query.order_by(Unidades.cpa.asc(), Unidades.btl.asc()).all()
    db_types = [row[0] for row in db.session.query(TipoIncidente.tipo_incidente).all() if row[0]]
    incident_types = [
        SimpleNamespace(tipo_incidente=value)
        for value in sorted(set(TIPOS_INCIDENTE_PERMITIDOS).union(db_types))
    ]
    return {
        "incident_types": incident_types,
        "statuses": StatusIncidente.query.order_by(StatusIncidente.status.asc()).all(),
        "cpas": cpas,
        "unidades": unidades,
    }


def _dashboard_filters_from_request():
    default_start, default_end, default_next_month = _current_month_range()
    view = request.args.get("view", "status").strip()
    if view not in {"status", "cpa-btl"}:
        abort(400, description="Visualização inválida.")

    start_date = _parse_dashboard_date(request.args.get("startDate") or request.args.get("start_date"), "Data inicial") or default_start
    end_date = _parse_dashboard_date(request.args.get("endDate") or request.args.get("end_date"), "Data final") or default_end
    if end_date < start_date:
        abort(400, description="Período inválido.")

    end_exclusive = end_date + timedelta(days=1)
    if (end_exclusive - start_date).days > 370:
        abort(400, description="Período máximo de consulta excedido.")

    incident_type = (request.args.get("incidentType") or request.args.get("incident_type") or "todos").strip()
    status_filter = (request.args.get("status") or request.args.get("statusId") or "todos").strip()
    cpa = (request.args.get("cpa") or request.args.get("cpaId") or "todos").strip()
    btl = (request.args.get("btl") or request.args.get("btlId") or "todos").strip()

    if incident_type and incident_type != "todos" and incident_type not in TIPOS_INCIDENTE_PERMITIDOS and not TipoIncidente.query.filter_by(tipo_incidente=incident_type).first():
        abort(400, description="Tipo de incidente inválido.")
    if status_filter and status_filter != "todos" and not StatusIncidente.query.filter_by(status=status_filter).first():
        abort(400, description="Status inválido.")
    if cpa and cpa != "todos" and not Unidades.query.filter_by(cpa=cpa).first():
        abort(400, description="CPA inválido.")
    if btl and btl != "todos":
        if not cpa or cpa == "todos":
            abort(400, description="Selecione um CPA antes de filtrar por BTL.")
        btl_query = Unidades.query.filter_by(btl=btl)
        if cpa and cpa != "todos":
            btl_query = btl_query.filter_by(cpa=cpa)
        if not btl_query.first():
            abort(400, description="BTL não pertence ao CPA informado.")

    return {
        "view": view,
        "startDate": start_date.isoformat(),
        "endDate": end_date.isoformat(),
        "start_dt": datetime.combine(start_date, datetime.min.time()),
        "end_exclusive_dt": datetime.combine(end_exclusive, datetime.min.time()),
        "incidentType": incident_type,
        "status": status_filter,
        "cpa": cpa,
        "btl": btl,
    }


def _filtered_incident_query(filters):
    query = Incidente.query.filter(
        Incidente.start_date >= filters["start_dt"],
        Incidente.start_date < filters["end_exclusive_dt"],
    )
    if filters["incidentType"] != "todos":
        query = query.filter(Incidente.incident_type.in_(_filter_values_with_legacy(filters["incidentType"])))
    if filters["status"] != "todos":
        query = query.filter(Incidente.status_incident.in_(_filter_values_with_legacy(filters["status"])))
    if filters["cpa"] != "todos":
        query = query.filter(Incidente.cpa == filters["cpa"])
    if filters["btl"] != "todos":
        query = query.filter(Incidente.btl == filters["btl"])
    return query


def _dashboard_cards(query):
    total = query.count()
    closed = query.filter(Incidente.status_incident == "Encerrado").count()
    in_progress = query.filter(Incidente.status_incident.in_(_filter_values_with_legacy("Em Análise") + _filter_values_with_legacy("Em Mitigação"))).count()
    open_count = query.filter(Incidente.status_incident != "Encerrado").count()
    return {"total": total, "open": open_count, "inProgress": in_progress, "closed": closed}


def _status_chart_data(query):
    rows = (
        query.with_entities(Incidente.status_incident, db.func.count(Incidente.id))
        .group_by(Incidente.status_incident)
        .order_by(Incidente.status_incident.asc())
        .all()
    )
    total = sum(row[1] for row in rows)
    items = []
    for status_name, count in rows:
        label = status_name or "Não informado"
        percentage = round((count / total) * 100, 2) if total else 0
        items.append({"statusId": label, "label": label, "total": count, "percentage": percentage})
    return items


def _cpa_btl_chart_data(query):
    rows = (
        query.with_entities(Incidente.cpa, Incidente.btl, db.func.count(Incidente.id))
        .group_by(Incidente.cpa, Incidente.btl)
        .order_by(Incidente.cpa.asc(), db.func.count(Incidente.id).desc(), Incidente.btl.asc())
        .all()
    )
    unidades = Unidades.query.all()
    cpa_ids = {}
    btl_ids = {}
    for unidade in unidades:
        if unidade.cpa and unidade.cpa not in cpa_ids:
            cpa_ids[unidade.cpa] = unidade.id
        if unidade.btl:
            btl_ids[(unidade.cpa, unidade.btl)] = unidade.id

    groups = {}
    for cpa_name, btl_name, count in rows:
        cpa_label = cpa_name or "CPA não informado"
        btl_label = btl_name or "BTL não informado"
        group = groups.setdefault(cpa_label, {
            "cpaId": cpa_ids.get(cpa_label, cpa_label),
            "cpaName": cpa_label,
            "total": 0,
            "battalions": [],
        })
        group["total"] += count
        group["battalions"].append({
            "btlId": btl_ids.get((cpa_name, btl_name), btl_label),
            "btlName": btl_label,
            "total": count,
        })
    result = sorted(groups.values(), key=lambda item: str(item["cpaName"]))
    for group in result:
        group["battalions"].sort(key=lambda item: (-item["total"], item["btlName"]))
    return result


def _status_pie_html(items):
    if not items:
        return ""
    fig = px.pie(
        items,
        values="total",
        names="label",
        hole=0,
        color_discrete_sequence=['#06386d', '#0f7a4f', '#a76500', '#b42318', '#7fb7df']
    )
    fig.update_traces(textposition='outside', texttemplate='%{label}<br>%{value} (%{percent})', hovertemplate='%{label}<br>%{value} incidentes<br>%{percent}<extra></extra>')
    fig.update_layout(autosize=True, paper_bgcolor='white', plot_bgcolor='white', margin=dict(l=24, r=24, t=24, b=24), legend_title_text="Status")
    return fig.to_html(full_html=False, config={'responsive': True, 'displayModeBar': False})


def _dashboard_payload():
    filters = _dashboard_filters_from_request()
    query = _filtered_incident_query(filters)
    cards = _dashboard_cards(query)
    if filters["view"] == "cpa-btl":
        chart = {"type": "cpa-btl", "groups": _cpa_btl_chart_data(query)}
    else:
        chart = {"type": "status", "items": _status_chart_data(query)}
    return filters, cards, chart


@incidente_bp.route("/dashboard-incidentes", methods=['GET'])
@login_required
def dashboard_incidentes():
    filters, cards, chart = _dashboard_payload()
    options = _dashboard_filter_options()
    status_pie_html = _status_pie_html(chart.get("items", [])) if chart["type"] == "status" else ""
    max_btl_total = 0
    if chart["type"] == "cpa-btl":
        max_btl_total = max([btl["total"] for group in chart["groups"] for btl in group["battalions"]] or [0])

    registrar_auditoria(
        acao="INCIDENT_DASHBOARD_VIEWED",
        modulo="Dashboard incidentes",
        entidade="Incidente",
        descricao="Dashboard consolidado de incidentes consultado.",
        alteracoes={
            "view": {"anterior": None, "novo": filters["view"]},
            "start_date": {"anterior": None, "novo": filters["startDate"]},
            "end_date": {"anterior": None, "novo": filters["endDate"]},
            "incident_type": {"anterior": None, "novo": filters["incidentType"]},
            "status_incident": {"anterior": None, "novo": filters["status"]},
            "cpa": {"anterior": None, "novo": filters["cpa"]},
            "btl": {"anterior": None, "novo": filters["btl"]},
        },
    )

    return render_template(
        "dashboard/incidentes.html",
        title="Dashboard de Incidentes",
        filters=filters,
        cards=cards,
        chart=chart,
        status_pie_html=status_pie_html,
        max_btl_total=max_btl_total,
        **options,
    )


@incidente_bp.route("/api/dashboard/incidents", methods=['GET'])
@login_required
def api_dashboard_incidents():
    filters, cards, chart = _dashboard_payload()
    return jsonify({
        "filters": {
            "view": filters["view"],
            "startDate": filters["startDate"],
            "endDate": filters["endDate"],
            "incidentType": filters["incidentType"],
            "status": filters["status"],
            "cpa": filters["cpa"],
            "btl": filters["btl"],
        },
        "cards": cards,
        "chart": chart,
    })

@incidente_bp.route("/dashboard/incidentes_cpa_btl", methods=['GET'])
@login_required
def dashboard_incidentes_cpa_btl():
    params = request.args.to_dict()
    params["view"] = "cpa-btl"
    return redirect(url_for("incidente.dashboard_incidentes", **params))

@incidente_bp.route("/dashboard/incidentes_status", methods=['GET'])
@login_required
def dashboard_incidentes_status():
    params = request.args.to_dict()
    params["view"] = "status"
    return redirect(url_for("incidente.dashboard_incidentes", **params))
