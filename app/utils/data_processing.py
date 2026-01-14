import pandas as pd
from datetime import datetime
from app.models import Incidente, TipoIncidente, StatusIncidente
from app import db

def get_filtered_incidents_df(start_date, end_date, incident_type, status_str):
    """
    Retorna um DataFrame do Pandas com os incidentes filtrados por data, tipo e status.
    """
    # Define as datas padrão
    if not start_date:
        start_date = '2024-06-03'
    if not end_date:
        end_date = datetime.now().strftime('%Y-%m-%dT%H:%M')
    
    # Consulta todos os incidentes e converte para DataFrame
    incidentes_query = Incidente.query.all()
    df = pd.DataFrame([incidente.__dict__ for incidente in incidentes_query])
    
    # Converte as colunas de data para o formato datetime
    df['start_date'] = pd.to_datetime(df['start_date'])
    df['end_date'] = pd.to_datetime(df['end_date'])
    
    # Lógica de filtragem
    filtro_periodo = (df['start_date'] >= pd.to_datetime(start_date)) & \
                     (df['start_date'] <= pd.to_datetime(end_date))
    
    filtro_tipo_incidente = df['incident_type'] == df['incident_type']
    if incident_type and incident_type != 'todos':
        filtro_tipo_incidente = df['incident_type'] == incident_type
        
    filtro_status = df['status_incident'] == df['status_incident']
    if status_str and status_str != 'todos':
        filtro_status = df['status_incident'] == status_str
        
    df_filtered = df[filtro_periodo & filtro_tipo_incidente & filtro_status]
    
    filters = ({'start_date': start_date,
                'end_date': end_date, 
                'incident_type': incident_type, 
                'status': status_str})
    
    return df_filtered, filters