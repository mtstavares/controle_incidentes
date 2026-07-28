import base64
import hashlib
import hmac
import io
import json
import os
import secrets
import shutil
import sqlite3
import tempfile
import threading
import time
import zipfile
from contextlib import contextmanager
from datetime import timedelta, timezone
from pathlib import Path

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from flask import current_app
from sqlalchemy.engine import make_url

from app import db
from app.models import BackupConfig, BackupRegistro
from app.services.audit_service import AuditAction, registrar_auditoria
from app.services.timezone_service import local_now, to_local, utc_now

FORMAT_VERSION = "1"
HASH_ALGORITHM = "SHA-256"
BACKUP_MODULE = "Administração - Backup"
LOCK_FILE_NAME = ".divciber-backup.lock"
MONTHS_PT = {
    1: "JAN",
    2: "FEV",
    3: "MAR",
    4: "ABR",
    5: "MAI",
    6: "JUN",
    7: "JUL",
    8: "AGO",
    9: "SET",
    10: "OUT",
    11: "NOV",
    12: "DEZ",
}

INCLUDED_COMPONENTS = {
    "database": "Snapshot consistente do banco SQLite principal da aplicação.",
    "uploads/incidents": "Anexos de incidentes persistidos fora do diretório público.",
    "uploads/conscientizacoes": "Imagens de campanhas de conscientização persistidas fora do diretório público.",
}
EXCLUDED_COMPONENTS = {
    ".env": "Segredos e credenciais devem ser recuperados pelo cofre/ambiente, não pelo backup.",
    "logs": "Logs operacionais podem conter ruído e não são necessários para restauração transacional.",
    "tmp": "Arquivos temporários são recriados pela aplicação.",
    "cache/build/venv": "Artefatos recompiláveis não fazem parte do estado persistente.",
}

_scheduler_started = False
_scheduler_lock = threading.Lock()


class BackupError(RuntimeError):
    pass


class BackupConfigError(BackupError):
    pass


class BackupIntegrityError(BackupError):
    pass


def _sanitize_error(exc):
    return str(exc).replace("\n", " ").replace("\r", " ")[:500]


def _sha256_bytes(data):
    return hashlib.sha256(data).hexdigest()


