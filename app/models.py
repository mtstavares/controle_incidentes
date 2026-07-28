from datetime import datetime, timezone

from flask_login import UserMixin

from app import db


def utc_now():
    return datetime.now(timezone.utc)


class TimestampMixin:
    created_at = db.Column(db.DateTime(timezone=True), nullable=False, default=utc_now)
    updated_at = db.Column(db.DateTime(timezone=True), nullable=False, default=utc_now, onupdate=utc_now)
    deleted_at = db.Column(db.DateTime(timezone=True), nullable=True)


class User(UserMixin, TimestampMixin, db.Model):
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(50), unique=True, nullable=False, index=True)
    name = db.Column(db.String(150), nullable=False)
    email = db.Column(db.String(255), unique=True, nullable=False, index=True)
    profile = db.Column(db.String(50), nullable=False)
    is_temp_password = db.Column(db.Boolean, default=True, nullable=False)
    must_change_password = db.Column(db.Boolean, default=True, nullable=False)
    is_active = db.Column(db.Boolean, default=True, nullable=False, index=True)
    deleted_by_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=True, index=True)
    password = db.Column(db.String(256), nullable=False)

    incidente = db.relationship('Incidente', backref='autor', lazy=True)
    observacoes = db.relationship('IncidenteObs', backref='autor_obs', lazy=True)
    audit_logs = db.relationship('AuditLog', backref='usuario', lazy=True)
    deleted_by = db.relationship('User', remote_side=[id], foreign_keys=[deleted_by_id], post_update=True)

    def __repr__(self):
        return f'<User {self.username}>'
    
class Incidente(TimestampMixin, db.Model):
    id = db.Column(db.Integer, primary_key=True) # ID do incidente
    message_number = db.Column(db.String(100), nullable=True, index=True)
    incident_type = db.Column(db.String(100), nullable=False) # Tipo de incidente >>> posteriormente criar uma tabela de tipos de incidentes
    report_number = db.Column(db.String(50), nullable=False) # Número do relatório semanal ou relatorio técnico em que a análise foi feita
    ticket_number = db.Column(db.String(50), nullable= True) # Número da mensagem enviada ou chamado aberto
    cpa = db.Column(db.String(100), nullable=False) # grande comando ou diretoria
    btl = db.Column(db.String(100), nullable=False) # Batalhão ou unidade envolvida no incidente
    cia = db.Column(db.String(100), nullable=True) # Companhia envolvida no incidente
    description = db.Column(db.Text, nullable=False) # Descrição do incidente. Como? Quando? Onde? Quem? Por quê? Ações tomadas?
    start_date = db.Column(db.DateTime, nullable=False) # Data de abertura da análise/incidente
    end_date = db.Column(db.DateTime, nullable=True) # Data de encerramento da análise/incidente
    status_incident = db.Column(db.String(50), default='Em andamento', nullable=False) # Status da análise
    command_id = db.Column(db.Integer, db.ForeignKey('organizational_commands.id'), nullable=True, index=True)
    unit_id = db.Column(db.Integer, db.ForeignKey('organizational_units.id'), nullable=True, index=True)
    
    # Chave estrangeira para o usuário que realizou a análise
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    description_plain_text = db.Column(db.Text, nullable=True)
    
    # Relacionamento: uma análise pode ter várias observações
    # 'lazy=True' significa que as observações serão carregadas sob demanda
    obs_incidente = db.relationship('IncidenteObs', backref='incidente', lazy=True, cascade="all, delete-orphan")
    attachments = db.relationship('IncidentAttachment', backref='incidente', lazy=True, cascade="all, delete-orphan")
    command = db.relationship('OrganizationalCommand', backref='incidentes')
    unit = db.relationship('OrganizationalUnit', backref='incidentes')

    def __repr__(self):
        return f'<Incidente {self.incident_type} - {self.report_number}>'
    
    
class IncidenteObs(TimestampMixin, db.Model):
    
    # Modelo para a tabela de observações de análise
    id = db.Column(db.Integer, primary_key=True)
    texto_observacao = db.Column(db.Text, nullable=False)
    data_observacao = db.Column(db.DateTime, nullable=False, default=utc_now)
    
    # Chave estrangeira para o usuário que inseriu a observação
    usuario_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    
    # Chave estrangeira para a análise à qual a observação pertence
    incidente_id = db.Column(db.Integer, db.ForeignKey('incidente.id'), nullable=False)

    def __repr__(self):
        return f'<Observação {self.id}>'
        
