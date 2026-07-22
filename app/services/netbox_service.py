import ipaddress
import threading
import time
from typing import Any

import requests
from flask import current_app

from app.services.internal_api import (
    InternalAPIClient,
    InternalAPIConfigurationError,
    NETBOX_ENDPOINTS,
    SERVICE_NETBOX,
)


_CACHE_LOCK = threading.Lock()
_CACHE: dict[str, tuple[float, dict[str, Any]]] = {}


class NetBoxError(Exception):
    message = "Não foi possível realizar a consulta."
    audit_result = "FALHA"


class NetBoxValidationError(NetBoxError):
    message = "IP inválido."
    audit_result = "VALIDACAO"


class NetBoxUnavailableError(NetBoxError):
    message = "Serviço NetBox indisponível no momento."
    audit_result = "INDISPONIVEL"


class NetBoxCertificateError(NetBoxError):
    message = (
        "Não foi possível validar o certificado do NetBox. "
        "Configure o certificado confiável em NETBOX_API_CA_BUNDLE."
    )
    audit_result = "ERRO_CERTIFICADO"


class NetBoxConnectionError(NetBoxError):
    message = "Não foi possível conectar ao NetBox. Verifique a rede interna."
    audit_result = "ERRO_CONEXAO"


class NetBoxAuthError(NetBoxError):
    message = "Não foi possível autenticar no NetBox."
    audit_result = "ERRO_AUTENTICACAO"


class NetBoxTimeoutError(NetBoxError):
    message = "Tempo limite excedido ao consultar o NetBox."
    audit_result = "TIMEOUT"


class NetBoxInvalidResponseError(NetBoxError):
    message = "O NetBox retornou uma resposta inválida."
    audit_result = "RESPOSTA_INVALIDA"


def normalize_ip(value):
    text = str(value or "").strip()
    if len(text) > 45:
        raise NetBoxValidationError()
    try:
        return str(ipaddress.ip_address(text))
    except ValueError as exc:
        raise NetBoxValidationError() from exc


def mask_ip(value):
    try:
        ip = ipaddress.ip_address(str(value))
    except ValueError:
        return "***"
    if ip.version == 4:
        parts = str(ip).split(".")
        return f"{parts[0]}.{parts[1]}.*.{parts[3]}"
    return f"{ip.exploded[:9]}…{ip.exploded[-4:]}"


def clear_netbox_cache():
    with _CACHE_LOCK:
        _CACHE.clear()


def _cache_ttl():
    return int(current_app.config.get("NETBOX_SEARCH_CACHE_TTL_SECONDS", 300))


def _cache_get(ip_value):
    now = time.monotonic()
    with _CACHE_LOCK:
        cached = _CACHE.get(ip_value)
        if not cached:
            return None
        expires_at, payload = cached
        if expires_at <= now:
            _CACHE.pop(ip_value, None)
            return None
        return payload


def _cache_set(ip_value, payload):
    ttl = _cache_ttl()
    if ttl <= 0:
        return
    with _CACHE_LOCK:
        _CACHE[ip_value] = (time.monotonic() + ttl, payload)


def _text(value, default="Não disponível"):
    if value is None:
        return default
    text = str(value).strip()
    return text or default


def _label(value, default="Não disponível"):
    if isinstance(value, dict):
        return _text(value.get("label") or value.get("name") or value.get("display") or value.get("value"), default)
    return _text(value, default)


def _results(payload):
    if isinstance(payload, dict) and isinstance(payload.get("results"), list):
        return payload["results"]
    if isinstance(payload, list):
        return payload
    return []


def _request_json(client, path, params):
    try:
        response = client.get_json(path, params=params)
    except InternalAPIConfigurationError as exc:
        current_app.logger.warning("Falha na consulta NetBox: configuração ausente.")
        raise NetBoxUnavailableError() from exc
    except requests.Timeout as exc:
        current_app.logger.warning("Falha na consulta NetBox: timeout.")
        raise NetBoxTimeoutError() from exc
    except requests.exceptions.SSLError as exc:
        current_app.logger.warning("Falha na consulta NetBox: certificado TLS inválido.")
        raise NetBoxCertificateError() from exc
    except requests.exceptions.ConnectionError as exc:
        current_app.logger.warning("Falha na consulta NetBox: erro de conexão.")
        raise NetBoxConnectionError() from exc
    except requests.RequestException as exc:
        current_app.logger.warning("Falha na consulta NetBox: %s.", exc.__class__.__name__)
        raise NetBoxUnavailableError() from exc

    if response.status_code in {401, 403}:
        raise NetBoxAuthError()
    if response.status_code >= 500:
        raise NetBoxUnavailableError()
    if response.status_code not in {200, 404}:
        raise NetBoxUnavailableError()
    if response.status_code == 404:
        return {"results": []}

    try:
        return response.json()
    except ValueError as exc:
        raise NetBoxInvalidResponseError() from exc


def _assigned_object(value):
    if not isinstance(value, dict):
        return "Não disponível"
    parts = [
        value.get("device", {}).get("display") if isinstance(value.get("device"), dict) else None,
        value.get("name") or value.get("display"),
    ]
    return " - ".join(_text(part, "") for part in parts if _text(part, "")) or "Não disponível"


def _normalize_ip_address(item):
    return {
        "address": _text(item.get("address") or item.get("display")),
        "status": _label(item.get("status")),
        "dns_name": _text(item.get("dns_name")),
        "description": _text(item.get("description")),
        "role": _label(item.get("role")),
        "tenant": _label(item.get("tenant")),
        "vrf": _label(item.get("vrf")),
        "assigned_object": _assigned_object(item.get("assigned_object")),
    }


def _normalize_prefix(item):
    return {
        "prefix": _text(item.get("prefix") or item.get("display")),
        "status": _label(item.get("status")),
        "description": _text(item.get("description")),
        "site": _label(item.get("site")),
        "vlan": _label(item.get("vlan")),
        "tenant": _label(item.get("tenant")),
        "vrf": _label(item.get("vrf")),
    }


def consultar_ip(ip_value):
    normalized_ip = normalize_ip(ip_value)
    cached = _cache_get(normalized_ip)
    if cached:
        result = dict(cached)
        result["cache_hit"] = True
        return result

    with InternalAPIClient(SERVICE_NETBOX) as client:
        ip_payload = _request_json(
            client,
            NETBOX_ENDPOINTS["ip_addresses"],
            {"q": normalized_ip, "limit": 10},
        )
        prefix_payload = _request_json(
            client,
            NETBOX_ENDPOINTS["prefixes"],
            {"contains": normalized_ip, "limit": 10},
        )

    ip_addresses = [_normalize_ip_address(item) for item in _results(ip_payload)]
    prefixes = [_normalize_prefix(item) for item in _results(prefix_payload)]
    result = {
        "query_ip": normalized_ip,
        "ip_addresses": ip_addresses,
        "prefixes": prefixes,
        "total_results": len(ip_addresses) + len(prefixes),
        "cache_hit": False,
    }
    _cache_set(normalized_ip, result)
    return result
