from urllib.parse import quote

import requests
from flask import current_app


SERVICE_PM_CDPM = "pm_cdpm"
SERVICE_NETBOX = "netbox"

PM_ENDPOINTS = {
    "dados_por_cpf": "cpf/{cpf}/dadosResumidos",
    "dados_por_re": "re/{re}/dadosResumidos",
    "caracteristica_fisica": "cpf/{cpf}/caracteristicaFisica",
    "documentos": "cpf/{cpf}/documentos",
    "informacao_contato": "cpf/{cpf}/informacaoContato",
    "pesquisa_foto": "cpf/{cpf}/pesquisaFoto",
}

NETBOX_ENDPOINTS = {
    "ip_addresses": "ipam/ip-addresses/",
    "prefixes": "ipam/prefixes/",
}


class InternalAPIConfigurationError(RuntimeError):
    pass


def build_endpoint(template, **params):
    safe_params = {key: quote(str(value), safe="") for key, value in params.items()}
    return template.format(**safe_params)


def _base_url(service_name):
    urls = current_app.config.get("INTERNAL_API_BASE_URLS") or {}
    base_url = urls.get(service_name)
    if not base_url and service_name == SERVICE_PM_CDPM:
        base_url = current_app.config.get("PM_API_BASE_URL")
    if not base_url and service_name == SERVICE_NETBOX:
        base_url = current_app.config.get("NETBOX_API_BASE_URL")
    if not base_url:
        raise InternalAPIConfigurationError("Base URL da integração interna não configurada.")
    return str(base_url).rstrip("/")


def _timeout(service_name):
    timeouts = current_app.config.get("INTERNAL_API_TIMEOUTS") or {}
    if service_name == SERVICE_PM_CDPM and service_name not in timeouts:
        return float(current_app.config.get("PM_API_TIMEOUT", 10))
    if service_name == SERVICE_NETBOX and service_name not in timeouts:
        return float(current_app.config.get("NETBOX_API_TIMEOUT", 10))
    return float(timeouts.get(service_name, 10))


def _verify_tls(service_name):
    verify_tls = current_app.config.get("INTERNAL_API_VERIFY_TLS") or {}
    if verify_tls.get(service_name) is False:
        return False
    if service_name == SERVICE_PM_CDPM and service_name not in verify_tls:
        if current_app.config.get("PM_API_VERIFY_TLS") is False:
            return False
    if service_name == SERVICE_NETBOX and service_name not in verify_tls:
        if current_app.config.get("NETBOX_API_VERIFY_TLS") is False:
            return False

    ca_bundles = current_app.config.get("INTERNAL_API_CA_BUNDLES") or {}
    if service_name == SERVICE_PM_CDPM and service_name not in ca_bundles:
        return current_app.config.get("PM_API_CA_BUNDLE") or True
    if service_name == SERVICE_NETBOX and service_name not in ca_bundles:
        return current_app.config.get("NETBOX_API_CA_BUNDLE") or True
    return ca_bundles.get(service_name) or True


class InternalAPIClient:
    """Single HTTP integration layer; callers use logical services, not raw URLs."""

    def __init__(self, service_name):
        self.service_name = service_name
        self.base_url = _base_url(service_name)
        self.timeout = _timeout(service_name)
        self.verify = _verify_tls(service_name)
        self.session = requests.Session()
        self.token = self._token()

    def _token(self):
        tokens = current_app.config.get("INTERNAL_API_TOKENS") or {}
        if self.service_name == SERVICE_NETBOX and self.service_name not in tokens:
            return current_app.config.get("NETBOX_API_TOKEN")
        return tokens.get(self.service_name)

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        self.session.close()
        return False

    def get_json(self, path, params=None):
        url = f"{self.base_url}/{path.lstrip('/')}"
        headers = {}
        if self.token:
            headers["Authorization"] = f"Token {self.token}"
        response = self.session.get(url, params=params, headers=headers, timeout=self.timeout, verify=self.verify)
        return response
