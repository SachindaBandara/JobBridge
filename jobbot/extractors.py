"""Find new job links on listing pages/feeds and parse individual job pages."""
from html import escape
import json
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from urllib.parse import urljoin, urlparse
import feedparser
from bs4 import BeautifulSoup


@dataclass
class Job:
    title: str
    source_url: str
    company: str = ""
    location: str = ""
    employment_type: str = ""
    date_posted: str = ""
    valid_through: str = ""
    salary: str = ""
    description_html: str = ""
    summary: str = ""            # optional AI summary
    labels: list = field(default_factory=list)
    company_logo_url: str = ""
    advertisement_image_url: str = ""
    images: list[str] = field(default_factory=list)
    links: list[dict[str, str]] = field(default_factory=list)


JOB_HINTS = re.compile(r"job|career|vacanc|position|opening|hiring|recruit|apply|opportunit", re.I)
SKIP_HINTS = re.compile(r"login|signin|signup|register|about|contact|privacy|terms|/tag/|/category/|/page/|#|mailto:|javascript:|\.(jpg|png|gif|pdf|zip)$", re.I)


def is_feed(text: str) -> bool:
    head = text.lstrip()[:300].lower()
    return head.startswith("<?xml") or "<rss" in head or "<feed" in head


def discover_feed_url(html: str, base_url: str) -> str | None:
    soup = BeautifulSoup(html, "lxml")
    for l in soup.find_all("link", rel=True, type=True):
        if "alternate" in l["rel"] and ("rss" in l["type"] or "atom" in l["type"]) and l.get("href"):
            return urljoin(base_url, l["href"])
    return None


def links_from_feed(text: str, max_items: int) -> list[str]:
    d = feedparser.parse(text)
    entries = [
        (entry, index)
        for index, entry in enumerate(d.entries)
        if getattr(entry, "link", None)
    ]
    if any(entry.get("published_parsed") or entry.get("updated_parsed") for entry, _ in entries):
        entries.sort(
            key=lambda item: (
                item[0].get("published_parsed") or item[0].get("updated_parsed") or (1, 1, 1, 0, 0, 0, 0, 0, 0),
                -item[1],
            ),
            reverse=True,
        )
    return [entry.link for entry, _ in entries[:max_items]]


def _published_date(node) -> datetime | None:
    value = ""
    if getattr(node, "name", None) == "time":
        value = node.get("datetime") or node.get_text(" ", strip=True)
    if not value:
        date_node = node.select_one(
            'time[datetime], [itemprop="datePublished"], [itemprop="dateCreated"], '
            '[class*="date"], [class*="time"], [datetime], [data-date]'
        )
        if date_node:
            value = (
                date_node.get("datetime")
                or date_node.get("content")
                or date_node.get("data-date")
                or date_node.get_text(" ", strip=True)
            )
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).strip().replace("Z", "+00:00"))
    except ValueError:
        try:
            parsed = parsedate_to_datetime(str(value).strip())
        except (TypeError, ValueError, OverflowError):
            return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _listing_date(anchor) -> datetime | None:
    for parent in [anchor, *list(anchor.parents)[:5]]:
        published = _published_date(parent)
        if published:
            return published
    return None


def links_from_listing(html: str, base_url: str, max_items: int) -> list[str]:
    soup = BeautifulSoup(html, "lxml")
    host = urlparse(base_url).netloc
    candidates, seen = [], set()
    for a in soup.find_all("a", href=True):
        href = urljoin(base_url, a["href"]).split("#")[0]
        p = urlparse(href)
        text = a.get_text(" ", strip=True)
        if p.netloc != host or href in seen or href.rstrip("/") == base_url.rstrip("/"):
            continue
        if SKIP_HINTS.search(href) or len(text) < 8 or len(p.path.strip("/")) < 3:
            continue
        card = a.find_parent(["article", "li"])
        if not (JOB_HINTS.search(href) or JOB_HINTS.search(text)):
            card_hint = " ".join((card.get("class", []) if card else []) + ([card.get("id", "")] if card else []))
            if not card or not re.search(r"job|post|article|listing|vacan|career", card_hint, re.I):
                continue
        seen.add(href)
        candidates.append((_listing_date(a), len(candidates), href))

    if any(published for published, _, _ in candidates):
        candidates.sort(
            key=lambda item: (item[0] is not None, item[0] or datetime.min.replace(tzinfo=timezone.utc), -item[1]),
            reverse=True,
        )
    return [href for _, _, href in candidates[:max_items]]


def _flatten_ld(node):
    if isinstance(node, list):
        for n in node:
            yield from _flatten_ld(n)
    elif isinstance(node, dict):
        yield node
        graph = node.get("@graph")
        if isinstance(graph, list):
            yield from _flatten_ld(graph)


def _s(v) -> str:
    if isinstance(v, dict):
        return str(v.get("name") or v.get("value") or "")
    if isinstance(v, list):
        return ", ".join(filter(None, (_s(x) for x in v)))
    return str(v or "")


def _location(v) -> str:
    parts = []
    for loc in (v if isinstance(v, list) else [v]):
        addr = loc.get("address", {}) if isinstance(loc, dict) else {}
        if isinstance(addr, dict):
            parts.append(", ".join(filter(None, [addr.get("addressLocality"), addr.get("addressRegion"), _s(addr.get("addressCountry"))])))
        elif addr:
            parts.append(str(addr))
    return "; ".join(p for p in parts if p)