class IncidentAttachment(TimestampMixin, db.Model):
    __tablename__ = "incident_attachments"

    id = db.Column(db.Integer, primary_key=True)
    incident_id = db.Column(db.Integer, db.ForeignKey("incidente.id"), nullable=False, index=True)
    original_filename = db.Column(db.String(255), nullable=False)
    stored_filename = db.Column(db.String(255), nullable=False, unique=True)
    mime_type = db.Column(db.String(150), nullable=False)
    file_size = db.Column(db.BigInteger, nullable=False)
    sha256 = db.Column(db.String(64), nullable=False, index=True)
    uploaded_by_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False, index=True)
    uploaded_at = db.Column(db.DateTime(timezone=True), nullable=False)
    uploaded_by = db.relationship("User", backref="incident_attachments")

    def __repr__(self):
        return f'<IncidentAttachment {self.original_filename}>'


class Unidades(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    cpa = db.Column(db.String(100), nullable=False)
    btl = db.Column(db.String(100), nullable=False)
    

    def __repr__(self):
        return f'<Unidade {self.cpa} - {self.btl}>'


class OrganizationalCommand(TimestampMixin, db.Model):
    __tablename__ = "organizational_commands"

    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(100), nullable=False, unique=True, index=True)
    active = db.Column(db.Boolean, nullable=False, default=True, index=True)
    sort_order = db.Column(db.Integer, nullable=True, index=True)

    units = db.relationship(
        'OrganizationalUnit',
        back_populates='command',
        lazy=True,
        cascade="all, delete-orphan",
    )

    def __repr__(self):
        return f'<OrganizationalCommand {self.name}>'


class OrganizationalUnit(TimestampMixin, db.Model):
    __tablename__ = "organizational_units"
    __table_args__ = (
        db.UniqueConstraint("command_id", "normalized_name", name="uq_organizational_units_command_name"),
    )

    id = db.Column(db.Integer, primary_key=True)
    command_id = db.Column(db.Integer, db.ForeignKey("organizational_commands.id"), nullable=False, index=True)
    name = db.Column(db.String(100), nullable=False)
    normalized_name = db.Column(db.String(100), nullable=False)
    active = db.Column(db.Boolean, nullable=False, default=True, index=True)
    sort_order = db.Column(db.Integer, nullable=True, index=True)

    command = db.relationship('OrganizationalCommand', back_populates='units')

    def __repr__(self):
        return f'<OrganizationalUnit {self.command_id} - {self.name}>'


