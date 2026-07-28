import base64
import html
import re
import threading
import time
from dataclasses import dataclass
from html.parser import HTMLParser
from typing import Any

import requests
from flask import current_app

from app.services.internal_api import (
    InternalAPIClient,
    InternalAPIConfigurationError,
    PM_EMAIL_ENDPOINTS,
    PM_ENDPOINTS,
    SERVICE_PM_CDPM,
    SERVICE_PM_EMAIL,
    build_endpoint,
)


VALID_QUERY_RE = re.compile(r"^\d{6}$|^\d{11}$")
VALID_NAME_RE = re.compile(r"^[A-Za-zÀ-ÿ][A-Za-zÀ-ÿ\s'.-]{2,79}$")
_CACHE_LOCK = threading.Lock()
_CACHE: dict[str, tuple[float, dict[str, Any]]] = {}


class BuscarPMError(Exception):
    message = "Não foi possível realizar a consulta."
    audit_result = "FALHA"


class BuscarPMValidationError(BuscarPMError):
    message = "CPF, RE ou nome inválido."
    audit_result = "VALIDACAO"


class BuscarPMNotFoundError(BuscarPMError):
    message = "Policial militar não encontrado."
    audit_result = "NAO_ENCONTRADO"


class BuscarPMUnavailableError(BuscarPMError):
    message = "Serviço de consulta indisponível no momento."
    audit_result = "INDISPONIVEL"


class BuscarPMCertificateError(BuscarPMError):
    message = (
        "Não foi possível validar o certificado da API interna. "
        "Configure o certificado confiável em PM_API_CA_BUNDLE."
    )
    audit_result = "ERRO_CERTIFICADO"


class BuscarPMConnectionError(BuscarPMError):
    message = "Não foi possível conectar ao serviço de consulta. Verifique a rede interna."
    audit_result = "ERRO_CONEXAO"


class BuscarPMAuthError(BuscarPMError):
    message = "Não foi possível autenticar na API de consulta."
    audit_result = "ERRO_AUTENTICACAO"


class BuscarPMTimeoutError(BuscarPMError):
    message = "Tempo limite excedido ao consultar a API."
    audit_result = "TIMEOUT"


class BuscarPMInvalidResponseError(BuscarPMError):
    message = "A API retornou uma resposta inválida."
    audit_result = "RESPOSTA_INVALIDA"


@dataclass(frozen=True)
class BuscarPMQuery:
    value: str
    kind: str


def normalize_query(value):
    raw_value = str(value or "").strip()
    digits = re.sub(r"\D", "", raw_value)
    digits = digits[:11]
    if not digits or re.sub(r"\d", "", raw_value).strip(" .-()/"):
        raise BuscarPMValidationError()
    if not VALID_QUERY_RE.fullmatch(digits):
        raise BuscarPMValidationError()
    return BuscarPMQuery(value=digits, kind="CPF" if len(digits) == 11 else "RE")


def normalize_name_query(value):
    raw_value = str(value or "").strip()
    name = re.sub(r"\s+", " ", raw_value)
    if any(char.isdigit() for char in name) or not VALID_NAME_RE.fullmatch(name):
        raise BuscarPMValidationError()
    return BuscarPMQuery(value=name[:80], kind="NOME")


def mask_query(value):
    digits = re.sub(r"\D", "", str(value or ""))
    if digits:
        return f"***{digits[-4:]}"
    text = re.sub(r"\s+", " ", str(value or "")).strip()
    return f"{text[:3].upper()}***" if text else "***"


