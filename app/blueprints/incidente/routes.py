# app/blueprints/analise/routes.py

from flask import render_template, url_for, flash, redirect, request, current_app
from app.blueprints.incidente import incidente_bp
from app.models import Incidente, User, IncidenteObs, Unidades, StatusIncidente, TipoIncidente
from app import db
from flask_login import login_required, current_user
from datetime import datetime, timedelta, timezone
from sqlalchemy import or_
from app.utils.data_processing import get_filtered_incidents_df
from app.blueprints.users.routes import allowed_edit_profile




# Função auxiliar para formatar timedelta em uma string legível ##  PASSAR ESSA FUNÇÃO PARA UM ARQUIVO UTILIDADES
def format_timedelta(td):
    """Formata um objeto timedelta para uma string legível (Dias, Horas, Minutos)."""
    if not td:
        return "N/A"
        
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
    
    #recebendo parametros de filtro da URL
    status_filter = request.args.get('status_filter')
    direction_filter = request.args.get('direction', 'desc') # Padrão decrescente pela data de criação
    sort_by = request.args.get('sort_by', 'start_date') # Padrão ordenação pela data de início
    
    query = Incidente.query
    
    if status_filter and status_filter != 'todos':
        query = query.filter(Incidente.status_incident == status_filter)
    
    if sort_by:
        if direction_filter == 'desc':
            query = query.order_by(db.desc(getattr(Incidente, sort_by)))
        else:
            query = query.order_by(db.asc(getattr(Incidente, sort_by)))
    
    
    # Rota para listar todas as análises
    incidentes = query.all()
    
    
    
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
                           title="Incidentes Registrados", 
                           incidentes = incidentes_com_tempo,
                           status_options=status_options,
                           direction_filter=direction_filter,
                           sort_by=sort_by,
                           status_filter=status_filter)


#=================================REGISTRAR NOVO INCIDENTE=================================
@incidente_bp.route("/incidente/new", methods=['GET', 'POST'])

@login_required
def new_incident():
    if allowed_edit_profile(current_user): # função para verificar permissão do usuário para edição
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


            # 3. Atribui os novos valores ao objeto incident (GARANTINDO A ATRIBUIÇÃO DE STRINGS)
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
            db.session.commit()
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
        db.session.delete(incident)
        db.session.commit()
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
    
    return render_template('incidente/incidentes.html', title=f"Resultados da pesquisa para: {termo}", incidentes=resultados)

    
################################################################################
#===============================OBSERVAÇÕES DO INCIDENTE========================
################################################################################


#=================================ADD OBSERVAÇÃO=================================
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
        flash('Observação adicionada com sucesso!', 'success')
        return redirect(url_for('incidente.incident_view', incident_id=incident_id))
    else:
        flash('Acesso negado: Você não tem permissão para inserir uma observação.', 'danger')
        return redirect(url_for('incidente.incident_view', incident_id=incident_id))

#=================================EXCLUIR OBSERVAÇÃO=================================
@incidente_bp.route("/incidente/<int:incident_id>/delete_obs/<int:obs_id>", methods=['POST'])
@login_required
def delete_obs(incident_id, obs_id):
    # Rota para excluir observação
    obs = IncidenteObs.query.get_or_404(obs_id)

    db.session.delete(obs)
    db.session.commit()
    flash('Observação excluida com sucesso!', 'success')
    return redirect(url_for('incidente.incident_view', incident_id=incident_id))
                    
                    
#=================================VIEW DO INCIDENTE=================================
@incidente_bp.route("/incidente/<int:incident_id>", methods=['GET'])
@login_required
def incident_view(incident_id):
    # Rota para visualizar detalhes de um incidente específico
    incidente = Incidente.query.get_or_404(incident_id)
    return render_template('incidente/incidente_view.html', title="Detalhes do Incidente", incidente=incidente)



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
        labels={'cpa': 'Grande Comando', 'total': 'Incidentes'}
    )
    fig_bar.update_layout(barmode='stack')
    bar_chart_html = fig_bar.to_html(full_html=False)
    
    return render_template(
        'dashboard/incidentes_cpa_btl.html',
        title="Dashboard de Incidentes",
        start_date= filters['start_date'],
        end_date= filters['end_date'],
        incidents_types=incidents_types,
        status=status,
        bar_chart_html=bar_chart_html,
        filtros_aplicados=filters
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
        title='Incidentes por Status'
    )
    fig_donut.update_traces(textposition='outside', textinfo='percent+label')
    donut_chart_html = fig_donut.to_html(full_html=False)
    
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
        
    
    

        
        