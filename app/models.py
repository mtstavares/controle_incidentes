# app/models.py

from datetime import datetime, timezone
from app import db # Importando a instância do SQLAlchemy de app/__init__.py
from werkzeug.security import generate_password_hash, check_password_hash # Importando funções para hash de senha
from flask_login import UserMixin # Importando UserMixin para integração com Flask-Login


def utc_now():
    return datetime.now(timezone.utc)


class TimestampMixin:
    created_at = db.Column(db.DateTime(timezone=True), nullable=False, default=utc_now)
    updated_at = db.Column(db.DateTime(timezone=True), nullable=False, default=utc_now, onupdate=utc_now)
    deleted_at = db.Column(db.DateTime(timezone=True), nullable=True)


class User(UserMixin, TimestampMixin, db.Model):
    id = db.Column(db.Integer, primary_key=True) # ID do usuário
    username = db.Column(db.String(50), unique=True, nullable=False, index=True) # Nome de usuário único
    name = db.Column(db.String(150), nullable=False) # Nome do usuário
    email = db.Column(db.String(255), unique=True, nullable=False, index=True) # Email do usuário único
    profile = db.Column(db.String(50), nullable=False) # Perfil do usuário (admin, user ou viewer)
    is_temp_password = db.Column(db.Boolean, default=True, nullable=False)
    must_change_password = db.Column(db.Boolean, default=True, nullable=False)
    is_active = db.Column(db.Boolean, default=True, nullable=False, index=True)
    deleted_by_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=True, index=True)
    password = db.Column(db.String(256), nullable=False) # Hash da senha do usuário
    
    
    # Relacionamento: um usuário pode ter várias análises e várias observações
    # 'backref' permite acessar o usuário a partir da análise (ex: analise.autor)
    incidente = db.relationship('Incidente', backref='autor', lazy=True)
    observacoes = db.relationship('IncidenteObs', backref='autor_obs', lazy=True)
    audit_logs = db.relationship('AuditLog', backref='usuario', lazy=True)
    deleted_by = db.relationship('User', remote_side=[id], foreign_keys=[deleted_by_id], post_update=True)
    
    # def set_password(self, password):
    #     self.password_hash = generate_password_hash(password) # Gera o hash da senha
    # def set_password(self, password):
    #     self.password_hash = generate_password_hash(password) # Gera o hash da senha
    
    # def check_password(self, password):
    #     return check_password_hash(self.password_hash, password) # Verifica a senha fornecida com o hash armazenado
    # def check_password(self, password):
    #     return check_password_hash(self.password_hash, password) # Verifica a senha fornecida com o hash armazenado
    
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
            "email",
            "url_origem",
            "data_coleta",
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
    imported_at = db.Column(db.DateTime(timezone=True), nullable=False, default=utc_now, index=True)
    imported_by_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=True, index=True)
    imported_by = db.relationship("User", backref="credenciais_importadas")

    def __repr__(self):
        return f"<CredencialComprometida {self.id} - {self.cpf}>"


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
    
    
