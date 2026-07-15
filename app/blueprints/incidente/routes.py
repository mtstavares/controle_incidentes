# app/blueprints/analise/routes.py

from flask import abort, render_template, url_for, flash, redirect, request, current_app, send_file
from app.blueprints.incidente import incidente_bp
from app.models import Incidente, User, IncidenteObs, Unidades, StatusIncidente, TipoIncidente, IncidentAttachment
from app import db
from flask_login import login_required, current_user
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo
from sqlalchemy import String, cast, or_
from urllib.parse import urlsplit
from types import SimpleNamespace
from app.utils.data_processing import get_filtered_incidents_df
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
    "Requisi??es automatizadas",
    "Transfer?ncia de arquivo malicioso",
    "Bloqueio de acesso a VPN",
    "Phishing",
    "Comando e Controle",
    "Incidente envolvendo VPN corporativa",
    "Criptomining",
    "Malware",
    "Ativador KMS",
    "Tentativa de intrus?o",
    "Comprometimento de Credenciais",
    "Quebra de Confidencialidade",
    "Brute Force",
}
TIPOS_INCIDENTE_FORM = sorted(TIPOS_INCIDENTE_PERMITIDOS)
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


def _today_local_date():
    return datetime.now(SAO_PAULO_TZ).date()


def _parse_registration_date(value):
    try:
        parsed = datetime.strptime((value or "").strip(), "%Y-%m-%d")
    except ValueError as exc:
        raise ValueError("Formato de data inv?lido.") from exc
    return parsed.replace(hour=0, minute=0, second=0, microsecond=0)


def _get_incident_types_for_form(current_value=None):
    values = list(TIPOS_INCIDENTE_FORM)
    if current_value and current_value not in values:
        values.append(current_value)
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
    target.cia = request.form.get("cia", "").strip()
    target.description = description
    target.description_plain_text = description_plain_text
    return target


def _incident_edit_draft(original_incident, description=None, description_plain_text=""):
    draft = _incident_draft_from_form(description=description, description_plain_text=description_plain_text)
    draft.id = original_incident.id
    draft.attachments = list(original_incident.attachments or [])
    return draft


