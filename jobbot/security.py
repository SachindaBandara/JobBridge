"""Security helpers: SSRF-safe URL validation and HTML sanitisation."""
import ipaddress
import re
import socket
from urllib.parse import urlparse
import bleach
from bs4 import BeautifulSoup


class UnsafeURL(ValueError):
    pass


def _is_public(ip: str) -> bool:
    a = ipaddress.ip_address(ip.split("%")[0])
    return not (a.is_private or a.is_loopback or a.is_link_local or a.is_multicast
                or a.is_reserved or a.is_unspecified)


def validate_url(url: str, allowed_domains=None) -> str:
    """Reject anything that is not a plain http(s) URL to a public host (SSRF defence)."""
    p = urlparse((url or "").strip())
    if p.scheme not in ("http", "https"):
        raise UnsafeURL(f"Scheme not allowed: {p.scheme!r}")
    if not p.hostname:
        raise UnsafeURL("Missing host")
    if p.username or p.password:
        raise UnsafeURL("Credentials in URL not allowed")
    if p.port not in (None, 80, 443):
        raise UnsafeURL(f"Port not allowed: {p.port}")
    host = p.hostname.lower()
    if allowed_domains and not any(host == d or host.endswith("." + d) for d in allowed_domains):
        raise UnsafeURL(f"Domain not in allow-list: {host}")
    try:
        infos = socket.getaddrinfo(host, p.port or (443 if p.scheme == "https" else 80),
                                   proto=socket.IPPROTO_TCP)
    except socket.gaierror as e:
        raise UnsafeURL(f"DNS failure for {host}: {e}") from e
    for info in infos:
        if not _is_public(info[4][0]):
            raise UnsafeURL(f"Host resolves to non-public address: {info[4][0]}")
    return p.geturl()


ALLOWED_TAGS = [
    "p", "br", "hr", "div", "span", "ul", "ol", "li", "strong", "b", "em", "i",
    "h1", "h2", "h3", "h4", "h5", "h6", "a", "blockquote", "small", "img",
    "table", "thead", "tbody", "tr", "th", "td",
]
ALLOWED_ATTRS = {"a": ["href", "title", "rel", "target"], "img": ["src", "alt", "title"]}


def sanitize_html(html: str) -> str:
    """Keep safe semantic bold formatting while stripping source styles and unsafe markup."""
    html = re.sub(
        r"&lt;(/?(?:b|strong))&gt;",
        lambda match: f"<{match.group(1)}>",
        html or "",
        flags=re.I,
    )
    soup = BeautifulSoup(html, "html.parser")
    for tag in soup.find_all(True):
        declarations = tag.get("style", "")
        styled_bold = any(
            name.strip().lower() == "font-weight"
            and re.fullmatch(r"(?:bold|bolder|[6-9]00)\s*(?:!important)?", value.strip(), re.I)
            for name, _, value in (declaration.partition(":") for declaration in declarations.split(";"))
            if _
        )
        class_names = tag.get("class") or []
        class_bold = any(
            re.search(r"(?:^|[-_])(?:b|bold|strong|fw-bold|font-weight-bold)(?:$|[-_])", name, re.I)
            or re.search(r"(?:font|text)[-_]?bold", name, re.I)
            for name in class_names
        )
        if (styled_bold or class_bold) and tag.name not in ("b", "strong"):
            tag.name = "strong"
    return bleach.clean(str(soup), tags=ALLOWED_TAGS, attributes=ALLOWED_ATTRS,
                        protocols=["http", "https", "mailto"], strip=True)
