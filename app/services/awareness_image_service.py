import os
from pathlib import Path
from uuid import uuid4

from flask import current_app
from werkzeug.utils import secure_filename


ALLOWED_IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp"}
MIME_BY_EXTENSION = {
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".png": "image/png",
    ".webp": "image/webp",
}
ALLOWED_MIME_TYPES = set(MIME_BY_EXTENSION.values())


class AwarenessImageValidationError(ValueError):
    pass


def get_awareness_upload_folder():
    folder = Path(current_app.config.get(
        "AWARENESS_UPLOAD_FOLDER",
        Path(current_app.instance_path) / "uploads" / "conscientizacoes",
    ))
    folder.mkdir(parents=True, exist_ok=True)
    return folder


def _stream_size(storage):
    stream = storage.stream
    position = stream.tell()
    stream.seek(0, os.SEEK_END)
    size = stream.tell()
    stream.seek(position, os.SEEK_SET)
    return size


def _read_file(storage):
    stream = storage.stream
    position = stream.tell()
    stream.seek(0)
    data = stream.read()
    stream.seek(position, os.SEEK_SET)
    return data


def _validate_extension(filename):
    safe_name = secure_filename(filename or "")
    suffixes = [suffix.lower() for suffix in Path(safe_name).suffixes]
    extension = Path(safe_name).suffix.lower()
    if not safe_name or extension not in ALLOWED_IMAGE_EXTENSIONS:
        raise AwarenessImageValidationError("Formato de imagem não permitido.")
    if len(suffixes) != 1:
        raise AwarenessImageValidationError("Nome de arquivo com extensão inválida.")
    return extension


def _validate_mime(storage, extension):
    expected_mime = MIME_BY_EXTENSION[extension]
    supplied_mime = (storage.mimetype or "").lower()
    if supplied_mime not in ALLOWED_MIME_TYPES or supplied_mime != expected_mime:
        raise AwarenessImageValidationError("Tipo MIME da imagem não corresponde ao formato permitido.")


def _looks_like_png(data):
    return (
        data.startswith(b"\x89PNG\r\n\x1a\n")
        and len(data) >= 33
        and data[12:16] == b"IHDR"
        and data.endswith(b"IEND\xaeB`\x82")
    )


def _looks_like_jpeg(data):
    return data.startswith(b"\xff\xd8\xff") and data.endswith(b"\xff\xd9") and len(data) > 20


def _looks_like_webp(data):
    return (
        data.startswith(b"RIFF")
        and len(data) >= 16
        and data[8:12] == b"WEBP"
        and data[12:16] in {b"VP8 ", b"VP8L", b"VP8X"}
    )


def _validate_content(data, extension):
    validators = {
        ".png": _looks_like_png,
        ".jpg": _looks_like_jpeg,
        ".jpeg": _looks_like_jpeg,
        ".webp": _looks_like_webp,
    }
    if not validators[extension](data):
        raise AwarenessImageValidationError("Imagem inválida ou corrompida.")


def validate_awareness_image(storage):
    if not storage or not storage.filename:
        raise AwarenessImageValidationError("Selecione uma imagem.")

    extension = _validate_extension(storage.filename)
    _validate_mime(storage, extension)

    size = _stream_size(storage)
    if size <= 0:
        raise AwarenessImageValidationError("Imagem vazia não é permitida.")
    if size > current_app.config.get("MAX_AWARENESS_IMAGE_SIZE", 5 * 1024 * 1024):
        raise AwarenessImageValidationError("A imagem excede o limite permitido.")

    data = _read_file(storage)
    _validate_content(data, extension)
    return extension, size


def save_awareness_image(storage):
    extension, size = validate_awareness_image(storage)
    folder = get_awareness_upload_folder()

    stored_filename = f"{uuid4().hex}{extension}"
    destination = (folder / stored_filename).resolve()
    folder_resolved = folder.resolve()
    if folder_resolved not in destination.parents:
        raise AwarenessImageValidationError("Caminho de imagem inválido.")

    storage.stream.seek(0)
    storage.save(destination)
    return {
        "stored_filename": stored_filename,
        "mime_type": MIME_BY_EXTENSION[extension],
        "size": size,
        "path": destination,
    }


def resolve_awareness_image_path(stored_filename):
    if not stored_filename or Path(stored_filename).name != stored_filename:
        raise AwarenessImageValidationError("Imagem inválida.")
    folder = get_awareness_upload_folder().resolve()
    candidate = (folder / stored_filename).resolve()
    if folder not in candidate.parents or not candidate.exists():
        raise AwarenessImageValidationError("Imagem não encontrada.")
    return candidate


def delete_awareness_image(stored_filename, *, raise_on_error=False):
    try:
        path = resolve_awareness_image_path(stored_filename)
    except AwarenessImageValidationError:
        if raise_on_error:
            raise
        return
    try:
        path.unlink()
    except OSError as exc:
        current_app.logger.exception("Falha ao remover imagem de conscientização.")
        if raise_on_error:
            raise AwarenessImageValidationError("Não foi possível remover a imagem da campanha.") from exc