def _render_incident_form_response(*, title, unidades, status_incident_list, incidents_types, data_atual, incident=None, edit_mode=False, status_code=200):
    return render_template(
        "incidente/new_incident.html",
        title=title,
        incident=incident,
        edit_mode=edit_mode,
        unidades=unidades,
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




# Função auxiliar para formatar timedelta em uma string legível ##  PASSAR ESSA FUN??O PARA UM ARQUIVO UTILIDADES
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
                           title="Incidentes de seguran?a",
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
        status_incident_list = StatusIncidente.query.all()
        incidents_types = _get_incident_types_for_form()

        if request.method == 'POST':
            status_incident = request.form.get('status_incidente', '').strip()
            registration_date = (request.form.get('registration_date') or request.form.get('start_data_hora', '')[:10]).strip()
            incident_type = request.form.get('incident_type', '').strip()
            report_number = request.form.get('report_number', '').strip()
            message_number = request.form.get('message_number', '').strip()
            ticket_number = request.form.get('ticket_number', '').strip()
            btl = request.form.get('btl', '').strip()
            cpa = request.form.get('cpa', '').strip()
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
                    "cia": {"anterior": None, "novo": cia},
                    "description": {"anterior": None, "novo": "[descrição sanitizada]"},
                    "user_id": {"anterior": None, "novo": user_id},
                },
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
        # Rota para registro de novo incidente
        if request.method == 'POST':
            # recebendo dados do formulário
            status_incident = request.form['status_incidente'] #notnull
            start_date = request.form['start_data_hora'] #notnull
            incident_type = request.form['incident_type'] #notnull
            report_number = request.form['report_number'] #notnull
            ticket_number = request.form['ticket_number']
            btl = request.form['btl'] #notnull
            cpa = request.form['cpa'] #notnull
            cia = request.form['cia']
            description = request.form['description'] #notnull
            
            # Usuário logado
            user_id = current_user.id
            
            #print(f"Status Incidente: {status_incident}\nStart Date: {start_date}\nIncident Type: {incident_type}\nreport_number: {report_number}\nTicket Number: {ticket_number}\nBTL: {btl}\nCPA: {cpa}\nCIA: {cia}\nDescription: {description}\nUser ID: {user_id}")
            # Verifica os campos obrigatórios
            if not all([status_incident, start_date, incident_type, report_number,btl, cpa, description]):
                flash('Erro: Os campos obrigatórios devem ser preenchidos.', 'danger')
                return redirect(url_for('incidente.new_incident'))
            
            # Convertendo campos de data para datetime
            start_date = datetime.strptime(start_date, '%Y-%m-%dT%H:%M')
            # if end_date:
            #     end_date = datetime.strptime(end_date, '%Y-%m-%dT%H:%M')
            # else:
            #     end_date = None
            
            # Criando nova instância de Incidente    
            new_incident = Incidente(
                status_incident=status_incident,
                start_date=start_date,
                incident_type=incident_type,
                report_number=report_number,
                ticket_number=ticket_number,
                btl=btl,
                cpa=cpa,
                cia=cia,
                description=description,
                user_id=user_id,
                # end_date=end_date
            )
            
            # Adicionando e comitando no banco de dados
            db.session.add(new_incident)
            db.session.commit()
            registrar_auditoria(
                acao=AuditAction.CRIAR,
                modulo="Incidentes de segurança",
                entidade="Incidente",
                entidade_id=new_incident.id,
                descricao=f"Incidente criado: {new_incident.report_number}",
                alteracoes={
                    "status_incident": {"anterior": None, "novo": status_incident},
                    "start_date": {"anterior": None, "novo": start_date},
                    "incident_type": {"anterior": None, "novo": incident_type},
                    "report_number": {"anterior": None, "novo": report_number},
                    "ticket_number": {"anterior": None, "novo": ticket_number},
                    "btl": {"anterior": None, "novo": btl},
                    "cpa": {"anterior": None, "novo": cpa},
                    "cia": {"anterior": None, "novo": cia},
                    "description": {"anterior": None, "novo": description},
                    "user_id": {"anterior": None, "novo": user_id},
                },
            )
            flash('Incidente registrado com sucesso!', 'success')
            
            return redirect(url_for('incidente.incidents_list')) #alterar para lista de incidentes
            
        unidades = Unidades.query.all() # Carrega os dados da tabela unidades para o formulário
        incidents_types = TipoIncidente.query.all()# Carrega os dados da tabela TipoIncidente para o formulário
        status_incident_list = StatusIncidente.query.all() # Carrega os dados da tabela status para o formulário    
        return render_template('incidente/new_incident.html', title="Registro de Incidente", unidades= unidades , status_incident_list=status_incident_list, incidents_types=incidents_types)
    else:
        flash('Acesso negado: Você não tem permissão para registrar um novo incidente.', 'danger')
        return redirect(url_for('incidente.incidents_list'))