class PMEmailSearchParser(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.hidden_fields: dict[str, str] = {}
        self.in_grid = False
        self.in_row = False
        self.in_cell = False
        self.current_cell = ""
        self.current_row: list[str] = []
        self.rows: list[dict[str, str]] = []
        self.current_tooltip = ""

    def handle_starttag(self, tag, attrs):
        attrs_dict = dict(attrs)
        if tag == "input":
            name = attrs_dict.get("name")
            input_type = (attrs_dict.get("type") or "").lower()
            if name and input_type == "hidden":
                self.hidden_fields[name] = attrs_dict.get("value", "")
        if tag == "table" and attrs_dict.get("id") == "gridEmail":
            self.in_grid = True
        elif self.in_grid and tag == "tr":
            self.in_row = True
            self.current_row = []
            self.current_tooltip = ""
        elif self.in_grid and self.in_row and tag in {"td", "th"}:
            self.in_cell = True
            self.current_cell = ""
        elif self.in_grid and self.in_row and tag == "a":
            title = attrs_dict.get("title")
            if title:
                self.current_tooltip = title

    def handle_endtag(self, tag):
        if self.in_grid and tag == "table":
            self.in_grid = False
        elif self.in_grid and self.in_row and tag == "tr":
            if len(self.current_row) >= 5 and self.current_row[0].lower() != "posto":
                details = _parse_tooltip_details(self.current_tooltip)
                self.rows.append(
                    {
                        "posto": _text(self.current_row[0]),
                        "re": _text(self.current_row[1]),
                        "nome": _text(self.current_row[2]),
                        "email": _text(self.current_row[3]),
                        "status": _text(self.current_row[4]),
                        "codigo_opm": _text(details.get("codigo_opm")),
                        "opm": _text(details.get("opm")),
                    }
                )
            self.in_row = False
        elif self.in_grid and self.in_cell and tag in {"td", "th"}:
            self.current_row.append(_text(self.current_cell, default=""))
            self.in_cell = False

    def handle_data(self, data):
        if self.in_grid and self.in_cell:
            self.current_cell += data


def _parse_tooltip_details(raw_title):
    if not raw_title:
        return {}
    text = html.unescape(re.sub(r"<[^>]+>", " ", raw_title))
    text = re.sub(r"\s+", " ", text).strip()
    result = {}
    opm_code_match = re.search(r"Cod\.?\s*OPM:\s*([^ ]+)", text, flags=re.IGNORECASE)
    if opm_code_match:
        result["codigo_opm"] = opm_code_match.group(1).strip()
    opm_match = re.search(r"OPM:\s*(.*?)(?:\s+Status:|$)", text, flags=re.IGNORECASE)
    if opm_match:
        result["opm"] = opm_match.group(1).strip()
    return result


def _parse_email_search_html(page_html):
    parser = PMEmailSearchParser()
    parser.feed(page_html or "")
    return parser


def _cache_ttl():
    return int(current_app.config.get("PM_SEARCH_CACHE_TTL_SECONDS", 300))


def _cache_get(cpf):
    now = time.monotonic()
    with _CACHE_LOCK:
        cached = _CACHE.get(cpf)
        if not cached:
            return None
        expires_at, payload = cached
        if expires_at <= now:
            _CACHE.pop(cpf, None)
            return None
        return payload


def _cache_set(cpf, payload):
    ttl = _cache_ttl()
    if ttl <= 0:
        return
    with _CACHE_LOCK:
        _CACHE[cpf] = (time.monotonic() + ttl, payload)


def clear_pm_cache():
    with _CACHE_LOCK:
        _CACHE.clear()


def _first_dados(payload):
    dados = payload.get("dados") if isinstance(payload, dict) else None
    if not isinstance(dados, list) or not dados:
        raise BuscarPMNotFoundError()
    return dados[0] or {}


def _first_optional_dados(payload):
    dados = payload.get("dados") if isinstance(payload, dict) else None
    if not isinstance(dados, list) or not dados:
        return {}
    return dados[0] or {}


def _safe_get(data, *path, default=None):
    current = data
    for key in path:
        if isinstance(current, dict):
            current = current.get(key)
        elif isinstance(current, list) and isinstance(key, int):
            current = current[key] if len(current) > key else None
        else:
            return default
        if current is None:
            return default
    return current if current not in ("", None) else default


def _text(value, default="Não disponível"):
    if value is None:
        return default
    text = re.sub(r"\s+", " ", str(value)).strip()
    return text or default


def _request_json(client, path):
    try:
        response = client.get_json(path)
    except InternalAPIConfigurationError as exc:
        current_app.logger.warning("Falha na consulta PM: configuração ausente.")
        raise BuscarPMUnavailableError() from exc
    except requests.Timeout as exc:
        current_app.logger.warning("Falha na consulta PM: timeout.")
        raise BuscarPMTimeoutError() from exc
    except requests.exceptions.SSLError as exc:
        current_app.logger.warning("Falha na consulta PM: certificado TLS inválido.")
        raise BuscarPMCertificateError() from exc
    except requests.exceptions.ConnectionError as exc:
        current_app.logger.warning("Falha na consulta PM: erro de conexão.")
        raise BuscarPMConnectionError() from exc
    except requests.RequestException as exc:
        current_app.logger.warning("Falha na consulta PM: %s.", exc.__class__.__name__)
        raise BuscarPMUnavailableError() from exc

    if response.status_code in {401, 403}:
        raise BuscarPMAuthError()
    if response.status_code == 404:
        raise BuscarPMNotFoundError()
    if response.status_code >= 500:
        raise BuscarPMUnavailableError()
    if response.status_code != 200:
        raise BuscarPMNotFoundError()

    try:
        return response.json()
    except ValueError as exc:
        raise BuscarPMInvalidResponseError() from exc


def _request_optional_json(client, path):
    try:
        return _request_json(client, path)
    except BuscarPMNotFoundError:
        return {"dados": []}


def _request_html(client, method, path, data=None):
    try:
        response = client.get(path) if method == "GET" else client.post_form(path, data or {})
    except InternalAPIConfigurationError as exc:
        current_app.logger.warning("Falha na consulta PM por nome: configuração ausente.")
        raise BuscarPMUnavailableError() from exc
    except requests.Timeout as exc:
        current_app.logger.warning("Falha na consulta PM por nome: timeout.")
        raise BuscarPMTimeoutError() from exc
    except requests.exceptions.SSLError as exc:
        current_app.logger.warning("Falha na consulta PM por nome: certificado TLS inválido.")
        raise BuscarPMCertificateError() from exc
    except requests.exceptions.ConnectionError as exc:
        current_app.logger.warning("Falha na consulta PM por nome: erro de conexão.")
        raise BuscarPMConnectionError() from exc
    except requests.RequestException as exc:
        current_app.logger.warning("Falha na consulta PM por nome: %s.", exc.__class__.__name__)
        raise BuscarPMUnavailableError() from exc

    if response.status_code in {401, 403}:
        raise BuscarPMAuthError()
    if response.status_code >= 500:
        raise BuscarPMUnavailableError()
    if response.status_code not in {200, 302}:
        raise BuscarPMNotFoundError()
    return response.text or ""


def buscar_pm_por_nome(query_value):
    query = normalize_name_query(query_value)
    name_value = query.value
    cache_key = f"nome:{name_value.casefold()}"
    cached = _cache_get(cache_key)
    if cached:
        result = dict(cached)
        result["cache_hit"] = True
        return result

    endpoint = PM_EMAIL_ENDPOINTS["consulta_email"]
    try:
        client_context = InternalAPIClient(SERVICE_PM_EMAIL)
    except InternalAPIConfigurationError as exc:
        current_app.logger.warning("Falha na consulta PM por nome: configuração ausente.")
        raise BuscarPMUnavailableError() from exc

    with client_context as client:
        initial_parser = _parse_email_search_html(_request_html(client, "GET", endpoint))
        form_data = {
            **initial_parser.hidden_fields,
            "ddlUnidade": "0",
            "txtRE": "",
            "txtCPF": "",
            "txtNome": name_value,
            "txtEmail": "",
            "ddlStatus": "0",
            "btnBuscar": "Pesquisar",
        }
        parser = _parse_email_search_html(_request_html(client, "POST", endpoint, form_data))

    if not parser.rows:
        raise BuscarPMNotFoundError()

    result = {
        "mode": "nome",
        "query_kind": "NOME",
        "resultados_nome": parser.rows,
        "total_results": len(parser.rows),
        "cache_hit": False,
    }
    _cache_set(cache_key, result)
    return result


def _re_from_payload(data):
    numero = _safe_get(data, "re", "numero")
    digito = _safe_get(data, "re", "digito")
    if numero and digito:
        return f"{numero}-{digito}"
    return _text(numero)


def _opm_from_payload(data):
    parts = [
        _safe_get(data, "opm", "opmN02Des"),
        _safe_get(data, "opm", "opmN03Des"),
        _safe_get(data, "opm", "opmN04Des"),
        _safe_get(data, "opm", "apelido"),
    ]
    valid_parts = [_text(part, default="") for part in parts if _text(part, default="")]
    return " - ".join(valid_parts) or "Não disponível"


def _dados_policial(data):
    return {
        "nome": _text(_safe_get(data, "nomeCompleto")),
        "nome_guerra": _text(_safe_get(data, "nomeGuerra")),
        "posto": _text(_safe_get(data, "posto", "sigla")),
        "re": _re_from_payload(data),
        "cpf": _text(_safe_get(data, "cpf", "cpfComDigito")),
        "situacao_legal": _text(_safe_get(data, "situacaoLegal", "descricao")),
        "data_nascimento": _text(_safe_get(data, "dataNascimento")),
        "opm": _opm_from_payload(data),
        "codigo_opm": _text(_safe_get(data, "opm", "codigo")),
    }


def _contato(data):
    emails = []
    seen_emails = set()
    for item in data.get("emails", []) if isinstance(data, dict) else []:
        if not isinstance(item, dict):
            continue
        email = _text(item.get("endereco"), default="")
        email_key = email.casefold()
        if email and email_key not in seen_emails:
            emails.append(email)
            seen_emails.add(email_key)
    return {
        "email": _text("; ".join(emails)),
        "emails": emails,
        "telefone": _text(
            "-".join(
                part
                for part in [
                    str(_safe_get(data, "telefones", 0, "ddd", default="") or ""),
                    str(_safe_get(data, "telefones", 0, "numero", default="") or ""),
                ]
                if part
            )
        ),
    }


def _documentos(data):
    rg_numero = _safe_get(data, "rg", "numero")
    rg_digito = _safe_get(data, "rg", "digito")
    rg_uf = _safe_get(data, "rg", "uf")
    rg = "Não disponível"
    if rg_numero:
        rg = str(rg_numero)
        if rg_digito:
            rg += f"-{rg_digito}"
        if rg_uf:
            rg += f"/{rg_uf}"
    return {
        "rg": rg,
        "cnh": _text(_safe_get(data, "cnh", "numero")),
        "categoria": _text(_safe_get(data, "cnh", "categoria")),
        "validade": _text(_safe_get(data, "cnh", "dataExpiracao")),
    }


def _caracteristicas(data):
    cabelo = " ".join(
        part
        for part in [
            _text(_safe_get(data, "cabelo", "cor"), default=""),
            _text(_safe_get(data, "cabelo", "tipo"), default=""),
        ]
        if part
    )
    tipo_sanguineo = "".join(
        part
        for part in [
            _text(_safe_get(data, "tipoSanguineo", "tipo"), default=""),
            _text(_safe_get(data, "tipoSanguineo", "fator"), default=""),
        ]
        if part
    )
    return {
        "estatura": _text(_safe_get(data, "estatura")),
        "cabelo": cabelo or "Não disponível",
        "olhos": _text(_safe_get(data, "olhos", "descricao")),
        "cutis": _text(_safe_get(data, "cutis", "descricaoCutis")),
        "tipo_sanguineo": tipo_sanguineo or "Não disponível",
    }


def _foto(data):
    image_value = _safe_get(data, "imagem")
    if not image_value:
        return None
    try:
        base64.b64decode(str(image_value), validate=True)
    except (ValueError, TypeError):
        current_app.logger.warning("Foto de PM ignorada por Base64 inválido.")
        return None
    return f"data:image/jpeg;base64,{image_value}"


def _cpf_from_re(client, re_value):
    payload = _request_json(client, build_endpoint(PM_ENDPOINTS["dados_por_re"], re=re_value))
    data = _first_dados(payload)
    cpf = _safe_get(data, "cpf", "cpfComDigito")
    if not cpf:
        raise BuscarPMNotFoundError()
    return re.sub(r"\D", "", str(cpf))


def _buscar_pm_por_cpf_com_client(client, cpf, query_kind):
    cached = _cache_get(cpf)
    if cached:
        result = dict(cached)
        result["cache_hit"] = True
        result["query_kind"] = query_kind
        return result

    resumidos = _first_dados(_request_json(client, build_endpoint(PM_ENDPOINTS["dados_por_cpf"], cpf=cpf)))
    caracteristicas = _first_optional_dados(
        _request_optional_json(client, build_endpoint(PM_ENDPOINTS["caracteristica_fisica"], cpf=cpf))
    )
    documentos = _first_optional_dados(
        _request_optional_json(client, build_endpoint(PM_ENDPOINTS["documentos"], cpf=cpf))
    )
    contato = _first_optional_dados(
        _request_optional_json(client, build_endpoint(PM_ENDPOINTS["informacao_contato"], cpf=cpf))
    )
    foto_payload = _first_optional_dados(
        _request_optional_json(client, build_endpoint(PM_ENDPOINTS["pesquisa_foto"], cpf=cpf))
    )

    result = {
        "mode": "detalhe",
        "query_kind": query_kind,
        "cpf_cache_key": cpf,
        "dados": _dados_policial(resumidos),
        "contato": _contato(contato),
        "documentos": _documentos(documentos),
        "caracteristicas": _caracteristicas(caracteristicas),
        "foto_data_uri": _foto(foto_payload),
        "cache_hit": False,
    }
    _cache_set(cpf, result)
    return result


def _buscar_pm_por_re_com_client(client, re_value):
    cpf = _cpf_from_re(client, re_value)
    return _buscar_pm_por_cpf_com_client(client, cpf, "RE")


def buscar_pm(query_value):
    query = normalize_query(query_value)
    with InternalAPIClient(SERVICE_PM_CDPM) as client:
        if query.kind == "RE":
            return _buscar_pm_por_re_com_client(client, query.value)
        return _buscar_pm_por_cpf_com_client(client, query.value, query.kind)
