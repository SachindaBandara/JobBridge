"""Orchestrates: sources -> links -> new-only -> parse -> render -> Blogger."""
import logging
from . import whatsapp
from .ai import summarize
from .extractors import discover_feed_url, is_feed, links_from_feed, links_from_listing, parse_job
from .fetcher import Fetcher, FetchError
from .post_metadata import post_labels, post_title, search_description
from .render import render_post
from .storage import Store

log = logging.getLogger(__name__)


def collect_links(src: dict, fetcher: Fetcher) -> list[str]:
    n = int(src.get("max_items", 5))
    kind = src.get("type")
    if kind == "whatsapp_file":
        return whatsapp.links_from_file(src["path"], n)
    if kind == "url":
        return [src["url"]]
    if kind == "website":
        final, text = fetcher.get(src["url"])
        if is_feed(text):
            return links_from_feed(text, n)
        feed = discover_feed_url(text, final)
        if feed:
            try:
                _, ftext = fetcher.get(feed)
                links = links_from_feed(ftext, n)
                if links:
                    return links
            except FetchError as e:
                log.info("Feed discovery failed (%s); using page links", e)
        return links_from_listing(text, final, n)
    raise ValueError(f"Unknown source type: {kind}")


def run(cfg: dict, sources: list[dict], publish: bool = False, dry_run: bool = False) -> list[dict]:
    fetcher, store = Fetcher(cfg), Store()
    blogger = None
    if not dry_run:
        from .blogger import Blogger
        blogger = Blogger(cfg)
    go_live = publish or cfg["blogger"]["publish_immediately"]
    results = []
    for src in sources:
        try:
            links = collect_links(src, fetcher)
        except (FetchError, OSError, ValueError) as e:
            log.error("Source failed %s: %s", src, e)
            continue
        for link in links:
            if store.seen(link):
                continue
            try:
                final, page = fetcher.get(link)
                job = parse_job(page, final)
                if cfg["content"]["use_ai_summary"]:
                    job.summary = summarize(job, cfg)
                content = render_post(job, cfg["content"]["excerpt_chars"])
                title = post_title(job)
                labels = post_labels(job, cfg["blogger"]["labels"])
                description = search_description(job)
                if dry_run:
                    results.append({
                        "title": title,
                        "url": final,
                        "status": "dry-run",
                        "html": content,
                        "labels": labels,
                        "search_description": description,
                        "search_description_requires_manual_entry": True,
                    })
                    continue
                post = blogger.insert(title, content, labels, is_draft=not go_live)
                status = "published" if go_live else "draft"
                store.add(link, job.title, status, post.get("id", ""))
                results.append({
                    "title": title,
                    "url": post.get("url", ""),
                    "status": status,
                    "labels": labels,
                    "search_description": description,
                    "search_description_requires_manual_entry": True,
                })
                log.info("%s: %s", status, title)
            except Exception as e:  # keep going on per-item failures
                log.error("Item failed %s: %s", link, e)
    return results
