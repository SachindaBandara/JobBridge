"""Generate Blogger labels and a search description for a job post."""
import re
from urllib.parse import urlsplit


_GOVERNMENT_HINTS = re.compile(
    r"\b(?:government|govt\.?|ministry|public service commission|civil service|"
    r"municipal council|local authority)\b",
    re.I,
)
_GOVERNMENT_DESCRIPTION_HINTS = re.compile(
    r"\b(?:government|public sector|state sector)\s+(?:job|vacancy|position|role)\b",
    re.I,
)


def is_govdoc_source(job) -> bool:
    source_host = (urlsplit(job.source_url or "").hostname or "").lower().rstrip(".")
    return source_host == "job.govdoc.lk" or source_host.endswith(".job.govdoc.lk")


def is_gazette_government_job(job) -> bool:
    source_host = (urlsplit(job.source_url or "").hostname or "").lower().rstrip(".")
    return source_host in ("gazette.lk", "www.gazette.lk") and bool(
        getattr(job, "government_job", False)
    )


def is_gazette_job(job) -> bool:
    source_host = (urlsplit(job.source_url or "").hostname or "").lower().rstrip(".")
    return source_host in ("gazette.lk", "www.gazette.lk") and bool(
        getattr(job, "gazette_job", False)
    )


def uses_government_post_design(job) -> bool:
    return is_govdoc_source(job) or is_gazette_job(job)


def job_category(job) -> str:
    organization_types = re.sub(r"[\s_-]+", "", job.organization_type or "").lower()
    if "governmentorganization" in organization_types:
        return "Government Jobs"

    source_host = (urlsplit(job.source_url or "").hostname or "").lower().rstrip(".")
    employer_and_title = " ".join((job.company or "", job.title or ""))
    description = job.description_html or ""
    if (
        _GOVERNMENT_HINTS.search(employer_and_title)
        or _GOVERNMENT_DESCRIPTION_HINTS.search(description)
        or source_host.endswith((".gov", ".gov.lk"))
        or is_govdoc_source(job)
        or is_gazette_government_job(job)
    ):
        return "Government Jobs"
    return "Private Jobs"


def _label_text(value: str) -> str:
    return " ".join(str(value or "").split())[:200]


def post_title(job) -> str:
    position = " ".join(str(job.title or "").split())
    company = " ".join(str(job.company or "").split())
    if position and company:
        title = f"{position} - {company}"
    else:
        title = position or company
    return title[:200]


def post_labels(job, configured_labels: list[str] | None = None) -> list[str]:
    candidates = [
        *(configured_labels or []),
        job.company,
        job.title,
        job_category(job),
    ]
    if re.search(r"\bintern(?:ship)?s?\b", job.employment_type or "", re.I):
        candidates.append("Internships")
    job_specific_labels = any(_label_text(value) for value in (job.company, job.title, job_category(job)))
    if job_specific_labels:
        candidates = [
            label for label in candidates
            if _label_text(label).casefold() != "jobs"
        ]
    labels = []
    seen = set()
    for candidate in candidates:
        label = _label_text(candidate)
        key = label.casefold()
        if label and key not in seen:
            labels.append(label)
            seen.add(key)
    return labels


def search_description(job) -> str:
    category = job_category(job)
    title = _label_text(job.title)
    company = _label_text(job.company)
    if len(title) > 60:
        title = title[:57].rsplit(" ", 1)[0] + "..."
    if len(company) > 60:
        company = company[:57].rsplit(" ", 1)[0] + "..."
    description = f"{category}: {title}"
    if company:
        description += f" at {company}"
    return description[:150].rsplit(" ", 1)[0] if len(description) > 150 else description
