import re
import tempfile
import unicodedata
from dataclasses import dataclass, field
from datetime import datetime, time
from pathlib import Path

import pandas as pd
from flask import current_app
from sqlalchemy import and_, or_
from werkzeug.utils import secure_filename

from app import db
from app.models import CredencialComprometida
from app.services.timezone_service import APP_TIMEZONE, utc_now


ALLOWED_EXTENSIONS = {".xlsx", ".xls"}
MAX_SPREADSHEET_SIZE = 10 * 1024 * 1024
MAX_SEARCH_LENGTH = 80
ACCESS_FILTERS = {"", "somente_ad", "somente_ms", "ad_ms", "nenhum", "alguma_aplicacao"}
EMAIL_NOT_FOUND = "e-mail não localizado"
ORDER_FIELDS = {
    "data_coleta": CredencialComprometida.data_coleta,
    "nome": CredencialComprometida.nome_busca,
    "cpf": CredencialComprometida.cpf,
    "id": CredencialComprometida.id,
}
ORDER_DIRECTIONS = {"asc", "desc"}

REQUIRED_COLUMNS = {
    "nome": "NOME",
    "cpf": "CPF",
    "email": "EMAIL",
    "data_coleta": "DATA COLETA",
    "acesso_ad": "ACESSO AD",
    "acesso_ms": "ACESSO MS",
    "situacao_legal": "Situação legal",
    "mensagem_bloqueio": "MSG BLOQUEIO.",
}

OPTIONAL_COLUMNS = {
    "url": "URL",
    "permitiu_acesso": "Permitiu acesso a alguma aplicação?",
    "observacoes": "OBSERVAÇÕES",
}

COLUMN_ALIASES = {
    "nome": "nome",
    "cpf": "cpf",
    "email": "email",
    "url": "url",
    "data coleta": "data_coleta",
    "permitiu acesso a alguma aplicacao": "permitiu_acesso",
    "acesso ad": "acesso_ad",
    "acesso ms": "acesso_ms",
    "situacao legal": "situacao_legal",
    "observacoes": "observacoes",
    "msg bloqueio": "mensagem_bloqueio",
    "senha": "senha",
}

TRUE_VALUES = {"sim", "s", "true", "1", "positivo", "positiva", "yes", "y"}
FALSE_VALUES = {"nao", "não", "n", "false", "0", "negativo", "negativa", "no"}


@dataclass
class ImportSummary:
    total_rows: int = 0
    imported: int = 0
    updated: int = 0
    rejected: int = 0
    ignored_password_column: bool = False
    errors: list[dict] = field(default_factory=list)


def normalize_text(value, *, max_length=None, preserve_newlines=False):
    if value is None or pd.isna(value):
        return None
    text = str(value)
    text = text.encode("utf-8", errors="ignore").decode("utf-8", errors="ignore")
    text = unicodedata.normalize("NFC", text)
    text = "".join(ch for ch in text if ch in "\r\n\t" or not unicodedata.category(ch).startswith("C"))
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    if preserve_newlines:
        text = "\n".join(re.sub(r"[ \t]+", " ", line).strip() for line in text.split("\n"))
        text = re.sub(r"\n{3,}", "\n\n", text)
    else:
        text = re.sub(r"\s+", " ", text).strip()
    if text[:1] in {"=", "+", "-", "@"}:
        text = "'" + text
    if max_length and len(text) > max_length:
        text = text[:max_length].rstrip()
    return text or None


def normalize_key(value):
    text = normalize_text(value) or ""
    text = unicodedata.normalize("NFD", text)
    text = "".join(ch for ch in text if unicodedata.category(ch) != "Mn")
    text = re.sub(r"[^a-zA-Z0-9]+", " ", text)
    return re.sub(r"\s+", " ", text).strip().casefold()


def normalize_column_name(value):
    return COLUMN_ALIASES.get(normalize_key(value), normalize_key(value).replace(" ", "_"))


def normalize_cpf(value):
    return re.sub(r"\D", "", str(value or ""))


def is_valid_cpf(cpf):
    if not re.fullmatch(r"\d{11}", cpf):
        return False
    if cpf == cpf[0] * 11:
        return False
    total = sum(int(cpf[i]) * (10 - i) for i in range(9))
    digit = (total * 10) % 11
    if digit == 10:
        digit = 0
    if digit != int(cpf[9]):
        return False
    total = sum(int(cpf[i]) * (11 - i) for i in range(10))
    digit = (total * 10) % 11
    if digit == 10:
        digit = 0
    return digit == int(cpf[10])


def format_cpf(cpf):
    digits = normalize_cpf(cpf)
    if len(digits) != 11:
        return digits
    return f"{digits[:3]}.{digits[3:6]}.{digits[6:9]}-{digits[9:]}"


def mask_cpf(cpf):
    digits = normalize_cpf(cpf)
    return f"***.***.***-{digits[-2:]}" if len(digits) >= 2 else "***.***.***-**"


