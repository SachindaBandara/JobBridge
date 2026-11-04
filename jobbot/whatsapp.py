"""WhatsApp channel input.

IMPORTANT: WhatsApp provides NO official API to read posts from a Channel, and scraping
WhatsApp violates its Terms of Service. This module therefore only supports content YOU
legitimately obtain: copy/paste of channel messages (saved to a text file). It extracts the
job links inside them. Channel invite links themselves are ignored (they carry no posts).
"""
import re
from pathlib import Path

URL_RE = re.compile(r"https?://[^\s<>\"')\]]+", re.I)
IGNORED = ("whatsapp.com", "wa.me")


def extract_links(text: str) -> list[str]:
    out, seen = [], set()
    for u in URL_RE.findall(text or ""):
        u = u.rstrip(".,;:!?")
        host = re.sub(r"^https?://([^/]+).*", r"\1", u).lower()
        if any(host == d or host.endswith("." + d) for d in IGNORED) or u in seen:
            continue
        seen.add(u)
        out.append(u)
    return out


def links_from_file(path: str, max_items: int = 20) -> list[str]:
    text = Path(path).read_text(encoding="utf-8", errors="replace")
    return list(reversed(extract_links(text)))[:max_items]   # newest messages are at the bottom
