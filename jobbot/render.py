"""Build Blogger post HTML with a company logo, dates, and the full job description."""
import html
from datetime import date, datetime
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


def render_post(job, excerpt_chars: int = 600) -> str:
    e = html.escape
    rows = [("Posted", _date_only(job.date_posted)), ("Apply before", _date_only(job.valid_through))]
    facts = "".join(f"<li><strong>{e(k)}:</strong> {e(v)}</li>" for k, v in rows if v)
    logo = ""
    if job.company_logo_url:
        logo_url = e(job.company_logo_url, quote=True)
        logo_alt = e(f"{job.company} logo" if job.company else "Company logo", quote=True)
        logo = f'<p><img src="{logo_url}" alt="{logo_alt}"></p>'
    advertisement = ""
    if job.advertisement_image_url:
        image_url = e(job.advertisement_image_url, quote=True)
        advertisement = f'<p><img src="{image_url}" alt="Job advertisement"></p>'
    summary = ""
    if job.summary:
        summary_text = job.summary[:excerpt_chars]
        if len(job.summary) > excerpt_chars:
            summary_text = summary_text.rsplit(" ", 1)[0] + "…"
        summary = f"<h2>Overview</h2><p>{e(summary_text)}</p>"
    body = (
        logo
        + advertisement
        + (f"<ul>{facts}</ul>" if facts else "")
        + summary
        + sanitize_html(job.description_html)
    )
    return sanitize_html(body)