def normalize_email(value):
    email = (normalize_text(value, max_length=255) or "").strip().lower()
    return email


def is_valid_email(email):
    return bool(re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", email or ""))


def normalize_bool(value):
    key = normalize_key(value)
    if not key:
        return False
    if key in TRUE_VALUES:
        return True
    if key in FALSE_VALUES:
        return False
    return False


def parse_collection_date(value):
    if value is None or pd.isna(value):
        return None
    raw_value = str(value).strip()
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}", raw_value):
        parsed = pd.to_datetime(raw_value, errors="coerce", format="%Y-%m-%d")
    elif re.fullmatch(r"\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}", raw_value):
        parsed = pd.to_datetime(raw_value, errors="coerce", format="%Y-%m-%d %H:%M:%S")
    else:
        parsed = pd.to_datetime(raw_value, errors="coerce", dayfirst=True)
    if pd.isna(parsed):
        return None
    dt = parsed.to_pydatetime()
    if dt.tzinfo is None:
        dt = datetime.combine(dt.date(), dt.time() if dt.time() != time.min else time.min, tzinfo=APP_TIMEZONE)
    else:
        dt = dt.astimezone(APP_TIMEZONE)
    return dt


def _read_spreadsheet(path):
    suffix = path.suffix.lower()
    engine = "openpyxl" if suffix == ".xlsx" else "xlrd"
    return pd.read_excel(path, dtype=str, engine=engine)


def validate_spreadsheet_file(storage):
    filename = secure_filename(storage.filename or "")
    suffix = Path(filename).suffix.lower()
    if suffix not in ALLOWED_EXTENSIONS:
        raise ValueError("Envie uma planilha Excel no formato .xlsx ou .xls.")

    stream = storage.stream
    position = stream.tell()
    stream.seek(0, 2)
    size = stream.tell()
    stream.seek(position)
    if size <= 0:
        raise ValueError("A planilha enviada está vazia.")
    if size > MAX_SPREADSHEET_SIZE:
        raise ValueError("A planilha excede o tamanho máximo permitido.")
    return suffix


def _row_value(row, key):
    value = row.get(key)
    return None if value is None or pd.isna(value) else value


def _build_record(row):
    cpf = normalize_cpf(_row_value(row, "cpf"))
    email = normalize_email(_row_value(row, "email"))
    if not email:
        email = EMAIL_NOT_FOUND
    nome = normalize_text(_row_value(row, "nome"), max_length=255)
    data_coleta = parse_collection_date(_row_value(row, "data_coleta"))
    acesso_ad = normalize_bool(_row_value(row, "acesso_ad"))
    acesso_ms = normalize_bool(_row_value(row, "acesso_ms"))
    permitiu_acesso = normalize_bool(_row_value(row, "permitiu_acesso")) or acesso_ad or acesso_ms
    situacao_legal = normalize_text(_row_value(row, "situacao_legal"), max_length=150)

    errors = []
    if not nome:
        errors.append("nome ausente")
    if not is_valid_cpf(cpf):
        errors.append("CPF inválido")
    if email != EMAIL_NOT_FOUND and not is_valid_email(email):
        errors.append("e-mail inválido")
    if not data_coleta:
        errors.append("data de coleta inválida")
    if not situacao_legal:
        errors.append("situação legal ausente")

    return {
        "nome": nome,
        "nome_busca": normalize_key(nome),
        "cpf": cpf,
        "email": email,
        "url_origem": normalize_text(_row_value(row, "url"), max_length=2000),
        "data_coleta": data_coleta,
        "permitiu_acesso": permitiu_acesso,
        "acesso_ad": acesso_ad,
        "acesso_ms": acesso_ms,
        "situacao_legal": situacao_legal,
        "situacao_legal_normalizada": normalize_key(situacao_legal) if situacao_legal else None,
        "observacoes": normalize_text(_row_value(row, "observacoes"), max_length=4000, preserve_newlines=True),
        "mensagem_bloqueio": normalize_text(_row_value(row, "mensagem_bloqueio"), max_length=1000, preserve_newlines=True),
    }, errors


def _find_existing(record):
    return CredencialComprometida.query.filter(
        CredencialComprometida.cpf == record["cpf"],
        CredencialComprometida.email == record["email"],
        CredencialComprometida.url_origem == record["url_origem"],
        CredencialComprometida.data_coleta == record["data_coleta"],
    ).first()


def _merge_record(existing, record, user_id):
    changed = False
    for field, value in record.items():
        if value in (None, "") and getattr(existing, field) not in (None, ""):
            continue
        if getattr(existing, field) != value:
            setattr(existing, field, value)
            changed = True
    if changed:
        existing.imported_at = utc_now()
        existing.imported_by_id = user_id
    return changed


