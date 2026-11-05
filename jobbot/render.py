"""Build Blogger post HTML with a company logo, dates, and the full job description."""
import html
import re
from datetime import date, datetime
from urllib.parse import urlsplit
from bs4 import BeautifulSoup
from .post_metadata import (
    is_gazette_job,
    is_govdoc_source,
    uses_government_post_design,
)
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


def _information_row(job) -> str:
    government_design = uses_government_post_design(job)
    location_label = "Closing date" if government_design else "Location"
    location_value = (
        _date_only(job.visible_closing_date) if government_design else job.location
    ) or "Not specified"
    facts = (
        ("Posted", _date_only(job.date_posted) or "Not specified"),
        ("Job position", job.title or "Not specified"),
        (location_label, location_value),
        ("Employment type", _employment_type(job.employment_type) or "Not specified"),
    )
    cells = "".join(
        '<td style="width:25%;padding:13px 14px;vertical-align:top;'
        'border-right:1px solid #dcebe1">'
        f'<span style="display:block;margin-bottom:5px;color:#718078;font-size:11px;'
        f'font-weight:500;letter-spacing:.3px">{html.escape(label)}</span>'
        f'<strong style="display:block;color:#183d2d;font-size:13px;line-height:1.45;'
        f'font-weight:600">{html.escape(value)}</strong></td>'
        for label, value in facts
    )
    return (
        '<table role="presentation" style="width:100%;margin:0 0 24px;'
        'border:1px solid #dcebe1;border-radius:10px;border-spacing:0;'
        'background-color:#f4f8f5"><tbody><tr>'
        + cells
        + '</tr></tbody></table>'
    )


def _company_logo(job) -> str:
    if is_gazette_job(job):
        return ""
    safe_logo_url = _safe_image_url(job.company_logo_url)
    if not safe_logo_url and is_govdoc_source(job):
        candidate = job.advertisement_image_url
        if "/logo/" in urlsplit(candidate or "").path.lower():
            safe_logo_url = _safe_image_url(candidate)
    if not safe_logo_url:
        return ""
    logo_url = html.escape(safe_logo_url, quote=True)
    logo_alt = html.escape(f"{job.company} logo" if job.company else "Company logo", quote=True)
    return (
        '<div style="margin:0 0 22px;padding:0 0 18px;border-bottom:1px solid #e8eee9">'
        f'<img src="{logo_url}" alt="{logo_alt}" '
        'style="display:block;max-width:180px;max-height:80px;object-fit:contain">'
        '</div>'
    )


def _section_heading(title: str) -> str:
    return (
        '<h2 style="margin:0 0 14px;padding:0 0 9px;border-bottom:2px solid #e5eee8;'
        'color:#183d2d;font-size:19px;line-height:1.35;font-weight:600">'
        f'{html.escape(title)}</h2>'
    )


def _supplementary_facts(job) -> str:
    facts = tuple(
        (label, value)
        for label, value in (
            ("Salary", job.salary),
            ("Closing date", _date_only(job.visible_closing_date)),
        )
        if value
    )
    if not facts:
        return ""
    cells = "".join(
        '<td style="width:50%;padding:11px 14px;vertical-align:top">'
        f'<span style="display:block;margin-bottom:4px;color:#718078;font-size:11px">'
        f'{html.escape(label)}</span>'
        f'<strong style="color:#183d2d;font-size:13px;font-weight:600">{html.escape(value)}</strong></td>'
        for label, value in facts
    )
    return (
        '<table role="presentation" style="width:100%;margin:0 0 24px;'
        'border:1px solid #e4ebe6;border-radius:8px;border-spacing:0;'
        'background-color:#fff"><tbody><tr>'
        + cells
        + '</tr></tbody></table>'
    )


def _safe_link(value: str) -> str:
    parsed = urlsplit(value or "")
    if (
        parsed.scheme.lower() not in ("http", "https")
        or not parsed.hostname
        or parsed.username
        or parsed.password
    ):
        return ""
    return value


def _job_action_sections(job, include_job_details: bool = True) -> str:
    sections = []
    if include_job_details:
        sections.append(("Job Details", job.job_details_links))
    sections.append(("Download Applications", job.application_links))
    rows = []
    for heading, links in sections:
        buttons = []
        for item in links:
            url = _safe_link(item.get("url", ""))
            if not url:
                continue
            label = html.escape(item.get("text") or "Open")
            buttons.append(
                f'<a href="{html.escape(url, quote=True)}" target="_blank" rel="noopener noreferrer" '
                'style="display:block;margin-top:8px;padding:10px 12px;border-radius:6px;'
                'background-color:#0866e8;color:#fff;text-align:center;text-decoration:none;'
                f'font-size:13px;font-weight:500">{label}</a>'
            )
        if buttons:
            width = "50%" if include_job_details else "100%"
            rows.append(
                f'<td style="width:{width};padding:13px 14px;vertical-align:top;'
                'border:1px solid #dfe5e1;border-radius:6px;background-color:#f5f6f5">'
                f'<h3 style="margin:0;color:#26332d;text-align:center;font-size:15px;'
                f'font-weight:600">{html.escape(heading)}</h3>'
                + "".join(buttons)
                + '</td>'
            )
    if not rows:
        return ""
    if len(rows) == 1 and include_job_details:
        rows.append('<td style="width:50%;padding:0"></td>')
    return (
        '<table role="presentation" style="width:100%;margin:0 0 24px;'
        'border-spacing:10px 0"><tbody><tr>'
        + "".join(rows)
        + '</tr></tbody></table>'
    )