class TipoIncidente(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    tipo_incidente = db.Column(db.String(100), nullable=False)
    desc_incidente = db.Column(db.Text, nullable=True)
    

    def __repr__(self):
        return f'<TipoIncidente {self.tipo_incidente} - {self.desc_incidente}>'

class StatusIncidente(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    status = db.Column(db.String(50), nullable=False)
    desc_status = db.Column(db.Text, nullable=True)
    

    def __repr__(self):
        return f'<StatusIncidente {self.status} - {self.desc_status}>'
    

class CredencialComprometida(TimestampMixin, db.Model):
    __tablename__ = "credenciais_comprometidas"
    __table_args__ = (
        db.UniqueConstraint(
            "cpf",
            "credencial_fingerprint",
            "data_coleta",
            "lote_id",
            name="uq_credenciais_comprometidas_dedup",
        ),
    )

    id = db.Column(db.Integer, primary_key=True)
    nome = db.Column(db.String(255), nullable=False)
    nome_busca = db.Column(db.String(255), nullable=False, index=True)
    cpf = db.Column(db.String(11), nullable=False, index=True)
    email = db.Column(db.String(255), nullable=False, index=True)
    url_origem = db.Column(db.Text, nullable=True)
    data_coleta = db.Column(db.DateTime(timezone=True), nullable=True, index=True)
    permitiu_acesso = db.Column(db.Boolean, nullable=False, default=False, index=True)
    acesso_ad = db.Column(db.Boolean, nullable=False, default=False, index=True)
    acesso_ms = db.Column(db.Boolean, nullable=False, default=False, index=True)
    situacao_legal = db.Column(db.String(150), nullable=True)
    situacao_legal_normalizada = db.Column(db.String(150), nullable=True, index=True)
    observacoes = db.Column(db.Text, nullable=True)
    mensagem_bloqueio = db.Column(db.Text, nullable=True)
    rds = db.Column(db.String(255), nullable=True)
    credencial_fingerprint = db.Column(db.String(64), nullable=True, index=True)
    lote_id = db.Column(db.Integer, db.ForeignKey("credenciais_import_lotes.id"), nullable=True, index=True)
    imported_at = db.Column(db.DateTime(timezone=True), nullable=False, default=utc_now, index=True)
    imported_by_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=True, index=True)
    imported_by = db.relationship("User", backref="credenciais_importadas")
    lote = db.relationship("CredencialImportLote", back_populates="credenciais")

    def __repr__(self):
        return f"<CredencialComprometida {self.id} - {self.cpf}>"


class CredencialImportLote(TimestampMixin, db.Model):
    __tablename__ = "credenciais_import_lotes"
    __table_args__ = (
        db.UniqueConstraint("arquivo_sha256", name="uq_credenciais_import_lotes_arquivo_sha256"),
        db.UniqueConstraint(
            "ano_referencia",
            "mes_referencia",
            "versao",
            name="uq_credenciais_import_lotes_competencia_versao",
        ),
    )

    id = db.Column(db.Integer, primary_key=True)
    arquivo_nome_original = db.Column(db.String(255), nullable=False)
    arquivo_sha256 = db.Column(db.String(64), nullable=False, index=True)
    ano_referencia = db.Column(db.Integer, nullable=False, index=True)
    mes_referencia = db.Column(db.Integer, nullable=False, index=True)
    imported_at = db.Column(db.DateTime(timezone=True), nullable=False, default=utc_now, index=True)
    imported_by_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=True, index=True)
    total_testado = db.Column(db.Integer, nullable=False, default=0)
    total_validado = db.Column(db.Integer, nullable=False, default=0)
    total_somente_ad = db.Column(db.Integer, nullable=False, default=0)
    total_somente_ms = db.Column(db.Integer, nullable=False, default=0)
    total_ad_ms = db.Column(db.Integer, nullable=False, default=0)
    total_nao_validado = db.Column(db.Integer, nullable=False, default=0)
    rejeitados = db.Column(db.Integer, nullable=False, default=0)
    status = db.Column(db.String(30), nullable=False, default="ativo", index=True)
    versao = db.Column(db.Integer, nullable=False, default=1)
    lote_substituido_id = db.Column(db.Integer, db.ForeignKey("credenciais_import_lotes.id"), nullable=True)

    imported_by = db.relationship("User", foreign_keys=[imported_by_id], backref="credenciais_lotes_importados")
    lote_substituido = db.relationship("CredencialImportLote", remote_side=[id])
    credenciais = db.relationship("CredencialComprometida", back_populates="lote", lazy=True)

    def __repr__(self):
        return (
            f"<CredencialImportLote {self.ano_referencia:04d}-"
            f"{self.mes_referencia:02d} v{self.versao} {self.status}>"
        )


class CredencialColetaMensal(TimestampMixin, db.Model):
    __tablename__ = "credenciais_coletas_mensais"
    __table_args__ = (
        db.UniqueConstraint("ano_referencia", "mes_referencia", name="uq_credenciais_coletas_mensais_competencia"),
    )

    id = db.Column(db.Integer, primary_key=True)
    ano_referencia = db.Column(db.Integer, nullable=False, index=True)
    mes_referencia = db.Column(db.Integer, nullable=False, index=True)
    quantidade_localizada = db.Column(db.Integer, nullable=False, default=0)

    def __repr__(self):
        return (
            f"<CredencialColetaMensal {self.ano_referencia:04d}-"
            f"{self.mes_referencia:02d}: {self.quantidade_localizada}>"
        )


class ConscientizacaoCampanha(TimestampMixin, db.Model):
    __tablename__ = "conscientizacao_campanhas"

    id = db.Column(db.Integer, primary_key=True)
    titulo = db.Column(db.String(150), nullable=False, index=True)
    imagem_arquivo = db.Column(db.String(255), nullable=False, unique=True)
    imagem_mime_type = db.Column(db.String(50), nullable=False)
    imagem_tamanho = db.Column(db.BigInteger, nullable=False)
    data_publicacao = db.Column(db.Date, nullable=False, index=True)
    created_by_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=True, index=True)

    created_by = db.relationship("User", backref="conscientizacoes_criadas")

    def __repr__(self):
        return f"<ConscientizacaoCampanha {self.id} - {self.titulo}>"


