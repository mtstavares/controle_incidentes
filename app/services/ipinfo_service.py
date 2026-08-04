import ipaddress
from typing import Any

import requests
from flask import current_app

from app.services.internal_api import (
    IPINFO_ENDPOINTS,
    SERVICE_IPINFO,
    InternalAPIClient,
    InternalAPIConfigurationError,
    build_endpoint,
)


class IPInfoError(Exception):
    message = "Não foi possível consultar as informações públicas do IP."
    audit_result = "FALHA_IPINFO"


class IPInfoConfigurationError(IPInfoError):
    message = "Token do IPinfo não configurado."
    audit_result = "IPINFO_CONFIG_AUSENTE"


class IPInfoUnavailableError(IPInfoError):
    message = "Serviço IPinfo indisponível no momento."
    audit_result = "IPINFO_INDISPONIVEL"


class IPInfoAuthError(IPInfoError):
    message = "Não foi possível autenticar no IPinfo."
    audit_result = "IPINFO_AUTENTICACAO"


class IPInfoTimeoutError(IPInfoError):
    message = "Tempo limite excedido ao consultar o IPinfo."
    audit_result = "IPINFO_TIMEOUT"


class IPInfoInvalidResponseError(IPInfoError):
    message = "O IPinfo retornou uma resposta inválida."
    audit_result = "IPINFO_RESPOSTA_INVALIDA"


def _text(value: Any, default: str = "Não disponível") -> str:
    if value is None:
        return default
    text = str(value).strip()
    return text or default


def _bool_label(value: Any) -> str:
    if value is True:
        return "Sim"
    if value is False:
        return "Não"
    return "Não disponível"


def _dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _validate_lookup_ip(value: str) -> str:
    try:
        return str(ipaddress.ip_address(value))
    except ValueError as exc:
        raise IPInfoInvalidResponseError() from exc


def _request_lookup(ip_value: str) -> dict[str, Any]:
    with InternalAPIClient(SERVICE_IPINFO) as client:
        if not client.token:
            raise IPInfoConfigurationError()
        try:
            response = client.get_json(
                build_endpoint(IPINFO_ENDPOINTS["lookup"], ip=ip_value),
                params={"token": client.token},
            )
        except InternalAPIConfigurationError as exc:
            current_app.logger.warning("Falha na consulta IPinfo: configuração ausente.")
            raise IPInfoConfigurationError() from exc
        except requests.Timeout as exc:
            current_app.logger.warning("Falha na consulta IPinfo: timeout.")
            raise IPInfoTimeoutError() from exc
        except requests.RequestException as exc:
            current_app.logger.warning("Falha na consulta IPinfo: %s.", exc.__class__.__name__)
            raise IPInfoUnavailableError() from exc

    if response.status_code in {401, 403}:
        raise IPInfoAuthError()
    if response.status_code == 404:
        return {}
    if response.status_code >= 500:
        raise IPInfoUnavailableError()
    if response.status_code != 200:
        raise IPInfoUnavailableError()
    try:
        payload = response.json()
    except ValueError as exc:
        raise IPInfoInvalidResponseError() from exc
    if not isinstance(payload, dict):
        raise IPInfoInvalidResponseError()
    return payload


def consultar_ip_publico(ip_value: str) -> dict[str, Any]:
    normalized_ip = _validate_lookup_ip(ip_value)
    payload = _request_lookup(normalized_ip)
    if not payload:
        return {"found": False, "ip": normalized_ip}

    geo = _dict(payload.get("geo"))
    autonomous_system = _dict(payload.get("as"))
    anonymous = _dict(payload.get("anonymous"))
    return {
        "found": True,
        "ip": _text(payload.get("ip") or normalized_ip),
        "hostname": _text(payload.get("hostname")),
        "geo": {
            "city": _text(geo.get("city")),
            "region": _text(geo.get("region")),
            "region_code": _text(geo.get("region_code")),
            "country": _text(geo.get("country")),
            "country_code": _text(geo.get("country_code")),
            "continent": _text(geo.get("continent")),
            "timezone": _text(geo.get("timezone")),
        },
        "as": {
            "asn": _text(autonomous_system.get("asn")),
            "name": _text(autonomous_system.get("name")),
            "domain": _text(autonomous_system.get("domain")),
            "type": _text(autonomous_system.get("type")),
            "last_changed": _text(autonomous_system.get("last_changed")),
        },
        "anonymous": {
            "name": _text(anonymous.get("name")),
            "last_seen": _text(anonymous.get("last_seen")),
            "percent_days_seen": _text(anonymous.get("percent_days_seen")),
            "is_proxy": _bool_label(anonymous.get("is_proxy")),
            "is_relay": _bool_label(anonymous.get("is_relay")),
            "is_tor": _bool_label(anonymous.get("is_tor")),
            "is_vpn": _bool_label(anonymous.get("is_vpn")),
            "is_res_proxy": _bool_label(anonymous.get("is_res_proxy")),
        },
        "flags": {
            "is_anonymous": _bool_label(payload.get("is_anonymous")),
            "is_anycast": _bool_label(payload.get("is_anycast")),
            "is_hosting": _bool_label(payload.get("is_hosting")),
            "is_mobile": _bool_label(payload.get("is_mobile")),
            "is_satellite": _bool_label(payload.get("is_satellite")),
        },
    }