def _salary(v) -> str:
    if not isinstance(v, dict):
        return ""
    val = v.get("value", {})
    if isinstance(val, dict):
        lo, hi, unit = val.get("minValue"), val.get("maxValue"), val.get("unitText", "")
        amount = f"{lo}-{hi}" if lo and hi else str(val.get("value", lo or hi or ""))
        return f"{amount} {v.get('currency', '')} {unit}".strip() if amount else ""
    return ""


def _logo_url(value, base_url: str) -> str:
    if isinstance(value, list):
        for item in value:
            url = _logo_url(item, base_url)
            if url:
                return url
        return ""
    if isinstance(value, dict):
        value = value.get("url") or value.get("contentUrl") or value.get("@id") or ""
    if not isinstance(value, str) or not value.strip():
        return ""
    url = urljoin(base_url, value.strip())
    return url if urlparse(url).scheme in ("http", "https") else ""


def _advertisement_image(soup: BeautifulSoup, base_url: str, structured_image=None) -> str:
    image = _logo_url(structured_image, base_url)
    if image:
        return image

    for meta in soup.find_all("meta", attrs={"content": True}):
        key = (meta.get("property") or meta.get("name") or "").lower()
        if key in ("og:image", "og:image:url", "twitter:image", "twitter:image:src"):
            image = _logo_url(meta["content"], base_url)
            if image:
                return image

    for candidate in _page_assets(soup, base_url)[0]:
        path = urlparse(candidate).path.lower()
        if re.search(r"(?:job|advert|vacanc|career|position)[-_/.]", path):
            return candidate
    return ""


def _page_assets(soup: BeautifulSoup, base_url: str) -> tuple[list[str], list[dict[str, str]]]:
    images, seen_images = [], set()
    candidates = []
    for image in soup.find_all(["img", "source"]):
        candidates.extend((image.get("src"), image.get("data-src"), image.get("data-original")))
        candidates.extend(
            item.strip().split()[0]
            for item in (image.get("srcset") or image.get("data-srcset") or "").split(",")
            if item.strip()
        )
    candidates.extend(
        meta.get("content")
        for meta in soup.find_all("meta", attrs={"content": True})
        if (meta.get("property") or meta.get("name") or "").lower()
        in ("og:image", "og:image:url", "twitter:image", "twitter:image:src")
    )
    for link in soup.find_all("link", href=True):
        rel = link.get("rel") or []
        if "image_src" in rel:
            candidates.append(link["href"])
    for candidate in candidates:
        if not candidate:
            continue
        url = urljoin(base_url, candidate.strip())
        if urlparse(url).scheme in ("http", "https") and url not in seen_images:
            seen_images.add(url)
            images.append(url)

    links, seen_links = [], set()
    for anchor in soup.find_all("a", href=True):
        url = urljoin(base_url, anchor["href"].strip())
        if urlparse(url).scheme not in ("http", "https") or url in seen_links:
            continue
        seen_links.add(url)
        links.append({"text": anchor.get_text(" ", strip=True)[:200], "url": url})
    return images, links


def parse_job(html: str, url: str) -> Job:
    soup = BeautifulSoup(html, "lxml")
    images, links = _page_assets(soup, url)
    for s in soup.find_all("script", type="application/ld+json"):
        try:
            data = json.loads(s.string or "")
        except (json.JSONDecodeError, TypeError):
            continue
        for node in _flatten_ld(data):
            t = node.get("@type")
            if t == "JobPosting" or (isinstance(t, list) and "JobPosting" in t):
                organization = node.get("hiringOrganization")
                return Job(
                    title=_s(node.get("title")) or (soup.title.get_text(strip=True) if soup.title else "Job opening"),
                    source_url=url,
                    company=_s(organization),
                    location=_location(node.get("jobLocation")) or _s(node.get("applicantLocationRequirements")),
                    employment_type=_s(node.get("employmentType")),
                    date_posted=_s(node.get("datePosted")),
                    valid_through=_s(node.get("validThrough")),
                    salary=_salary(node.get("baseSalary")),
                    description_html=_s(node.get("description")),
                    company_logo_url=_logo_url(
                        organization.get("logo") if isinstance(organization, dict) else None,
                        url,
                    ),
                    advertisement_image_url=_advertisement_image(soup, url, node.get("image")),
                    images=images,
                    links=links,
                )

    def meta(prop):
        m = soup.find("meta", attrs={"property": prop}) or soup.find("meta", attrs={"name": prop})
        return (m.get("content") or "").strip() if m else ""

    title = meta("og:title") or (soup.title.get_text(strip=True) if soup.title else "") or "Job opening"
    container = (
        soup.select_one('[itemprop="description"], .job-description, #job-description, .job-details')
        or soup.find("article")
        or soup.find("main")
        or soup.body
        or soup
    )
    for unwanted in container.find_all(["script", "style", "noscript", "iframe", "form", "button", "nav", "footer"]):
        unwanted.decompose()
    description = "".join(str(child) for child in container.contents).strip()
    if not BeautifulSoup(description, "lxml").get_text(" ", strip=True):
        description = f"<p>{escape(meta('og:description'))}</p>"
    return Job(
        title=title,
        source_url=url,
        description_html=description,
        advertisement_image_url=_advertisement_image(soup, url),
        images=images,
        links=links,
    )