#=================================EDITAR INCIDENTE=================================
@incidente_bp.route("/incidente/<int:incident_id>/edit", methods=['GET', 'POST'])
@login_required
def edit_incident(incident_id): # Rota para editar um incidente
    if not allowed_edit_profile(current_user):
        current_app.logger.info(f"Usuario {current_user.id} tentou editar o incidente {incident_id}. Sem permiss?o. {current_user.profile}")
        flash('Acesso negado: Voc? n?o tem permiss?o para editar este incidente.', 'danger')
        return redirect(url_for('incidente.incident_view', incident_id=incident_id))

    incident = Incidente.query.get_or_404(incident_id)
    data_atual = _today_local_date()
    unidades = Unidades.query.all()
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
        btl = request.form.get('btl', '').strip()
        cpa = request.form.get('cpa', '').strip()
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
                    'cia': incident.cia,
                    'description': "[alterado]" if original_data['description'] != incident.description else original_data['description'],
                    'description_plain_text': "[alterado]" if original_data['description_plain_text'] != incident.description_plain_text else original_data['description_plain_text'],
                }
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

        registrar_auditoria(
            acao=AuditAction.EDITAR,
            modulo="Incidentes de seguran?a",
            entidade="Incidente",
            entidade_id=incident.id,
            descricao=f"Incidente editado: {incident.report_number}",
            alteracoes=audit_changes,
        )
        for attachment in saved_attachments:
            registrar_auditoria(
                acao=AuditAction.UPLOAD_ANEXO,
                modulo="Incidentes de seguran?a",
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
    
    
    #Função para tornar uma string snake_case (e.g., 'status_incident') em uma string amigável (e.g., 'Status Incident').
    def format_key_name(key_name):
        """
        Transforma uma string snake_case (e.g., 'status_incident') em
        uma string amigável (e.g., 'Status Incident').
        """
        if not isinstance(key_name, str):
            return str(key_name)
        
        # 1. Substitui '_' por espaço
        # 2. Converte para o formato Título (primeira letra de cada palavra em maiúsculo)
        return key_name.replace('_', ' ').title()
    if allowed_edit_profile(current_user): # função para verificar permissão do usuário para edição
        
        #carregando dados do incidente registrado pelo id
        incident = Incidente.query.get_or_404(incident_id)
        
        # Veririfica o metodo da requisição, se for POST, atualiza os dados
        if request.method == 'POST':
            
            # Formato de data e hora que o input type="datetime-local" e o strftime usam
            DATE_FORMAT = '%Y-%m-%dT%H:%M'

            # 1. Armazenando os dados originais (em strings para comparação consistente)
            original_data = {
                'status_incident': incident.status_incident,
                'start_date': incident.start_date.strftime(DATE_FORMAT) if incident.start_date else '', 
                'incident_type': incident.incident_type,
                'report_number': incident.report_number,
                'ticket_number': incident.ticket_number,
                'btl': incident.btl,
                'cpa': incident.cpa,
                'cia': incident.cia,
                'description': incident.description
            }
            
            # Mapeamento dos campos do formulário para os atributos do modelo (Incidente)
            form_to_model = {
                'status_incidente': 'status_incident',
                'start_data_hora': 'start_date',
                'incident_type': 'incident_type',
                'report_number': 'report_number',
                'ticket_number': 'ticket_number',
                'btl': 'btl',
                'cpa': 'cpa',
                'cia': 'cia',
                'description': 'description',
            }

            changes = []
            
            # NOVAS VARIÁVEIS para armazenar os novos valores antes da atribuição final
            new_values_map = {}

            # 2. Iterando sobre o formulário para verificar mudanças E preparar a atribuição
            for form_key, model_key in form_to_model.items():
                # Obtém o novo valor do formulário, tratando como string
                new_value = request.form.get(form_key, '').strip()
                new_values_map[model_key] = new_value # Armazena o novo valor (string)

                # Obtém o valor original (string normalizada)
                original_value = original_data.get(model_key, '')
                
                # Normaliza valores nulos/vazios para melhor comparação
                original_str = str(original_value or '')
                new_str = str(new_value or '')
                
                # Exceção para campos que podem ser None/vazio e não devem gerar log se forem de None/Vazio para Vazio
                if model_key in ['ticket_number', 'cia'] and original_str in ('None', '') and (new_str == '' or new_str == 'None' or new_str is None):
                    continue
                
                # Se houve mudança, registra no log
                if new_str != original_str:
                    friendly_name = format_key_name(model_key)
                    if new_str == 'Encerrado': #SE O STATUS FOR ALTERADO PARA ENCERRADO, ATRIBUI A DATA ATUAL PARA END_DATE
                        incident.end_date = datetime.now()
                    changes.append(f"{friendly_name} alterado de '{original_str}' para '{new_str}'")


            # 3. Atribui os novos valores ao objeto incident (GARANTINDO A ATRIBUI??O DE STRINGS)
            # Isso resolve o problema do 'datetime.datetime' persistente.
            incident.status_incident = new_values_map['status_incident']
            incident.start_date = new_values_map['start_date'] # AGORA É A STRING DO FORM
            
            incident.incident_type = new_values_map['incident_type']
            incident.report_number = new_values_map['report_number']
            incident.ticket_number = new_values_map['ticket_number']
            incident.btl = new_values_map['btl']
            incident.cpa = new_values_map['cpa']
            incident.cia = new_values_map['cia']
            incident.description = new_values_map['description']

            
            # 4. Verifica os campos obrigatórios (mantido, mas usando os novos valores)
            if not all([incident.status_incident, incident.start_date, incident.incident_type, incident.report_number, incident.btl, incident.cpa, incident.description]):
                flash('Erro: Os campos obrigatórios devem ser preenchidos.', 'danger')
                return redirect(url_for('incidente.edit_incident', incident_id=incident_id))
            
            # 5. Convertendo campos de data para datetime (AGORA É SEGURO)
            try:
                # incident.start_date é garantidamente a string do form neste ponto
                incident.start_date = datetime.strptime(incident.start_date, DATE_FORMAT)
                
            except ValueError:
                flash('Erro: Formato de data/hora inválido.', 'danger')
                return redirect(url_for('incidente.edit_incident', incident_id=incident_id))

            # if incident.end_date:
            #     incident.end_date = datetime.strptime(incident.end_date, DATE_FORMAT)
            # else:
            #     incident.end_date = None
            
            # 5. Gravando a observação de alterações
            if changes:
                txt_obs = "Alterações:\n" + "\n".join(changes)
                txt_obs += f"Usuário: {current_user.name}"
                # Note: 'usuario_id=1' é o 'Sistema' conforme seu código original
                new_obs = IncidenteObs(incidente_id=incident.id, usuario_id=1, texto_observacao=txt_obs, data_observacao=datetime.now()) 
                db.session.add(new_obs)
            
            # 6. Adicionando e comitando no banco de dados (o objeto incident já foi modificado)
            audit_changes = montar_alteracoes(
                "Incidente",
                original_data,
                {
                    'status_incident': incident.status_incident,
                    'start_date': incident.start_date.strftime(DATE_FORMAT) if incident.start_date else '',
                    'incident_type': incident.incident_type,
                    'report_number': incident.report_number,
                    'ticket_number': incident.ticket_number,
                    'btl': incident.btl,
                    'cpa': incident.cpa,
                    'cia': incident.cia,
                    'description': incident.description,
                }
            )
            db.session.commit()
            registrar_auditoria(
                acao=AuditAction.EDITAR,
                modulo="Incidentes de segurança",
                entidade="Incidente",
                entidade_id=incident.id,
                descricao=f"Incidente editado: {incident.report_number}",
                alteracoes=audit_changes,
            )
            current_app.logger.info(f"Usuario {current_user.id} editou o incidente {incident_id}") # REGISTRANDO LOG
            flash('Incidente editado com sucesso!', 'success')
            return redirect(url_for('incidente.incident_view', incident_id=incident_id))
        
        edit_mode = True  # Indicador de modo de edição para o template
        unidades = Unidades.query.all() # Carrega os dados da tabela unidades para o formulário
        incidents_types = TipoIncidente.query.all()# Carrega os dados da tabela TipoIncidente para o formulário
        status_incident_list = StatusIncidente.query.all() # Carrega os dados da tabela status para o formulário
        # Se for GET, renderiza o formulário com os dados atuais
        return render_template('incidente/new_incident.html', title="Editar Incidente", incident = incident, edit_mode=edit_mode, unidades=unidades, status_incident_list=status_incident_list, incidents_types=incidents_types)
    else:
        current_app.logger.info(f"Usuario {current_user.id} tentou editar o incidente {incident_id}. Sem permissão. {current_user.profile}")
        flash('Acesso negado: Você não tem permissão para editar este incidente.', 'danger')
        return redirect(url_for('incidente.incident_view', incident_id=incident_id))
#================================EXCLUIR INCIDENTE=================================
@incidente_bp.route("/incidente/delete/<int:incident_id>", methods=['POST'])
@login_required 
def delete_incident(incident_id):
    if allowed_edit_profile(current_user):
        # Rota para excluir um incidente
        incident = Incidente.query.get_or_404(incident_id)
        report_number = incident.report_number
        for attachment in list(incident.attachments or []):
            delete_attachment_file(attachment)
        db.session.delete(incident)
        db.session.commit()
        registrar_auditoria(
            acao=AuditAction.EXCLUIR,
            modulo="Incidentes de seguran?a",
            entidade="Incidente",
            entidade_id=incident_id,
            descricao=f"Incidente exclu?do: {report_number}",
        )
        flash('Incidente exclu?do com sucesso!', 'success')
        return redirect(url_for('incidente.incidents_list'))
        data_atual = _today_local_date()
        unidades = Unidades.query.all()
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
            btl = request.form.get('btl', '').strip()
            cpa = request.form.get('cpa', '').strip()
            cia = request.form.get('cia', '').strip()
            raw_description = request.form.get('description', '')

            if incident_type not in TIPOS_INCIDENTE_PERMITIDOS and incident_type != original_data['incident_type']:
                flash('Tipo de incidente informado ? inv?lido.', 'danger')
                return redirect(url_for('incidente.edit_incident', incident_id=incident_id))

            try:
                start_date = _parse_registration_date(registration_date)
                description, description_plain_text = sanitize_incident_description(raw_description)
            except (ValueError, SanitizationError) as exc:
                flash(str(exc), 'danger')
                return redirect(url_for('incidente.edit_incident', incident_id=incident_id))

            if not all([status_incident, registration_date, incident_type, report_number, btl, cpa, description_plain_text]):
                flash('Erro: Os campos obrigat?rios devem ser preenchidos.', 'danger')
                return redirect(url_for('incidente.edit_incident', incident_id=incident_id))

            incident.status_incident = status_incident
            incident.start_date = start_date
            incident.incident_type = incident_type
            incident.report_number = report_number
            incident.message_number = message_number or None
            incident.ticket_number = ticket_number or None
            incident.btl = btl
            incident.cpa = cpa
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
                        'cia': incident.cia,
                        'description': "[alterado]" if original_data['description'] != incident.description else original_data['description'],
                        'description_plain_text': "[alterado]" if original_data['description_plain_text'] != incident.description_plain_text else original_data['description_plain_text'],
                    }
                )
                db.session.commit()
            except AttachmentValidationError as exc:
                db.session.rollback()
                flash(str(exc), 'danger')
                return redirect(url_for('incidente.edit_incident', incident_id=incident_id))
            except Exception as exc:
                db.session.rollback()
                for attachment in saved_attachments:
                    delete_attachment_file(attachment)
                current_app.logger.exception("Falha ao editar incidente: %s", exc)
                flash('N?o foi poss?vel editar o incidente.', 'danger')
                return redirect(url_for('incidente.edit_incident', incident_id=incident_id))

            registrar_auditoria(
                acao=AuditAction.EDITAR,
                modulo="Incidentes de seguran?a",
                entidade="Incidente",
                entidade_id=incident.id,
                descricao=f"Incidente editado: {incident.report_number}",
                alteracoes=audit_changes,
            )
            for attachment in saved_attachments:
                registrar_auditoria(
                    acao=AuditAction.UPLOAD_ANEXO,
                    modulo="Incidentes de seguran?a",
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
                )
            flash('Incidente editado com sucesso!', 'success')
            return redirect(url_for('incidente.incident_view', incident_id=incident_id))

        return render_template(
            'incidente/new_incident.html',
            title="Editar Incidente",
            incident=incident,
            edit_mode=True,
            unidades=unidades,
            status_incident_list=status_incident_list,
            incidents_types=incidents_types,
            data_atual=data_atual,
        )
        report_number = incident.report_number
        db.session.delete(incident)
        db.session.commit()
        registrar_auditoria(
            acao=AuditAction.EXCLUIR,
            modulo="Incidentes de segurança",
            entidade="Incidente",
            entidade_id=incident_id,
            descricao=f"Incidente excluído: {report_number}",
        )
        flash('Incidente excluído com sucesso!', 'success')
        return redirect(url_for('incidente.incidents_list'))   
    else:
        flash('Acesso negado: Você não tem permissão para excluir este incidente.', 'danger')
        return redirect(url_for('incidente.incident_view', incident_id=incident_id))




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
#===============================OBSERVAÇÕES DO INCIDENTE========================
################################################################################


