import os

from dotenv import load_dotenv


load_dotenv()


def _required_env(name):
    """Fail closed in production when critical configuration is missing."""
    value = os.getenv(name)
    if not value:
        raise ValueError(f"Variavel de ambiente obrigatoria ausente: {name}")
    return value


class Config:
    BASE_DIR = os.path.abspath(os.path.dirname(__file__))
    SECRET_KEY = os.getenv("SECRET_KEY", "dev-secret-key-change-me")
    SQLALCHEMY_DATABASE_URI = os.getenv(
        "DATABASE_URL",
        "sqlite:///" + os.path.join(BASE_DIR, "instance", "divciber.db"),
    )

    # Global request cap: Flask rejects oversized payloads before app processing.
    MAX_CONTENT_LENGTH = 50 * 1024 * 1024

    SQLALCHEMY_TRACK_MODIFICATIONS = False
    RATELIMIT_STORAGE_URI = os.getenv("RATELIMIT_STORAGE_URI", "memory://")
    TIMEZONE = os.getenv("TIMEZONE", "America/Sao_Paulo")
    MAX_ATTACHMENT_SIZE = 20 * 1024 * 1024
    MAX_INCIDENT_ATTACHMENTS_SIZE = 50 * 1024 * 1024
    MAX_ATTACHMENTS_PER_INCIDENT = 10
    INCIDENT_UPLOAD_FOLDER = os.path.join(BASE_DIR, "instance", "uploads", "incidents")


class DevelopmentConfig(Config):
    DEBUG = False


class ProductionConfig(Config):
    DEBUG = False
    SECRET_KEY = os.getenv("SECRET_KEY")
    SQLALCHEMY_DATABASE_URI = os.getenv("DATABASE_URL")

    @classmethod
    def validate(cls):
        cls.SECRET_KEY = _required_env("SECRET_KEY")
        cls.SQLALCHEMY_DATABASE_URI = _required_env("DATABASE_URL")
