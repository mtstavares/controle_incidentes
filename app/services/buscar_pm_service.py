import base64
import re
import threading
import time
from dataclasses import dataclass
from typing import Any
from urllib.parse import quote

import requests
from flask import current_app


VALID_QUERY_RE = re.compile(r"^\d{6}$|^\d{11}$")
_CACHE_LOCK = threading.Lock()
_CACHE: dict[str, tuple[float, dict[str, Any]]] = {}


class BuscarPMError(Exception):
    message = "Não foi possível realizar a consulta."
    audit_result = "FALHA"


class BuscarPMValidationError(BuscarPMError):
    message = "CPF ou RE inválido."
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
    digits = re.sub(r"\D", "", str(value or ""))[:11]
    if not VALID_QUERY_RE.fullmatch(digits):
        raise BuscarPMValidationError()
    return BuscarPMQuery(value=digits, kind="CPF" if len(digits) == 11 else "RE")


def mask_query(value):
    digits = re.sub(r"\D", "", str(value or ""))
    return f"***{digits[-4:]}" if digits else "***"


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


def _base_url():
    return str(current_app.config["PM_API_BASE_URL"]).rstrip("/")


def _verify_config():
    if current_app.config.get("PM_API_VERIFY_TLS") is False:
        return False
    return current_app.config.get("PM_API_CA_BUNDLE") or True


def _timeout():
    return float(current_app.config.get("PM_API_TIMEOUT", 10))


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


def _request_json(session, path):
    url = f"{_base_url()}/{path.lstrip('/')}"
    try:
        response = session.get(url, timeout=_timeout(), verify=_verify_config())
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


def _request_optional_json(session, path):
    try:
        return _request_json(session, path)
    except BuscarPMNotFoundError:
        return {"dados": []}


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
    return {
        "email": _text(_safe_get(data, "emails", 0, "endereco")),
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


def _cpf_from_re(session, re_value):
    payload = _request_json(session, f"re/{quote(re_value)}/dadosResumidos")
    data = _first_dados(payload)
    cpf = _safe_get(data, "cpf", "cpfComDigito")
    if not cpf:
        raise BuscarPMNotFoundError()
    return re.sub(r"\D", "", str(cpf))


def buscar_pm(query_value):
    query = normalize_query(query_value)
    with requests.Session() as session:
        cpf = query.value if query.kind == "CPF" else _cpf_from_re(session, query.value)
        cached = _cache_get(cpf)
        if cached:
            result = dict(cached)
            result["cache_hit"] = True
            result["query_kind"] = query.kind
            return result

        resumidos = _first_dados(_request_json(session, f"cpf/{quote(cpf)}/dadosResumidos"))
        caracteristicas = _first_optional_dados(_request_optional_json(session, f"cpf/{quote(cpf)}/caracteristicaFisica"))
        documentos = _first_optional_dados(_request_optional_json(session, f"cpf/{quote(cpf)}/documentos"))
        contato = _first_optional_dados(_request_optional_json(session, f"cpf/{quote(cpf)}/informacaoContato"))
        foto_payload = _first_optional_dados(_request_optional_json(session, f"cpf/{quote(cpf)}/pesquisaFoto"))

    result = {
        "query_kind": query.kind,
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