#=================================ADD OBSERVA??O=================================
@incidente_bp.route("/incidente/<int:incident_id>/add_obs", methods=['POST'])
@login_required
def add_obs(incident_id):
    if allowed_edit_profile(current_user):
        # Rota para adicionar observação ao incidente
        texto_observacao = request.form['texto_observacao']
        user_id = current_user.id # Usuário logado
        data_observacao = datetime.now() # Data e hora atual
        
        # Adicionando e comitando no banco de dados
        new_obs = IncidenteObs(incidente_id=incident_id, usuario_id=user_id, texto_observacao=texto_observacao, data_observacao=data_observacao)
        db.session.add(new_obs)
        db.session.commit()
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
        )
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
    db.session.commit()
    registrar_auditoria(
        acao=AuditAction.EXCLUIR_OBSERVACAO,
        modulo="Incidentes de segurança",
        entidade="IncidenteObs",
        entidade_id=obs_id_value,
        descricao=f"Observação excluída do incidente {incident_id}.",
    )
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
    db.session.commit()
    registrar_auditoria(
        acao=AuditAction.EXCLUIR_ANEXO,
        modulo="Incidentes de segurança",
        entidade="IncidentAttachment",
        entidade_id=attachment_id,
        descricao=f"Anexo excluído do incidente {incident_id}: {original_filename}",
    )
    flash('Anexo excluído com sucesso.', 'success')
    return redirect(url_for('incidente.incident_view', incident_id=incident_id))



