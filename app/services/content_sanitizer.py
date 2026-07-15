from html import escape
from html.parser import HTMLParser
from urllib.parse import urlparse


MAX_DESCRIPTION_HTML_LENGTH = 100_000
MAX_DESCRIPTION_TEXT_LENGTH = 50_000
MAX_DESCRIPTION_LINKS = 50
MAX_DESCRIPTION_ELEMENTS = 5_000

ALLOWED_TAGS = {
    "p", "br", "strong", "b", "em", "i", "u", "s", "strike", "h1", "h2", "h3",
    "blockquote", "ul", "ol", "li", "a", "code", "pre", "hr", "span", "sub", "sup",
    "div", "font",
}
VOID_TAGS = {"br", "hr"}
DANGEROUS_TAGS = {"script", "style", "iframe", "object", "embed", "svg", "math", "meta", "link"}
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
ALLOWED_URL_SCHEMES = {"http", "https", "mailto", ""}


class SanitizationError(ValueError):
    pass


class _Sanitizer(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts = []
        self.text_parts = []
        self.link_count = 0
        self.element_count = 0
        self.skip_depth = 0

    def handle_starttag(self, tag, attrs):
        tag = tag.lower()
        if tag not in ALLOWED_TAGS:
            if tag in DANGEROUS_TAGS:
                self.skip_depth += 1
            return

        if self.skip_depth:
            return

        self.element_count += 1
        if self.element_count > MAX_DESCRIPTION_ELEMENTS:
            raise SanitizationError("Descrição possui elementos em excesso.")

        clean_attrs = self._sanitize_attrs(tag, attrs)
        attrs_text = "".join(f' {name}="{escape(value, quote=True)}"' for name, value in clean_attrs)
        self.parts.append(f"<{tag}{attrs_text}>")

    def handle_endtag(self, tag):
        tag = tag.lower()
        if tag not in ALLOWED_TAGS:
            if tag in DANGEROUS_TAGS:
                self.skip_depth = max(0, self.skip_depth - 1)
            return
        if not self.skip_depth and tag not in VOID_TAGS:
            self.parts.append(f"</{tag}>")

    def handle_startendtag(self, tag, attrs):
        self.handle_starttag(tag, attrs)

    def handle_data(self, data):
        if self.skip_depth:
            return
        self.parts.append(escape(data))
        self.text_parts.append(data)

    def handle_entityref(self, name):
        if self.skip_depth:
            return
        self.parts.append(f"&{name};")
        self.text_parts.append(f"&{name};")

    def handle_charref(self, name):
        if self.skip_depth:
            return
        self.parts.append(f"&#{name};")
        self.text_parts.append(f"&#{name};")

    def _sanitize_attrs(self, tag, attrs):
        clean = []
        for raw_name, raw_value in attrs:
            if not raw_name:
                continue
            name = raw_name.lower()
            value = (raw_value or "").strip()
            if name.startswith("on"):
                continue
            if tag == "a" and name in {"href", "title", "target", "rel"}:
                if name == "href":
                    parsed = urlparse(value)
                    if parsed.scheme.lower() not in ALLOWED_URL_SCHEMES:
                        continue
                    self.link_count += 1
                    if self.link_count > MAX_DESCRIPTION_LINKS:
                        raise SanitizationError("Descrição possui links em excesso.")
                    clean.append(("href", value))
                    clean.append(("target", "_blank"))
                    clean.append(("rel", "noopener noreferrer"))
                elif name == "title":
                    clean.append((name, value[:150]))
                continue
            if tag == "font" and name in {"face", "size", "color"}:
                clean.append((name, value[:80]))
                continue
            if name == "align" and tag in {"p", "div", "h1", "h2", "h3"}:
                if value.lower() in {"left", "center", "right", "justify"}:
                    clean.append(("align", value.lower()))
                continue
            if name == "class":
                classes = [item for item in value.split() if item in ALLOWED_CLASSES]
                if classes:
                    clean.append(("class", " ".join(classes)))
                continue
            if name == "style":
                style = self._sanitize_style(value)
                if style:
                    clean.append(("style", style))
        return clean

    def _sanitize_style(self, style):
        allowed = []
        for declaration in style.split(";"):
            if ":" not in declaration:
                continue
            prop, value = declaration.split(":", 1)
            prop = prop.strip().lower()
            value = value.strip()
            lowered = value.lower()
            if prop not in ALLOWED_STYLE_PROPERTIES:
                continue
            if "url(" in lowered or "expression(" in lowered or "javascript:" in lowered:
                continue
            allowed.append(f"{prop}: {value[:80]}")
        return "; ".join(allowed)


def sanitize_incident_description(raw_html):
    raw_html = (raw_html or "").strip()
    if len(raw_html) > MAX_DESCRIPTION_HTML_LENGTH:
        raise SanitizationError("Descrição excede o tamanho máximo permitido.")

    parser = _Sanitizer()
    parser.feed(raw_html)
    parser.close()
    sanitized = "".join(parser.parts).strip()
    plain_text = " ".join("".join(parser.text_parts).split())

    if not plain_text:
        raise SanitizationError("Descrição do incidente é obrigatória.")
    if len(plain_text) > MAX_DESCRIPTION_TEXT_LENGTH:
        raise SanitizationError("Texto da descrição excede o tamanho máximo permitido.")

    return sanitized, plain_text
