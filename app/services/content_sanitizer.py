import re

import bleach
from bleach.css_sanitizer import CSSSanitizer


MAX_DESCRIPTION_HTML_LENGTH = 100_000
MAX_DESCRIPTION_TEXT_LENGTH = 50_000
MAX_DESCRIPTION_LINKS = 50
MAX_DESCRIPTION_ELEMENTS = 5_000

ALLOWED_TAGS = {
    "p", "br", "strong", "b", "em", "i", "u", "s", "strike", "h1", "h2", "h3",
    "blockquote", "ul", "ol", "li", "a", "code", "pre", "hr", "span", "sub", "sup",
    "div", "font",
}
ALLOWED_CLASSES = {
    "ql-align-center", "ql-align-right", "ql-align-justify",
    "ql-size-small", "ql-size-large", "ql-size-huge",
    "ql-font-serif", "ql-font-monospace",
    "ql-indent-1", "ql-indent-2", "ql-indent-3", "ql-indent-4",
    "ql-indent-5", "ql-indent-6", "ql-indent-7", "ql-indent-8",
    "ql-syntax",
}
ALLOWED_STYLE_PROPERTIES = {
    "color",
    "background-color",
    "font-size",
    "font-family",
    "text-align",
}
ALLOWED_URL_SCHEMES = {"http", "https", "mailto"}

_DANGEROUS_BLOCK_RE = re.compile(
    r"<\s*(script|style|iframe|object|embed|svg|math|meta|link)\b[^>]*>.*?<\s*/\s*\1\s*>",
    re.IGNORECASE | re.DOTALL,
)
_ELEMENT_RE = re.compile(r"<\s*[a-zA-Z][^>]*>")
_LINK_RE = re.compile(r"<\s*a\b", re.IGNORECASE)


class SanitizationError(ValueError):
    pass


def _allowed_attributes(tag, name, value):
    name = name.lower()
    if name.startswith("on"):
        return False
    if tag == "a" and name in {"href", "title", "target", "rel"}:
        return True
    if tag == "font" and name in {"face", "size", "color"}:
        return True
    if name == "align" and tag in {"p", "div", "h1", "h2", "h3"}:
        return (value or "").lower() in {"left", "center", "right", "justify"}
    if name == "class":
        return any(item in ALLOWED_CLASSES for item in (value or "").split())
    if name == "style":
        return True
    return False


def _strip_dangerous_content(raw_html):
    cleaned = raw_html
    previous = None
    while cleaned != previous:
        previous = cleaned
        cleaned = _DANGEROUS_BLOCK_RE.sub("", cleaned)
    return cleaned


def sanitize_incident_description(raw_html):
    raw_html = (raw_html or "").strip()
    if len(raw_html) > MAX_DESCRIPTION_HTML_LENGTH:
        raise SanitizationError("Descrição excede o tamanho máximo permitido.")

    if len(_ELEMENT_RE.findall(raw_html)) > MAX_DESCRIPTION_ELEMENTS:
        raise SanitizationError("Descrição possui elementos em excesso.")
    if len(_LINK_RE.findall(raw_html)) > MAX_DESCRIPTION_LINKS:
        raise SanitizationError("Descrição possui links em excesso.")

    cleaner = bleach.Cleaner(
        tags=ALLOWED_TAGS,
        attributes=_allowed_attributes,
        protocols=ALLOWED_URL_SCHEMES,
        css_sanitizer=CSSSanitizer(allowed_css_properties=ALLOWED_STYLE_PROPERTIES),
        strip=True,
        strip_comments=True,
    )
    sanitized = cleaner.clean(_strip_dangerous_content(raw_html)).strip()
    plain_text = " ".join(
        bleach.clean(sanitized, tags=set(), attributes={}, strip=True).split()
    )

    if not plain_text:
        raise SanitizationError("Descrição do incidente é obrigatória.")
    if len(plain_text) > MAX_DESCRIPTION_TEXT_LENGTH:
        raise SanitizationError("Texto da descrição excede o tamanho máximo permitido.")

    return sanitized, plain_text