def _sha256_file(path):
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _canonical_json(data):
    return json.dumps(data, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")


def _manifest_aad(manifest):
    data = dict(manifest)
    data.pop("manifest_hmac", None)
    data.pop("encrypted_payload_sha256", None)
    return _canonical_json(data)


def _date_code(value=None):
    local_value = value or local_now()
    return f"{local_value.day:02d}{MONTHS_PT[local_value.month]}{local_value.year % 100:02d}_{local_value:%H%M%S}"


def _default_backup_dir():
    return current_app.config.get("DIVCIBER_BACKUP_DEFAULT_DIR")


def _legacy_instance_backup_dir():
    return str((_instance_root() / "backups").resolve())


def _next_run_from(now_value, interval_hours):
    return now_value + timedelta(hours=interval_hours)


def _as_aware_utc(value):
    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _database_path():
    uri = current_app.config["SQLALCHEMY_DATABASE_URI"]
    url = make_url(uri)
    if url.drivername != "sqlite":
        raise BackupConfigError("O backup transacional implementado está habilitado apenas para SQLite.")
    database = url.database
    if not database:
        raise BackupConfigError("Banco SQLite não localizado na configuração.")
    return Path(database).resolve()


def _instance_root():
    return Path(current_app.instance_path).resolve()


def _safe_resolve_backup_dir(value):
    raw = Path((value or "").strip()).expanduser()
    if not raw.is_absolute():
        raise BackupConfigError("Informe um diretório absoluto para backup.")
    resolved = raw.resolve()
    if resolved == _instance_root() or _instance_root() in resolved.parents:
        raise BackupConfigError("O diretório de backup não pode ficar dentro do diretório de dados da aplicação.")
    return resolved


def validate_backup_directory(value, *, create=False):
    target = _safe_resolve_backup_dir(value)
    if target.exists() and not target.is_dir():
        raise BackupConfigError("O caminho informado não é um diretório.")
    if not target.exists():
        if not create:
            raise BackupConfigError("O diretório informado não existe.")
        try:
            target.mkdir(parents=True, mode=0o700, exist_ok=False)
        except OSError as exc:
            raise BackupConfigError("Não foi possível criar o diretório de backup.") from exc
    if target.is_symlink():
        raise BackupConfigError("Links simbólicos não são aceitos como diretório de backup.")
    probe = target / f".divciber-write-test-{secrets.token_hex(8)}"
    try:
        probe.write_text("ok", encoding="utf-8")
        probe.unlink()
    except OSError as exc:
        raise BackupConfigError("Não foi possível gravar no diretório de backup.") from exc
    return target


def _ensure_layout(root):
    for child in ("completos", "incrementais", "manifestos", "temporarios", "quarentena"):
        (root / child).mkdir(parents=True, exist_ok=True)


def get_or_create_config():
    config = BackupConfig.query.order_by(BackupConfig.id.asc()).first()
    if config:
        if config.diretorio == _legacy_instance_backup_dir():
            config.diretorio = _default_backup_dir()
            config.updated_at = utc_now()
            db.session.commit()
        return config
    now_value = utc_now()
    interval = int(current_app.config.get("DIVCIBER_BACKUP_INTERVAL_HOURS", 6))
    config = BackupConfig(
        diretorio=_default_backup_dir(),
        intervalo_horas=interval,
        habilitado=True,
        retencao_dias=int(current_app.config.get("DIVCIBER_BACKUP_RETENTION_DAYS", 30)),
        min_backups_completos=int(current_app.config.get("DIVCIBER_BACKUP_MIN_FULL", 4)),
        formato_versao=FORMAT_VERSION,
        proxima_execucao=_next_run_from(now_value, interval),
    )
    db.session.add(config)
    db.session.commit()
    return config


def update_config(*, diretorio, intervalo_horas, habilitado, retencao_dias, min_backups_completos, create_dir, user):
    if isinstance(intervalo_horas, bool) or not isinstance(intervalo_horas, int) or not 1 <= intervalo_horas <= 168:
        raise BackupConfigError("Intervalo deve ser um número inteiro entre 1 e 168 horas.")
    if isinstance(retencao_dias, bool) or not isinstance(retencao_dias, int) or not 1 <= retencao_dias <= 3650:
        raise BackupConfigError("Retenção deve ser um número inteiro entre 1 e 3650 dias.")
    if (
        isinstance(min_backups_completos, bool)
        or not isinstance(min_backups_completos, int)
        or not 1 <= min_backups_completos <= 100
    ):
        raise BackupConfigError("Quantidade mínima de backups completos deve ficar entre 1 e 100.")
    backup_dir = validate_backup_directory(diretorio, create=create_dir)
    _ensure_layout(backup_dir)

    config = get_or_create_config()
    old = {
        "diretorio": config.diretorio,
        "intervalo_horas": config.intervalo_horas,
        "habilitado": config.habilitado,
        "retencao_dias": config.retencao_dias,
        "min_backups_completos": config.min_backups_completos,
    }
    config.diretorio = str(backup_dir)
    config.intervalo_horas = intervalo_horas
    config.habilitado = bool(habilitado)
    config.retencao_dias = retencao_dias
    config.min_backups_completos = min_backups_completos
    config.proxima_execucao = _next_run_from(utc_now(), intervalo_horas) if config.habilitado else None
    config.updated_by_id = getattr(user, "id", None)
    config.updated_at = utc_now()
    registrar_auditoria(
        acao=AuditAction.EDITAR,
        modulo=BACKUP_MODULE,
        entidade="BackupConfig",
        entidade_id=config.id,
        descricao="Configuração de backup atualizada.",
        alteracoes={key: {"anterior": old[key], "novo": getattr(config, key)} for key in old},
        commit=False,
        raise_on_error=True,
    )
    db.session.commit()
    return config


def _hmac_key():
    value = current_app.config.get("DIVCIBER_BACKUP_HMAC_KEY")
    if not value or len(value.strip()) < 32:
        raise BackupConfigError("Configure DIVCIBER_BACKUP_HMAC_KEY com valor forte antes de executar backups.")
    return value.encode("utf-8")


def _encryption_key():
    value = current_app.config.get("DIVCIBER_BACKUP_ENCRYPTION_KEY")
    if not value:
        raise BackupConfigError("Configure DIVCIBER_BACKUP_ENCRYPTION_KEY antes de executar backups.")
    text = value.strip()
    if len(text) == 64:
        try:
            key = bytes.fromhex(text)
        except ValueError:
            key = b""
        if len(key) == 32:
            return key
    try:
        padded = text + ("=" * (-len(text) % 4))
        key = base64.urlsafe_b64decode(padded)
    except Exception as exc:
        raise BackupConfigError("DIVCIBER_BACKUP_ENCRYPTION_KEY deve estar em hex ou base64 URL-safe.") from exc
    if len(key) != 32:
        raise BackupConfigError("DIVCIBER_BACKUP_ENCRYPTION_KEY deve representar exatamente 32 bytes.")
    return key


def generate_secret_material():
    return {
        "DIVCIBER_BACKUP_HMAC_KEY": secrets.token_urlsafe(48),
        "DIVCIBER_BACKUP_ENCRYPTION_KEY": base64.urlsafe_b64encode(AESGCM.generate_key(bit_length=256)).decode("ascii"),
    }


@contextmanager
def _filesystem_lock(root):
    _ensure_layout(root)
    lock_path = root / LOCK_FILE_NAME
    fd = None
    try:
        fd = os.open(str(lock_path), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        os.write(fd, f"{os.getpid()} {time.time()}".encode("ascii"))
        yield
    except FileExistsError as exc:
        raise BackupError("Já existe backup ou restauração em andamento.") from exc
    finally:
        if fd is not None:
            os.close(fd)
            try:
                lock_path.unlink()
            except FileNotFoundError:
                pass


def _last_valid_backup():
    return (
        BackupRegistro.query.filter_by(status="CONCLUIDO", integridade_status="VALIDO")
        .order_by(BackupRegistro.iniciado_em.desc(), BackupRegistro.id.desc())
        .first()
    )


def _last_valid_full():
    return (
        BackupRegistro.query.filter_by(tipo="COMPLETO", status="CONCLUIDO", integridade_status="VALIDO")
        .order_by(BackupRegistro.iniciado_em.desc(), BackupRegistro.id.desc())
        .first()
    )


def _load_manifest_from_package(package_path):
    with zipfile.ZipFile(package_path, "r") as package:
        manifest = json.loads(package.read("MANIFESTO.json").decode("utf-8"))
        stored_hmac = package.read("MANIFESTO.hmac").decode("ascii")
    expected = hmac.new(_hmac_key(), _canonical_json(manifest), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(stored_hmac, expected):
        raise BackupIntegrityError("HMAC do manifesto inválido.")
    return manifest


def _previous_snapshot_state(record):
    if not record:
        return {}
    package_path = Path(record.arquivo_caminho)
    if not package_path.exists():
        record.status = "INVALIDO"
        record.integridade_status = "INVALIDO"
        record.erro_sanitizado = "Arquivo físico do backup anterior não localizado."
        db.session.commit()
        raise BackupIntegrityError("Arquivo físico do backup anterior não localizado.")
    manifest = _load_manifest_from_package(package_path)
    return manifest.get("snapshot_state", {})


def _snapshot_database(temp_dir, date_code):
    source = _database_path()
    if not source.exists():
        raise BackupConfigError("Banco de dados principal não foi localizado.")
    target = temp_dir / f"DIVCIBER_DATABASE_{date_code}.db"
    source_conn = sqlite3.connect(str(source))
    try:
        dest_conn = sqlite3.connect(str(target))
        try:
            source_conn.backup(dest_conn)
            result = dest_conn.execute("PRAGMA integrity_check").fetchone()
            if not result or result[0] != "ok":
                raise BackupIntegrityError("Falha no integrity_check do snapshot SQLite.")
        finally:
            dest_conn.close()
    finally:
        source_conn.close()
    return target


def _iter_persistent_files():
    root = _instance_root()
    for relative_root in ("uploads/incidents", "uploads/conscientizacoes"):
        folder = root / relative_root
        if not folder.exists():
            continue
        for path in folder.rglob("*"):
            if path.is_file() and not path.is_symlink():
                yield relative_root, path.resolve()


def _collect_sources(temp_dir, date_code):
    sources = []
    db_snapshot = _snapshot_database(temp_dir, date_code)
    sources.append(("database/divciber.db", db_snapshot, "Banco de dados principal"))
    for relative_root, path in _iter_persistent_files():
        logical = f"{relative_root}/{path.name}"
        sources.append((logical, path, "Arquivo persistente"))
    return sources


def _payload_zip_bytes(entries):
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_DEFLATED) as payload:
        for logical, source_path, _description in entries:
            payload.write(source_path, logical)
    return buffer.getvalue()


def _write_manifest_copy(root, manifest, date_code):
    manifest_path = root / "manifestos" / f"MANIFESTO_{date_code}_{manifest['backup_uid']}.json"
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    return manifest_path


def _build_package(root, backup_type, date_code, manifest, payload_bytes):
    folder = root / ("completos" if backup_type == "COMPLETO" else "incrementais")
    filename = f"DIVCIBER_{backup_type}_{date_code}.zip"
    final_path = folder / filename
    tmp_path = folder / f".{filename}.{secrets.token_hex(8)}.tmp"

    manifest["payload_sha256"] = _sha256_bytes(payload_bytes)
    nonce = secrets.token_bytes(12)
    encrypted_payload = AESGCM(_encryption_key()).encrypt(nonce, payload_bytes, _manifest_aad(manifest))
    manifest["encrypted_payload_sha256"] = _sha256_bytes(encrypted_payload)
    manifest_hmac = hmac.new(_hmac_key(), _canonical_json(manifest), hashlib.sha256).hexdigest()

    with zipfile.ZipFile(tmp_path, "w", compression=zipfile.ZIP_DEFLATED) as package:
        package.writestr("MANIFESTO.json", json.dumps(manifest, ensure_ascii=False, indent=2))
        package.writestr("MANIFESTO.hmac", manifest_hmac)
        package.writestr("NONCE.bin", nonce)
        package.writestr("PAYLOAD.enc", encrypted_payload)
    with zipfile.ZipFile(tmp_path, "r") as package:
        package.testzip()
    os.replace(tmp_path, final_path)
    return final_path


def _decrypt_payload(package_path, manifest=None):
    manifest = manifest or _load_manifest_from_package(package_path)
    with zipfile.ZipFile(package_path, "r") as package:
        nonce = package.read("NONCE.bin")
        encrypted_payload = package.read("PAYLOAD.enc")
    if _sha256_bytes(encrypted_payload) != manifest.get("encrypted_payload_sha256"):
        raise BackupIntegrityError("Checksum do payload criptografado inválido.")
    payload = AESGCM(_encryption_key()).decrypt(nonce, encrypted_payload, _manifest_aad(manifest))
    if _sha256_bytes(payload) != manifest.get("payload_sha256"):
        raise BackupIntegrityError("Checksum do payload descriptografado inválido.")
    return payload


def _app_commit():
    head = Path(current_app.root_path).parent / ".git" / "HEAD"
    try:
        content = head.read_text(encoding="utf-8").strip()
        if content.startswith("ref:"):
            ref = head.parent / content.split(" ", 1)[1]
            return ref.read_text(encoding="utf-8").strip()[:80]
        return content[:80]
    except OSError:
        return None


def create_backup(*, manual=False, user=None, force_full=False):
    config = get_or_create_config()
    root = validate_backup_directory(config.diretorio, create=True)
    now_start = utc_now()
    local_start = to_local(now_start)
    date_code = _date_code(local_start)
    backup_uid = secrets.token_hex(16)
    previous = _last_valid_backup()
    latest_full = _last_valid_full()

    try:
        previous_state = _previous_snapshot_state(previous)
    except (BackupError, OSError, ValueError, zipfile.BadZipFile, InvalidTag) as exc:
        current_app.logger.warning(
            "Backup anterior ignorado por falha de integridade: %s",
            _sanitize_error(exc),
        )
        previous = None
        latest_full = None
        previous_state = {}

    backup_type = "COMPLETO" if force_full or latest_full is None else "INCREMENTAL"
    base_uid = backup_uid if backup_type == "COMPLETO" else latest_full.backup_uid
    previous_uid = previous.backup_uid if previous else None
    filename = f"DIVCIBER_{backup_type}_{date_code}.zip"
    relative_folder = "completos" if backup_type == "COMPLETO" else "incrementais"
    record = BackupRegistro(
        backup_uid=backup_uid,
        tipo=backup_type,
        status="EM_ANDAMENTO",
        arquivo_nome=filename,
        arquivo_caminho=str(root / relative_folder / filename),
        base_backup_uid=base_uid,
        backup_anterior_uid=previous_uid,
        criado_por="manual" if manual else "automatico",
        usuario_id=getattr(user, "id", None),
        iniciado_em=now_start,
        integridade_status="NAO_VALIDADO",
        app_commit=_app_commit(),
    )
    db.session.add(record)
    db.session.commit()

    action = "BACKUP_MANUAL" if manual else "BACKUP_AUTOMATICO"
    registrar_auditoria(
        acao=action,
        modulo=BACKUP_MODULE,
        entidade="BackupRegistro",
        entidade_id=backup_uid,
        descricao="Backup iniciado.",
        resultado="INICIADO",
        usuario=user,
    )

    try:
        with _filesystem_lock(root), tempfile.TemporaryDirectory(dir=root / "temporarios") as temp_name:
            temp_dir = Path(temp_name)
            sources = _collect_sources(temp_dir, date_code)
            snapshot_state = {}
            selected = []
            contents = set()
            for logical, source_path, description in sources:
                file_hash = _sha256_file(source_path)
                snapshot_state[logical] = {"sha256": file_hash, "size": source_path.stat().st_size}
                if backup_type == "COMPLETO" or previous_state.get(logical, {}).get("sha256") != file_hash:
                    selected.append((logical, source_path, description))
                    contents.add(logical.split("/", 1)[0] if "/" in logical else logical)

            payload_bytes = _payload_zip_bytes(selected)
            manifest = {
                "format_version": FORMAT_VERSION,
                "application": "DivCiber",
                "backup_uid": backup_uid,
                "backup_type": backup_type,
                "created_at_utc": now_start.isoformat(),
                "created_at_local": local_start.isoformat(),
                "hash_algorithm": HASH_ALGORITHM,
                "base_backup_uid": base_uid,
                "previous_backup_uid": previous_uid,
                "included_components": INCLUDED_COMPONENTS,
                "excluded_components": EXCLUDED_COMPONENTS,
                "included_files": [
                    {
                        "logical_path": logical,
                        "sha256": snapshot_state[logical]["sha256"],
                        "size": snapshot_state[logical]["size"],
                        "description": description,
                    }
                    for logical, _source_path, description in selected
                ],
                "snapshot_state": snapshot_state,
                "app_commit": record.app_commit,
            }
            package_path = _build_package(root, backup_type, date_code, manifest, payload_bytes)
            package_sha = _sha256_file(package_path)
            _load_manifest_from_package(package_path)
            _decrypt_payload(package_path, manifest)
            manifest_copy = _write_manifest_copy(root, manifest, date_code)

        finished = utc_now()
        record.status = "CONCLUIDO"
        record.integridade_status = "VALIDO"
        record.arquivo_caminho = str(package_path)
        record.manifesto_caminho = str(manifest_copy)
        record.pacote_sha256 = package_sha
        record.tamanho_bytes = package_path.stat().st_size
        record.conteudos = ", ".join(sorted(contents or {"sem_alteracoes"}))
        record.concluido_em = finished
        record.duracao_ms = int((finished - now_start).total_seconds() * 1000)
        config.ultima_execucao = finished
        config.proxima_execucao = _next_run_from(finished, config.intervalo_horas) if config.habilitado else None
        config.ultimo_resultado = "SUCESSO"
        db.session.commit()
        registrar_auditoria(
            acao=action,
            modulo=BACKUP_MODULE,
            entidade="BackupRegistro",
            entidade_id=backup_uid,
            descricao="Backup concluído com sucesso.",
            alteracoes={"tipo": {"anterior": None, "novo": backup_type}, "duracao_ms": {"anterior": None, "novo": record.duracao_ms}},
            usuario=user,
        )
        apply_retention_policy(config=config)
        return record
    except (BackupError, InvalidTag, OSError, sqlite3.Error, ValueError, zipfile.BadZipFile) as exc:
        finished = utc_now()
        record.status = "FALHA"
        record.integridade_status = "INVALIDO"
        record.erro_sanitizado = _sanitize_error(exc)
        record.concluido_em = finished
        record.duracao_ms = int((finished - now_start).total_seconds() * 1000)
        config.ultima_execucao = finished
        config.proxima_execucao = _next_run_from(finished, config.intervalo_horas) if config.habilitado else None
        config.ultimo_resultado = "FALHA"
        db.session.commit()
        registrar_auditoria(
            acao=action,
            modulo=BACKUP_MODULE,
            entidade="BackupRegistro",
            entidade_id=backup_uid,
            descricao="Backup falhou.",
            resultado="FALHA",
            usuario=user,
        )
        raise


def list_backup_records(filters=None):
    filters = filters or {}
    query = BackupRegistro.query
    if filters.get("tipo") in {"COMPLETO", "INCREMENTAL"}:
        query = query.filter(BackupRegistro.tipo == filters["tipo"])
    if filters.get("status") in {"CONCLUIDO", "FALHA", "INVALIDO", "QUARENTENA", "EXCLUIDO"}:
        query = query.filter(BackupRegistro.status == filters["status"])
    else:
        query = query.filter(BackupRegistro.status != "EXCLUIDO")
    return query.order_by(BackupRegistro.iniciado_em.desc(), BackupRegistro.id.desc())


def validate_backup_record(record):
    path = Path(record.arquivo_caminho)
    if not path.exists() or not path.is_file():
        record.integridade_status = "INVALIDO"
        record.status = "INVALIDO"
        record.erro_sanitizado = "Arquivo do backup não localizado."
        db.session.commit()
        raise BackupIntegrityError("Arquivo do backup não localizado.")
    if record.pacote_sha256 and _sha256_file(path) != record.pacote_sha256:
        record.integridade_status = "INVALIDO"
        record.status = "INVALIDO"
        record.erro_sanitizado = "Checksum do pacote divergente."
        db.session.commit()
        raise BackupIntegrityError("Checksum do pacote divergente.")
    manifest = _load_manifest_from_package(path)
    _decrypt_payload(path, manifest)
    record.integridade_status = "VALIDO"
    record.status = "CONCLUIDO"
    record.erro_sanitizado = None
    db.session.commit()
    registrar_auditoria(
        acao="VALIDAR_BACKUP",
        modulo=BACKUP_MODULE,
        entidade="BackupRegistro",
        entidade_id=record.backup_uid,
        descricao="Backup validado com sucesso.",
    )
    return manifest


def _chain_to(record):
    if record.status != "CONCLUIDO" or record.integridade_status != "VALIDO":
        raise BackupIntegrityError("Backup selecionado não está íntegro.")
    if record.tipo == "COMPLETO":
        return [record]
    full = BackupRegistro.query.filter_by(backup_uid=record.base_backup_uid).first()
    if not full or full.tipo != "COMPLETO":
        raise BackupIntegrityError("Backup completo de origem não foi localizado.")
    chain = [full]
    current = full
    while current.backup_uid != record.backup_uid:
        next_record = (
            BackupRegistro.query.filter_by(backup_anterior_uid=current.backup_uid, status="CONCLUIDO", integridade_status="VALIDO")
            .order_by(BackupRegistro.iniciado_em.asc(), BackupRegistro.id.asc())
            .first()
        )
        if not next_record:
            raise BackupIntegrityError("Cadeia incremental incompleta.")
        chain.append(next_record)
        current = next_record
        if len(chain) > 10000:
            raise BackupIntegrityError("Cadeia incremental inválida.")
    return chain


def _safe_extract_payload(payload_bytes, target):
    with zipfile.ZipFile(io.BytesIO(payload_bytes), "r") as payload:
        for info in payload.infolist():
            name = info.filename
            if info.is_dir() or Path(name).is_absolute() or ".." in Path(name).parts:
                raise BackupIntegrityError("Payload contém caminho inválido.")
            destination = (target / name).resolve()
            if target.resolve() not in destination.parents and destination != target.resolve():
                raise BackupIntegrityError("Payload tenta escrever fora do diretório permitido.")
            destination.parent.mkdir(parents=True, exist_ok=True)
            with payload.open(info, "r") as source, open(destination, "wb") as dest:
                shutil.copyfileobj(source, dest)


def restore_backup(record, *, confirmation, user):
    if confirmation != "RESTAURAR":
        raise BackupConfigError("Confirmação textual inválida.")
    config = get_or_create_config()
    root = validate_backup_directory(config.diretorio, create=False)
    chain = _chain_to(record)
    for item in chain:
        validate_backup_record(item)

    registrar_auditoria(
        acao="RESTAURACAO_SOLICITADA",
        modulo=BACKUP_MODULE,
        entidade="BackupRegistro",
        entidade_id=record.backup_uid,
        descricao="Restauração solicitada por administrador.",
        resultado="INICIADO",
        usuario=user,
    )

    pre_restore = create_backup(manual=True, user=user, force_full=True)
    with _filesystem_lock(root), tempfile.TemporaryDirectory(dir=root / "temporarios") as temp_name:
        restore_root = Path(temp_name) / "restore"
        restore_root.mkdir()
        for item in chain:
            manifest = validate_backup_record(item)
            payload = _decrypt_payload(Path(item.arquivo_caminho), manifest)
            _safe_extract_payload(payload, restore_root)

        db_source = restore_root / "database" / "divciber.db"
        if not db_source.exists():
            raise BackupIntegrityError("Cadeia selecionada não contém snapshot do banco.")
        check_conn = sqlite3.connect(str(db_source))
        try:
            result = check_conn.execute("PRAGMA integrity_check").fetchone()
            if not result or result[0] != "ok":
                raise BackupIntegrityError("Banco restaurado falhou no integrity_check.")
        finally:
            check_conn.close()

        database_path = _database_path()
        db.engine.dispose()
        database_backup = database_path.with_name(f"{database_path.name}.pre_restore_{secrets.token_hex(8)}")
        shutil.copy2(database_path, database_backup)
        try:
            shutil.copy2(db_source, database_path)
            instance_root = _instance_root()
            for relative_root in ("uploads/incidents", "uploads/conscientizacoes"):
                source_dir = restore_root / relative_root
                target_dir = (instance_root / relative_root).resolve()
                if instance_root not in target_dir.parents:
                    raise BackupIntegrityError("Diretório de restauração inválido.")
                if target_dir.exists():
                    shutil.rmtree(target_dir)
                if source_dir.exists():
                    shutil.copytree(source_dir, target_dir)
                else:
                    target_dir.mkdir(parents=True, exist_ok=True)
        except (BackupError, OSError, shutil.Error):
            shutil.copy2(database_backup, database_path)
            raise
        finally:
            try:
                database_backup.unlink()
            except OSError:
                pass

    registrar_auditoria(
        acao="RESTAURACAO_CONCLUIDA",
        modulo=BACKUP_MODULE,
        entidade="BackupRegistro",
        entidade_id=record.backup_uid,
        descricao=f"Sistema restaurado. Backup pré-restauração: {pre_restore.backup_uid}.",
        usuario=user,
    )
    return True


def delete_backup(record, *, user):
    if record.status == "EXCLUIDO":
        return
    if record.tipo == "COMPLETO" and BackupRegistro.query.filter_by(base_backup_uid=record.backup_uid, status="CONCLUIDO").filter(
        BackupRegistro.backup_uid != record.backup_uid
    ).count():
        raise BackupConfigError("Não é possível excluir backup completo referenciado por incrementais.")
    valid_count = BackupRegistro.query.filter_by(status="CONCLUIDO", integridade_status="VALIDO").count()
    if valid_count <= 1 and record.status == "CONCLUIDO" and record.integridade_status == "VALIDO":
        raise BackupConfigError("Não é possível excluir o único backup válido existente.")
    path = Path(record.arquivo_caminho)
    if path.exists() and path.is_file():
        path.unlink()
    manifest_path = Path(record.manifesto_caminho) if record.manifesto_caminho else None
    if manifest_path and manifest_path.exists() and manifest_path.is_file():
        manifest_path.unlink()
    record.status = "EXCLUIDO"
    record.integridade_status = "NAO_VALIDADO"
    db.session.commit()
    registrar_auditoria(
        acao=AuditAction.EXCLUIR,
        modulo=BACKUP_MODULE,
        entidade="BackupRegistro",
        entidade_id=record.backup_uid,
        descricao="Backup excluído.",
        usuario=user,
    )


def apply_retention_policy(*, config=None):
    config = config or get_or_create_config()
    cutoff = utc_now() - timedelta(days=config.retencao_dias)
    expired_incrementals = BackupRegistro.query.filter(
        BackupRegistro.tipo == "INCREMENTAL",
        BackupRegistro.status == "CONCLUIDO",
        BackupRegistro.iniciado_em < cutoff,
    ).all()
    for record in expired_incrementals:
        try:
            delete_backup(record, user=None)
        except BackupError:
            continue
    full_backups = (
        BackupRegistro.query.filter_by(tipo="COMPLETO", status="CONCLUIDO", integridade_status="VALIDO")
        .order_by(BackupRegistro.iniciado_em.desc())
        .all()
    )
    for record in full_backups[config.min_backups_completos :]:
        if BackupRegistro.query.filter_by(base_backup_uid=record.backup_uid, status="CONCLUIDO").filter(
            BackupRegistro.backup_uid != record.backup_uid
        ).count():
            continue
        try:
            delete_backup(record, user=None)
        except BackupError:
            continue


def backup_status_summary():
    config = get_or_create_config()
    try:
        root = validate_backup_directory(config.diretorio, create=False)
        disk = shutil.disk_usage(root)
        directory_error = None
    except (BackupError, OSError) as exc:
        disk = None
        directory_error = str(exc)
    total_size = db.session.query(db.func.coalesce(db.func.sum(BackupRegistro.tamanho_bytes), 0)).filter(
        BackupRegistro.status != "EXCLUIDO"
    ).scalar()
    return {
        "config": config,
        "directory_error": directory_error,
        "free_bytes": disk.free if disk else None,
        "total_backups": BackupRegistro.query.filter(BackupRegistro.status != "EXCLUIDO").count(),
        "total_size": total_size or 0,
        "last_full": _last_valid_full(),
        "last_incremental": BackupRegistro.query.filter_by(
            tipo="INCREMENTAL", status="CONCLUIDO", integridade_status="VALIDO"
        ).order_by(BackupRegistro.iniciado_em.desc()).first(),
        "running": BackupRegistro.query.filter_by(status="EM_ANDAMENTO").count() > 0,
        "security_ready": bool(current_app.config.get("DIVCIBER_BACKUP_HMAC_KEY"))
        and bool(current_app.config.get("DIVCIBER_BACKUP_ENCRYPTION_KEY")),
    }


def run_due_backup():
    config = get_or_create_config()
    if not config.habilitado or not config.proxima_execucao:
        return None
    if _as_aware_utc(config.proxima_execucao) > utc_now():
        return None
    return create_backup(manual=False)


def start_backup_scheduler(app):
    global _scheduler_started
    if app.testing or not app.config.get("DIVCIBER_BACKUP_SCHEDULER_ENABLED", True):
        return
    with _scheduler_lock:
        if _scheduler_started:
            return
        _scheduler_started = True

    def _loop():
        with app.app_context():
            while True:
                try:
                    run_due_backup()
                except (BackupError, OSError, sqlite3.Error, ValueError, zipfile.BadZipFile, InvalidTag) as exc:
                    app.logger.warning("Falha no agendador de backup DivCiber: %s", _sanitize_error(exc))
                time.sleep(60)

    thread = threading.Thread(target=_loop, name="divciber-backup-scheduler", daemon=True)
    thread.start()
