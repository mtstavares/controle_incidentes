import hashlib
import os
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

from flask import current_app
from werkzeug.utils import secure_filename

from app.models import IncidentAttachment


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


class AttachmentValidationError(ValueError):
    pass


def get_upload_folder():
    folder = Path(current_app.config.get("INCIDENT_UPLOAD_FOLDER", Path(current_app.instance_path) / "uploads" / "incidents"))
    folder.mkdir(parents=True, exist_ok=True)
    return folder


def _sha256(path):
    digest = hashlib.sha256()
    with open(path, "rb") as file_obj:
        for chunk in iter(lambda: file_obj.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


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
            if extension in BLOCKED_EXTENSIONS or extension not in ALLOWED_EXTENSIONS:
                raise AttachmentValidationError("Tipo de arquivo não permitido.")

            stored_filename = f"{uuid4().hex}{extension}"
            destination = upload_folder / stored_filename
            storage.save(destination)
            saved_paths.append(destination)

            size = destination.stat().st_size
            if size <= 0:
                raise AttachmentValidationError("Arquivo vazio não é permitido.")
            if size > current_app.config.get("MAX_ATTACHMENT_SIZE", 20 * 1024 * 1024):
                raise AttachmentValidationError("O arquivo excede o limite permitido.")
            current_total += size
            if current_total > current_app.config.get("MAX_INCIDENT_ATTACHMENTS_SIZE", 50 * 1024 * 1024):
                raise AttachmentValidationError("O limite total de anexos do incidente foi excedido.")

            attachment = IncidentAttachment(
                incident_id=incident.id,
                original_filename=original[:255],
                stored_filename=stored_filename,
                mime_type=MIME_BY_EXTENSION.get(extension, "application/octet-stream"),
                file_size=size,
                sha256=_sha256(destination),
                uploaded_by_id=user.id,
                uploaded_at=datetime.now(timezone.utc),
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