class BackupConfig(TimestampMixin, db.Model):
    __tablename__ = "backup_config"

    id = db.Column(db.Integer, primary_key=True)
    diretorio = db.Column(db.String(500), nullable=False)
    intervalo_horas = db.Column(db.Integer, nullable=False, default=6)
    habilitado = db.Column(db.Boolean, nullable=False, default=True)
    retencao_dias = db.Column(db.Integer, nullable=False, default=30)
    min_backups_completos = db.Column(db.Integer, nullable=False, default=4)
    ultima_execucao = db.Column(db.DateTime(timezone=True), nullable=True)
    proxima_execucao = db.Column(db.DateTime(timezone=True), nullable=True)
    ultimo_resultado = db.Column(db.String(30), nullable=True)
    formato_versao = db.Column(db.String(20), nullable=False, default="1")
    updated_by_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=True, index=True)

    updated_by = db.relationship("User", backref="backup_configs_alteradas")

    def __repr__(self):
        return f"<BackupConfig {self.diretorio}>"


class BackupRegistro(TimestampMixin, db.Model):
    __tablename__ = "backup_registros"

    id = db.Column(db.Integer, primary_key=True)
    backup_uid = db.Column(db.String(64), nullable=False, unique=True, index=True)
    tipo = db.Column(db.String(20), nullable=False, index=True)
    status = db.Column(db.String(30), nullable=False, default="EM_ANDAMENTO", index=True)
    arquivo_nome = db.Column(db.String(255), nullable=False)
    arquivo_caminho = db.Column(db.String(700), nullable=False)
    manifesto_caminho = db.Column(db.String(700), nullable=True)
    base_backup_uid = db.Column(db.String(64), nullable=True, index=True)
    backup_anterior_uid = db.Column(db.String(64), nullable=True, index=True)
    pacote_sha256 = db.Column(db.String(64), nullable=True)
    tamanho_bytes = db.Column(db.BigInteger, nullable=False, default=0)
    conteudos = db.Column(db.Text, nullable=True)
    criado_por = db.Column(db.String(20), nullable=False, default="automatico")
    usuario_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=True, index=True)
    iniciado_em = db.Column(db.DateTime(timezone=True), nullable=False, default=utc_now, index=True)
    concluido_em = db.Column(db.DateTime(timezone=True), nullable=True)
    duracao_ms = db.Column(db.Integer, nullable=True)
    erro_sanitizado = db.Column(db.String(500), nullable=True)
    integridade_status = db.Column(db.String(30), nullable=False, default="NAO_VALIDADO")
    app_commit = db.Column(db.String(80), nullable=True)

    usuario = db.relationship("User", backref="backups_solicitados")

    def __repr__(self):
        return f"<BackupRegistro {self.backup_uid} {self.tipo} {self.status}>"


class AuditLog(db.Model):
    __tablename__ = "audit_logs"

    id = db.Column(db.Integer, primary_key=True)
    timestamp = db.Column(db.DateTime(timezone=True), nullable=False, index=True)
    request_id = db.Column(db.String(64), nullable=True, index=True)
    usuario_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=True, index=True)
    usuario_identificacao = db.Column(db.String(255), nullable=False)
    acao = db.Column(db.String(50), nullable=False, index=True)
    modulo = db.Column(db.String(100), nullable=False, index=True)
    entidade = db.Column(db.String(100), nullable=True)
    entidade_id = db.Column(db.String(100), nullable=True, index=True)
    descricao = db.Column(db.String(500), nullable=False)
    alteracoes = db.Column(db.JSON, nullable=True)
    ip_address = db.Column(db.String(45), nullable=True)
    user_agent = db.Column(db.String(500), nullable=True)
    endpoint = db.Column(db.String(255), nullable=True)
    metodo_http = db.Column(db.String(10), nullable=True)
    resultado = db.Column(db.String(30), nullable=False, default="SUCESSO")

    @property
    def occurred_at(self):
        return self.timestamp

    @property
    def actor_user_id(self):
        return self.usuario_id

    @property
    def actor_name(self):
        return self.usuario_identificacao

    @property
    def action(self):
        return self.acao

    @property
    def entity_type(self):
        return self.entidade

    @property
    def entity_id(self):
        return self.entidade_id

    @property
    def source_ip(self):
        return self.ip_address

    @property
    def old_values(self):
        values = {}
        for key, change in (self.alteracoes or {}).items():
            if isinstance(change, dict) and "anterior" in change:
                values[key] = change.get("anterior")
        return values

    @property
    def new_values(self):
        values = {}
        for key, change in (self.alteracoes or {}).items():
            if isinstance(change, dict) and "novo" in change:
                values[key] = change.get("novo")
        return values

    def __repr__(self):
        return f'<AuditLog {self.acao} {self.modulo} {self.timestamp}>'
    
    
