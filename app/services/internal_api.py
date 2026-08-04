import re
from urllib.parse import quote, urlparse

import requests
from flask import current_app


SERVICE_PM_CDPM = "pm_cdpm"
SERVICE_PM_EMAIL = "pm_email"
SERVICE_NETBOX = "netbox"
SERVICE_IPINFO = "ipinfo"

PM_ENDPOINTS = {
    "dados_por_cpf": "cpf/{cpf}/dadosResumidos",
    "dados_por_re": "re/{re}/dadosResumidos",
    "caracteristica_fisica": "cpf/{cpf}/caracteristicaFisica",
    "documentos": "cpf/{cpf}/documentos",
    "informacao_contato": "cpf/{cpf}/informacaoContato",
    "pesquisa_foto": "cpf/{cpf}/pesquisaFoto",
}

PM_EMAIL_ENDPOINTS = {
    "consulta_email": "ConsultaEmail.aspx",
}

NETBOX_ENDPOINTS = {
    "ip_addresses": "ipam/ip-addresses/",
    "prefixes": "ipam/prefixes/",
}

IPINFO_ENDPOINTS = {
    "lookup": "lookup/{ip}",
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
    if not base_url and service_name == SERVICE_PM_EMAIL:
        base_url = current_app.config.get("PM_EMAIL_SEARCH_BASE_URL")
    if not base_url and service_name == SERVICE_NETBOX:
        base_url = current_app.config.get("NETBOX_API_BASE_URL")
    if not base_url and service_name == SERVICE_IPINFO:
        base_url = current_app.config.get("IPINFO_API_BASE_URL")
    if not base_url:
        raise InternalAPIConfigurationError("Base URL da integração interna não configurada.")
    clean_base_url = re.sub(r"[\x00-\x20]+", "", str(base_url)).strip("'\"<>")
    parsed = urlparse(clean_base_url.rstrip("/"))
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise InternalAPIConfigurationError("Base URL da integração interna inválida.")
    if parsed.username or parsed.password:
        raise InternalAPIConfigurationError("Base URL da integração interna não pode conter credenciais.")
    return parsed.geturl()


def _build_url(base_url, path):
    clean_path = str(path or "").strip().lstrip("/")
    if not clean_path:
        return base_url
    if base_url.lower().endswith(f"/{clean_path.lower()}"):
        return base_url
    return f"{base_url}/{clean_path}"


def _timeout(service_name):
    timeouts = current_app.config.get("INTERNAL_API_TIMEOUTS") or {}
    if service_name == SERVICE_PM_CDPM and service_name not in timeouts:
        return float(current_app.config.get("PM_API_TIMEOUT", 10))
    if service_name == SERVICE_PM_EMAIL and service_name not in timeouts:
        return float(current_app.config.get("PM_API_TIMEOUT", 10))
    if service_name == SERVICE_NETBOX and service_name not in timeouts:
        return float(current_app.config.get("NETBOX_API_TIMEOUT", 10))
    if service_name == SERVICE_IPINFO and service_name not in timeouts:
        return float(current_app.config.get("IPINFO_API_TIMEOUT", 10))
    return float(timeouts.get(service_name, 10))


def _verify_tls(service_name):
    verify_tls = current_app.config.get("INTERNAL_API_VERIFY_TLS") or {}
    if verify_tls.get(service_name) is False:
        return False
    if service_name == SERVICE_PM_CDPM and service_name not in verify_tls:
        if current_app.config.get("PM_API_VERIFY_TLS") is False:
            return False
    if service_name == SERVICE_PM_EMAIL and service_name not in verify_tls:
        if current_app.config.get("PM_API_VERIFY_TLS") is False:
            return False
    if service_name == SERVICE_NETBOX and service_name not in verify_tls:
        if current_app.config.get("NETBOX_API_VERIFY_TLS") is False:
            return False
    if service_name == SERVICE_IPINFO and service_name not in verify_tls:
        if current_app.config.get("IPINFO_API_VERIFY_TLS") is False:
            return False

    ca_bundles = current_app.config.get("INTERNAL_API_CA_BUNDLES") or {}
    if service_name == SERVICE_PM_CDPM and service_name not in ca_bundles:
        return current_app.config.get("PM_API_CA_BUNDLE") or True
    if service_name == SERVICE_PM_EMAIL and service_name not in ca_bundles:
        return current_app.config.get("PM_API_CA_BUNDLE") or True
    if service_name == SERVICE_NETBOX and service_name not in ca_bundles:
        return current_app.config.get("NETBOX_API_CA_BUNDLE") or True
    if service_name == SERVICE_IPINFO and service_name not in ca_bundles:
        return current_app.config.get("IPINFO_API_CA_BUNDLE") or True
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
        if self.service_name == SERVICE_IPINFO and self.service_name not in tokens:
            return current_app.config.get("IPINFO_API_TOKEN")
        return tokens.get(self.service_name)

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        self.session.close()
        return False

    def get_json(self, path, params=None):
        url = _build_url(self.base_url, path)
        headers = {"Accept": "application/json"}
        if self.token and self.service_name == SERVICE_NETBOX:
            headers["Authorization"] = f"Token {self.token}"
        response = self.session.get(
            url,
            params=params,
            headers=headers,
            timeout=self.timeout,
            verify=self.verify,
            allow_redirects=False,
        )
        return response

    def get(self, path):
        url = _build_url(self.base_url, path)
        return self.session.get(
            url,
            headers={"Accept": "text/html,application/xhtml+xml"},
            timeout=self.timeout,
            verify=self.verify,
            allow_redirects=False,
        )

    def post_form(self, path, data):
        url = _build_url(self.base_url, path)
        return self.session.post(
            url,
            data=data,
            headers={
                "Accept": "text/html,application/xhtml+xml",
                "Content-Type": "application/x-www-form-urlencoded",
            },
            timeout=self.timeout,
            verify=self.verify,
            allow_redirects=False,
        )