def _govdoc_action_sections(job) -> str:
    sections = _job_action_sections(
        job,
        include_job_details=not is_gazette_job(job),
    )
    if not sections:
        return ""
    return f'<div style="margin-top:30px">{sections}</div>'


def _gazette_details_table(job) -> str:
    rows = []
    for item in job.gazette_details:
        label = item.get("label", "")
        if re.search(r"\b(?:whatsapp|facebook)\b", label, re.I):
            continue
        label = html.escape(label)
        text = html.escape(item.get("text", ""))
        url = _safe_link(item.get("url", ""))
        value = (
            f'<a href="{html.escape(url, quote=True)}" target="_blank" rel="noopener noreferrer" '
            'style="color:#00e;text-decoration:underline">'
            f'{text}</a>'
            if url
            else text
        )
        rows.append(
            '<tr>'
            '<th scope="row" style="width:50%;padding:10px 12px;border:1px solid #d9d9d9;'
            'text-align:center;font-weight:700">'
            f'{label}</th>'
            '<td style="width:50%;padding:10px 12px;border:1px solid #d9d9d9;'
            'text-align:center">'
            f'{value}</td></tr>'
        )
    if not rows:
        return ""
    return (
        '<table style="width:100%;margin-top:16px;border-collapse:collapse;'
        'font-size:15px;line-height:1.5"><tbody>'
        + "".join(rows)
        + '</tbody></table>'
    )


def _contact_section(job) -> str:
    contacts = []
    for item in job.contact_links:
        contact_type = item.get("type")
        value = item.get("url", "")
        if contact_type == "email":
            parsed = urlsplit(value)
            if parsed.scheme.lower() != "mailto" or not re.fullmatch(
                r"[^@\s]+@[^@\s]+\.[^@\s]+", parsed.path
            ) or parsed.query:
                continue
        elif contact_type == "phone":
            parsed = urlsplit(value)
            if parsed.scheme.lower() != "tel" or not re.fullmatch(
                r"[+0-9().\-\s]{5,40}", parsed.path
            ):
                continue
        else:
            continue
        contacts.append(
            '<td style="width:50%;padding:10px 12px;vertical-align:top">'
            f'<strong style="display:block;margin-bottom:4px;color:#718078;font-size:12px">'
            f'{html.escape(contact_type.title())}</strong>'
            f'<a href="{html.escape(value, quote=True)}" style="color:#0866e8;'
            f'font-size:14px;text-decoration:none">{html.escape(item.get("text") or parsed.path)}</a>'
            '</td>'
        )
    if not contacts:
        return ""
    if len(contacts) % 2:
        contacts.append('<td style="width:50%;padding:0"></td>')
    rows = "".join(
        "<tr>" + "".join(contacts[index:index + 2]) + "</tr>"
        for index in range(0, len(contacts), 2)
    )
    return (
        '<table role="presentation" style="width:100%;margin:0 0 24px;'
        'border-spacing:0;border-top:1px solid #e4ebe6"><tbody>'
        + rows
        + '</tbody></table>'
    )


def render_post(job, excerpt_chars: int = 600) -> str:
    government_design = uses_government_post_design(job)
    e = html.escape
    summary = ""
    if job.summary and not government_design:
        summary_text = job.summary[:excerpt_chars]
        if len(job.summary) > excerpt_chars:
            summary_text = summary_text.rsplit(" ", 1)[0] + "…"
        summary = (
            '<div style="margin:0 0 25px;padding:16px 18px;border-left:4px solid #16805a;'
            'border-radius:0 8px 8px 0;background-color:#f3f8f4">'
            f'{_section_heading("At a glance")}'
            f'<p style="margin:0;color:#35443b;font-size:14px;line-height:1.7">{e(summary_text)}</p></div>'
        )
    description = sanitize_html(job.description_html)
    description_section = (
        '<section style="margin:0;padding:0">'
        f'{_section_heading("Job description")}'
        f'<div style="color:#35443b;font-size:15px;line-height:1.8">{description}'
        f'{_gazette_details_table(job)}</div>'
        '</section>'
    )
    return (
        '<style>@import url("https://fonts.googleapis.com/css2?family=Montserrat:wght@400;500;600;700&display=swap");'
        '.jobbridge-post{font-family:Montserrat,Arial,Helvetica,sans-serif}</style>'
        '<div style="max-width:760px;margin:0 auto;padding:22px 24px;color:#26332d;'
        'font-family:Montserrat,Arial,Helvetica,sans-serif;line-height:1.65" class="jobbridge-post">'
        '<div style="height:4px;margin:0 0 22px;border-radius:3px;background-color:#16805a"></div>'
        f'{_company_logo(job)}'
        f'{_information_row(job)}'
        f'{"" if government_design else _supplementary_facts(job)}'
        f'{summary}'
        f'{"" if government_design else _job_action_sections(job)}'
        f'{"" if government_design else _contact_section(job)}'
        f'{description_section}'
        f'{_govdoc_action_sections(job) if government_design else ""}'
        '</div>'
    )