def import_credential_spreadsheet(storage, user_id=None):
    suffix = validate_spreadsheet_file(storage)
    summary = ImportSummary()
    temp_path = None
    temp_dir = Path(current_app.instance_path) / "tmp"
    temp_dir.mkdir(parents=True, exist_ok=True)

    try:
        with tempfile.NamedTemporaryFile(delete=False, suffix=suffix, dir=temp_dir) as temp_file:
            temp_path = Path(temp_file.name)
            storage.stream.seek(0)
            while True:
                chunk = storage.stream.read(1024 * 1024)
                if not chunk:
                    break
                temp_file.write(chunk)

        df = _read_spreadsheet(temp_path)
        df.columns = [normalize_column_name(column) for column in df.columns]
        if "senha" in df.columns:
            df = df.drop(columns=["senha"])
            summary.ignored_password_column = True

        missing = [label for key, label in REQUIRED_COLUMNS.items() if key not in df.columns]
        if missing:
            raise ValueError(f"Colunas obrigatórias ausentes: {', '.join(missing)}.")

        for key in OPTIONAL_COLUMNS:
            if key not in df.columns:
                df[key] = None

        summary.total_rows = int(len(df.index))
        for index, row in df.iterrows():
            line_number = int(index) + 2
            record, errors = _build_record(row)
            if errors:
                summary.rejected += 1
                summary.errors.append({"linha": line_number, "motivo": "; ".join(errors)})
                continue

            existing = _find_existing(record)
            if existing:
                if _merge_record(existing, record, user_id):
                    summary.updated += 1
                continue

            db.session.add(CredencialComprometida(**record, imported_at=utc_now(), imported_by_id=user_id))
            summary.imported += 1

        return summary
    finally:
        if temp_path and temp_path.exists():
            try:
                temp_path.unlink()
            except OSError:
                current_app.logger.warning("Não foi possível remover arquivo temporário de credenciais.")


def apply_credential_filters(query, args):
    search = (args.get("q") or "").strip()[:MAX_SEARCH_LENGTH]
    start_date = parse_collection_date(args.get("start_date"))
    end_date = parse_collection_date(args.get("end_date"))
    access_filter = args.get("access", "")
    situation = (args.get("situacao") or "").strip()

    if args.get("start_date") and not start_date:
        raise ValueError("Data inicial inválida.")
    if args.get("end_date") and not end_date:
        raise ValueError("Data final inválida.")
    if start_date and end_date and start_date.date() > end_date.date():
        raise ValueError("A data inicial não pode ser posterior à data final.")
    if access_filter not in ACCESS_FILTERS:
        raise ValueError("Filtro de acesso inválido.")

    if search:
        cpf_search = normalize_cpf(search)
        name_search = normalize_key(search)
        conditions = []
        if cpf_search:
            conditions.append(CredencialComprometida.cpf.like(f"%{cpf_search}%"))
        if name_search:
            conditions.append(CredencialComprometida.nome_busca.like(f"%{name_search}%"))
        if conditions:
            query = query.filter(or_(*conditions))

    if start_date:
        query = query.filter(CredencialComprometida.data_coleta >= datetime.combine(start_date.date(), time.min, tzinfo=APP_TIMEZONE))
    if end_date:
        query = query.filter(CredencialComprometida.data_coleta <= datetime.combine(end_date.date(), time.max, tzinfo=APP_TIMEZONE))

    if access_filter == "somente_ad":
        query = query.filter(and_(CredencialComprometida.acesso_ad.is_(True), CredencialComprometida.acesso_ms.is_(False)))
    elif access_filter == "somente_ms":
        query = query.filter(and_(CredencialComprometida.acesso_ad.is_(False), CredencialComprometida.acesso_ms.is_(True)))
    elif access_filter == "ad_ms":
        query = query.filter(and_(CredencialComprometida.acesso_ad.is_(True), CredencialComprometida.acesso_ms.is_(True)))
    elif access_filter == "nenhum":
        query = query.filter(and_(CredencialComprometida.acesso_ad.is_(False), CredencialComprometida.acesso_ms.is_(False)))
    elif access_filter == "alguma_aplicacao":
        query = query.filter(or_(
            CredencialComprometida.acesso_ad.is_(True),
            CredencialComprometida.acesso_ms.is_(True),
            CredencialComprometida.permitiu_acesso.is_(True),
        ))

    if situation:
        query = query.filter(CredencialComprometida.situacao_legal_normalizada == normalize_key(situation))

    return query


def order_credentials(query, args):
    field = args.get("sort", "data_coleta")
    direction = args.get("direction", "desc")
    if field not in ORDER_FIELDS:
        field = "data_coleta"
    if direction not in ORDER_DIRECTIONS:
        direction = "desc"
    column = ORDER_FIELDS[field]
    ordered = column.asc() if direction == "asc" else column.desc()
    return query.order_by(ordered, CredencialComprometida.id.desc()), field, direction


def credential_to_table_dict(item):
    return {
        "id": item.id,
        "cpf": format_cpf(item.cpf),
        "nome": item.nome,
        "email": item.email,
        "mensagem_bloqueio": item.mensagem_bloqueio or "",
        "situacao_legal": item.situacao_legal or "",
    }
