"""Build Blogger post HTML with a company logo, dates, and the full job description."""
import html
from datetime import date, datetime
from urllib.parse import urlsplit
from bs4 import BeautifulSoup
from .security import sanitize_html


def _date_only(value: str) -> str:
    value = (value or "").strip()
    if not value:
        return ""
    try:
        if len(value) == 10:
            return date.fromisoformat(value).isoformat()
        return datetime.fromisoformat(value.replace("Z", "+00:00")).date().isoformat()
    except ValueError:
        return value


def _safe_image_url(value: str) -> str:
    parsed = urlsplit(value or "")
    if parsed.scheme.lower() not in ("http", "https") or not parsed.hostname:
        return ""
    if parsed.username or parsed.password:
        return ""
    return value


def _employment_type(value: str) -> str:
    return " ".join((value or "").replace("_", " ").replace("-", " ").split()).title()


def render_post(job, excerpt_chars: int = 600) -> str:
    e = html.escape
    post_facts = (
        ("Posted", _date_only(job.date_posted) or "Not specified"),
        ("Job position", job.title or "Not specified"),
        ("Location", job.location or "Not specified"),
        ("Employment type", _employment_type(job.employment_type) or "Not specified"),
    )
    cell_width = f"{100 / len(post_facts):.2f}%" if post_facts else ""
    date_cells = "".join(
        f'<td style="width:{cell_width};padding:12px 16px;vertical-align:top">'
        f'<span style="display:block;color:#64756c;font-size:12px">{e(label)}</span>'
        f'<strong style="display:block;margin-top:3px;color:#183d2d;font-size:14px">{e(value)}</strong></td>'
        for label, value in post_facts
    )
    dates = ""
    if date_cells:
        dates = (
            '<table style="width:100%;margin:0 0 24px;border:1px solid #dcebe1;'
            'border-radius:8px;border-spacing:0;background-color:#f3f8f4"><tbody><tr>'
            f"{date_cells}</tr></tbody></table>"
        )
    logo = ""
    safe_logo_url = _safe_image_url(job.company_logo_url)
    if safe_logo_url:
        logo_url = e(safe_logo_url, quote=True)
        logo_alt = e(f"{job.company} logo" if job.company else "Company logo", quote=True)
        logo = (
            f'<p style="margin:0 0 18px"><img src="{logo_url}" alt="{logo_alt}" '
            'style="display:block;max-width:180px;max-height:80px;object-fit:contain"></p>'
        )
    summary = ""
    if job.summary:
        summary_text = job.summary[:excerpt_chars]
        if len(job.summary) > excerpt_chars:
            summary_text = summary_text.rsplit(" ", 1)[0] + "…"
        summary = (
            '<div style="margin:0 0 22px;padding:16px 18px;border-left:4px solid #16805a;'
            'background-color:#f3f8f4"><h2 style="margin:0 0 8px;color:#183d2d;font-size:18px">'
            f"At a glance</h2><p style=\"margin:0\">{e(summary_text)}</p></div>"
        )
    description = sanitize_html(job.description_html)
    description_section = (
        '<h2 style="margin:0 0 12px;padding-bottom:8px;border-bottom:1px solid #e3eae5;'
        'color:#183d2d;font-size:20px">Job description</h2>'
        f'<div style="font-size:15px;line-height:1.75;color:#35443b">{description}</div>'
    )
    body = (
        '<style>@import url("https://fonts.googleapis.com/css2?family=Montserrat:wght@400;500;600;700&display=swap");'
        '.jobbridge-post{font-family:Montserrat,Arial,Helvetica,sans-serif}</style>'
        '<div style="max-width:760px;margin:0 auto;padding:20px 22px;color:#26332d;'
        'font-family:Montserrat,Arial,Helvetica,sans-serif;line-height:1.6" class="jobbridge-post">'
        + logo
        + dates
        + summary
        + description_section
        + "</div>"
    )
    return body