#####################################################################################################
#=================================DASHBOARD=================================
#####################################################################################################

# app/blueprints/incidente/routes.py

import pandas as pd
import plotly.express as px
from sqlalchemy.sql import func

@incidente_bp.route("/dashboard/incidentes_cpa_btl", methods=['GET'])
@login_required
def dashboard_incidentes_cpa_btl():
    # # Rota para visualizar o dashboard de incidentes
    
   
    incidents_types = TipoIncidente.query.all() # Carrega os dados da tabela TipoIncidente para o formulário
    status = StatusIncidente.query.all() # Carrega os dados da tabela status para o formulário
    
    # Obtém os parâmetros de filtro da URL
    start_date = request.args.get('start_date')
    end_date = request.args.get('end_date')
    incident_type = request.args.get('incident_type')
    status_str = request.args.get('status')

    # Chama função que retorna o dataframe filtrado e os filtros aplicados
    df_filtred_incidentes_opm, filters = get_filtered_incidents_df(start_date, end_date, incident_type, status_str)
    
    ##################################################################
    # Gráfico de barras empilhadas com Plotly ======================
    
    df_bar = df_filtred_incidentes_opm # Passando o DataFrame filtrado para o gráfico de barras
    bar_counts = df_bar.groupby(['cpa', 'btl']).size().reset_index(name='total')
    
    fig_bar = px.bar(
        bar_counts,
        x='cpa',
        y='total',
        color='btl',
        title='',
        labels={'cpa': 'Grande Comando', 'total': 'Incidentes'},
        color_discrete_sequence=['#06386d', '#0f5f9f', '#7fb7df', '#122033', '#0f7a4f']
    )
    fig_bar.update_layout(barmode='stack', autosize=True, paper_bgcolor='white', plot_bgcolor='white', margin=dict(l=24, r=24, t=24, b=24))
    bar_chart_html = fig_bar.to_html(full_html=False, config={'responsive': True, 'displayModeBar': False})
    
    return render_template(
        'dashboard/incidentes_cpa_btl.html',
        title="Dashboard incidentes",
        start_date= filters['start_date'],
        end_date= filters['end_date'],
        incidents_types=incidents_types,
        status=status,
        bar_chart_html=bar_chart_html,
        filtros_aplicados=filters,
        total_incidents=len(df_filtred_incidentes_opm)
        )
        
