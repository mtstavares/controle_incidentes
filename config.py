import os


class Config:
    from dotenv import load_dotenv
    load_dotenv()
    BASE_DIR = os.path.abspath(os.path.dirname(__file__))
    SECRET_KEY = os.getenv('SECRET_KEY', 'dev-secret-key-change-me')
    SQLALCHEMY_DATABASE_URI = os.getenv(
        'DATABASE_URL',
        'sqlite:///' + os.path.join(BASE_DIR, 'instance', 'divciber.db')
    )
    
    # Desativa o rastreamento de modificações do SQLAlchemy para economizar memória
    SQLALCHEMY_TRACK_MODIFICATIONS = False
    MAX_ATTACHMENT_SIZE = 20 * 1024 * 1024
    MAX_INCIDENT_ATTACHMENTS_SIZE = 50 * 1024 * 1024
    MAX_ATTACHMENTS_PER_INCIDENT = 10
    INCIDENT_UPLOAD_FOLDER = os.path.join(BASE_DIR, 'instance', 'uploads', 'incidents')
    

class DevelopmentConfig(Config):
    DEBUG = False # Ativa o modo debug (recarregamento automático, mensagens de erro detalhadas)
    
class ProductionConfig(Config):
    DEBUG = False # Desativa o modo debug


























