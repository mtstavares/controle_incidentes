import hashlib
import os
import zipfile
from io import BytesIO
from pathlib import Path
from uuid import uuid4

from flask import current_app
from werkzeug.utils import secure_filename

from app.models import IncidentAttachment
from app.services.timezone_service import utc_now


ALLOWED_EXTENSIONS = {".pdf", ".png", ".jpg", ".jpeg", ".webp", ".doc", ".docx", ".xls", ".xlsx"}
BLOCKED_EXTENSIONS = {
    ".exe", ".dll", ".ps1", ".bat", ".cmd", ".js", ".vbs", ".jar", ".msi", ".scr",
    ".com", ".hta", ".lnk", ".iso", ".img", ".html", ".htm", ".svg", ".zip", ".rar",
    ".7z", ".tar", ".gz",
}
MIME_BY_EXTENSION = {
    ".pdf": "application/pdf",
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".webp": "image/webp",
    ".doc": "application/msword",
    ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    ".xls": "application/vnd.ms-excel",
    ".xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
}
MAGIC_MIME_BY_EXTENSION = {
    ".pdf": {"application/pdf"},
    ".png": {"image/png"},
    ".jpg": {"image/jpeg"},
    ".jpeg": {"image/jpeg"},
    ".webp": {"image/webp"},
    ".doc": {"application/msword", "application/vnd.ms-office", "application/ole", "application/octet-stream"},
    ".xls": {"application/vnd.ms-excel", "application/vnd.ms-office", "application/ole", "application/octet-stream"},
    ".docx": {"application/vnd.openxmlformats-officedocument.wordprocessingml.document", "application/zip", "application/octet-stream"},
    ".xlsx": {"application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", "application/zip", "application/octet-stream"},
}
SIGNATURES = {
    ".pdf": (b"%PDF-",),
    ".png": (b"\x89PNG\r\n\x1a\n",),
    ".jpg": (b"\xff\xd8\xff",),
    ".jpeg": (b"\xff\xd8\xff",),
    ".webp": (b"RIFF",),
    ".doc": (b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1",),
    ".xls": (b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1",),
    ".docx": (b"PK\x03\x04", b"PK\x05\x06", b"PK\x07\x08"),
    ".xlsx": (b"PK\x03\x04", b"PK\x05\x06", b"PK\x07\x08"),
}

try:
    import magic
except ImportError:  # pragma: no cover - depends on optional native package.
    magic = None


class AttachmentValidationError(ValueError):
    pass


def get_upload_folder():
    folder = Path(current_app.config.get(
        "INCIDENT_UPLOAD_FOLDER",
        Path(current_app.instance_path) / "uploads" / "incidents",
    ))
    folder.mkdir(parents=True, exist_ok=True)
    return folder


def _sha256(path):
    digest = hashlib.sha256()
    with open(path, "rb") as file_obj:
        for chunk in iter(lambda: file_obj.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _stream_size(storage):
    # Validate size before saving to disk, preventing upload-based DoS.
    stream = storage.stream
    current_position = stream.tell()
    stream.seek(0, os.SEEK_END)
    size = stream.tell()
    stream.seek(current_position, os.SEEK_SET)
    return size


def _read_head(storage, length=8192):
    stream = storage.stream
    current_position = stream.tell()
    stream.seek(0)
    head = stream.read(length)
    stream.seek(current_position, os.SEEK_SET)
    return head


def _detect_mime(head):
    if not magic:
        return None
    try:
        return magic.from_buffer(head, mime=True)
    except Exception:
        current_app.logger.warning("Falha ao detectar MIME com python-magic.", exc_info=True)
        return None


def _looks_like_ooxml(storage, extension):
    stream = storage.stream
    current_position = stream.tell()
    try:
        stream.seek(0)
        data = stream.read()
        with zipfile.ZipFile(BytesIO(data)) as archive:
            names = set(archive.namelist())
    except (OSError, zipfile.BadZipFile):
        return False
    finally:
        stream.seek(current_position, os.SEEK_SET)

    if "[Content_Types].xml" not in names:
        return False
    if extension == ".docx":
        return any(name.startswith("word/") for name in names)
    if extension == ".xlsx":
        return any(name.startswith("xl/") for name in names)
    return False


def _validate_filename(original, extension):
    suffixes = [suffix.lower() for suffix in Path(original).suffixes]
    if extension in BLOCKED_EXTENSIONS or extension not in ALLOWED_EXTENSIONS:
        raise AttachmentValidationError("Tipo de arquivo não permitido.")
    if any(suffix in BLOCKED_EXTENSIONS for suffix in suffixes):
        raise AttachmentValidationError("Nome de arquivo com extensão bloqueada.")


def _validate_file_signature(storage, extension):
    # Magic bytes are checked independently of client-supplied MIME/extension.
    head = _read_head(storage)
    if not any(head.startswith(signature) for signature in SIGNATURES.get(extension, ())):
        raise AttachmentValidationError("Assinatura do arquivo não corresponde ao tipo permitido.")
    if extension == ".webp" and head[8:12] != b"WEBP":
        raise AttachmentValidationError("Assinatura do arquivo não corresponde ao tipo permitido.")
    if extension in {".docx", ".xlsx"} and not _looks_like_ooxml(storage, extension):
        raise AttachmentValidationError("Documento Office inválido ou incompatível.")

    detected_mime = _detect_mime(head)
    if detected_mime and detected_mime not in MAGIC_MIME_BY_EXTENSION.get(extension, set()):
        raise AttachmentValidationError("Conteúdo do arquivo não corresponde à extensão informada.")


def save_incident_attachments(files, incident, user):
    uploaded = []
    saved_paths = []
    incoming = [file for file in files if file and file.filename]
    existing_count = len(incident.attachments or [])

    if existing_count + len(incoming) > current_app.config.get("MAX_ATTACHMENTS_PER_INCIDENT", 10):
        raise AttachmentValidationError("Quantidade máxima de anexos excedida.")

    current_total = sum(attachment.file_size for attachment in incident.attachments or [])
    upload_folder = get_upload_folder()

    try:
        for storage in incoming:
            original = secure_filename(storage.filename) or "arquivo"
            extension = Path(original).suffix.lower()
            _validate_filename(original, extension)

            size = _stream_size(storage)
            if size <= 0:
                raise AttachmentValidationError("Arquivo vazio não é permitido.")
            if size > current_app.config.get("MAX_ATTACHMENT_SIZE", 20 * 1024 * 1024):
                raise AttachmentValidationError("O arquivo excede o limite permitido.")
            if current_total + size > current_app.config.get("MAX_INCIDENT_ATTACHMENTS_SIZE", 50 * 1024 * 1024):
                raise AttachmentValidationError("O limite total de anexos do incidente foi excedido.")
            _validate_file_signature(storage, extension)

            stored_filename = f"{uuid4().hex}{extension}"
            destination = upload_folder / stored_filename
            storage.stream.seek(0)
            storage.save(destination)
            saved_paths.append(destination)
            current_total += size

            attachment = IncidentAttachment(
                incident_id=incident.id,
                original_filename=original[:255],
                stored_filename=stored_filename,
                mime_type=MIME_BY_EXTENSION.get(extension, "application/octet-stream"),
                file_size=size,
                sha256=_sha256(destination),
                uploaded_by_id=user.id,
                uploaded_at=utc_now(),
            )
            uploaded.append(attachment)
        return uploaded
    except Exception:
        for path in saved_paths:
            try:
                if path.exists():
                    path.unlink()
            except OSError:
                current_app.logger.exception("Falha ao remover anexo temporário: %s", path)
        raise


def resolve_attachment_path(attachment):
    upload_folder = get_upload_folder().resolve()
    candidate = (upload_folder / attachment.stored_filename).resolve()
    if upload_folder not in candidate.parents and candidate != upload_folder:
        raise AttachmentValidationError("Caminho de anexo inválido.")
    if not candidate.exists():
        raise AttachmentValidationError("Arquivo não encontrado.")
    return candidate


def delete_attachment_file(attachment):
    try:
        path = resolve_attachment_path(attachment)
    except AttachmentValidationError:
        return
    try:
        os.remove(path)
    except OSError:
        current_app.logger.exception("Falha ao remover anexo %s", attachment.id)