@incidente_bp.route("/dashboard/incidentes_status", methods=['GET'])
@login_required
def dashboard_incidentes_status():
    # Rota para visualizar o dashboard de Status de incidentes
    
    incidents_types = TipoIncidente.query.all() # Carrega os dados da tabela TipoIncidente para o formulário
    status = StatusIncidente.query.all() # Carrega os dados da tabela status para o formulário
    
     # Obtém os parâmetros de filtro da URL
    start_date = request.args.get('start_date')
    end_date = request.args.get('end_date')
    incident_type = request.args.get('incident_type')
    status_str = request.args.get('status')
    
    df_filtred, filtros_aplicados = get_filtered_incidents_df(start_date, end_date, incident_type, status_str)
    
    print(df_filtred)
    ###########################################
    #GRAFICO ROSCA
      
    df_donut = df_filtred
        
    status_counts = df_donut.groupby('status_incident').size().reset_index(name='total')
    
    # Cria o gráfico de rosca com Plotly
    fig_donut = px.pie(
        status_counts,
        values='total',
        names='status_incident',
        hole=0.6,
        title='Incidentes por Status',
        color_discrete_sequence=['#06386d', '#0f7a4f', '#a76500', '#b42318', '#7fb7df']
    )
    fig_donut.update_traces(textposition='outside', textinfo='percent+label')
    fig_donut.update_layout(autosize=True, paper_bgcolor='white', plot_bgcolor='white', margin=dict(l=24, r=24, t=48, b=24))
    donut_chart_html = fig_donut.to_html(full_html=False, config={'responsive': True, 'displayModeBar': False})
    
    print(len(df_filtred))
    print(datetime.now())
    
    total_incidents = len(df_filtred)
    total_incidentes_encerrados = len(df_filtred[df_filtred['status_incident'] == 'Encerrado'])
    total_incidentes_em_analise = len(df_filtred[df_filtred['status_incident'] == 'Em Análise'])
    total_incidentes_em_mitigacao = len(df_filtred[df_filtred['status_incident'] == 'Em Mitigação'])
    total_incidentes_falso_positivo = len(df_filtred[df_filtred['status_incident'] == 'Falso positivo'])
    print(f"Total de Incidentes: {total_incidents}")
    print(f"Total de Incidentes Encerrados: {total_incidentes_encerrados}")
    print(f"Total de Incidentes Em Análise: {total_incidentes_em_analise}")
    print(f"Total de Incidentes Aguardando Informação/Ação Externa: {total_incidentes_em_mitigacao}")
    print(f"Total de Incidentes Falso positivo: {total_incidentes_falso_positivo}")
    totais = ({"Total" : total_incidents,
               "Resolvido": total_incidentes_encerrados, 
               "Em Análise": total_incidentes_em_analise, 
               "Aguardando": total_incidentes_em_mitigacao, 
               "Falso Positivo": total_incidentes_falso_positivo})
    
    return render_template('dashboard/incidentes_status.html', 
                           title="Dashboard de Incidentes",
                           donut_chart_html=donut_chart_html,
                           filtros_aplicados=filtros_aplicados,
                           incidents_types=incidents_types,
                           status=status,
                           totais=totais)
        
    
    

        
        
