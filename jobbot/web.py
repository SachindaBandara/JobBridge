"""Local web interface for scanning job pages and creating Blogger drafts."""
import json
import logging
import mimetypes
import re
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse

from bs4 import BeautifulSoup

from .ai import GeminiAPIError, generate_image, summarize
from .extractors import Job, parse_job
from .fetcher import Fetcher, FetchError
from .pipeline import collect_links
from .post_metadata import post_labels, post_title, search_description
from .render import render_post
from .security import sanitize_html, validate_url
from .storage import Store

log = logging.getLogger(__name__)
MAX_BODY_BYTES = 3_000_000
STATIC_FILES = {
    "/": ("index.html", "text/html; charset=utf-8"),
    "/static/app.css": ("app.css", "text/css; charset=utf-8"),
    "/static/app.js": ("app.js", "text/javascript; charset=utf-8"),
}
GENERATED_DIR = Path("data/generated_images")


class APIError(Exception):
    def __init__(self, message: str, status: int = 400):
        super().__init__(message)
        self.status = status


def _job_data(job: Job, status: str = "ready", configured_labels: list[str] | None = None) -> dict:
    description = BeautifulSoup(job.description_html, "lxml").get_text(" ", strip=True)
    return {
        "title": job.title,
        "source_url": job.source_url,
        "company": job.company,
        "organization_type": job.organization_type,
        "location": job.location,
        "employment_type": job.employment_type,
        "date_posted": job.date_posted,
        "valid_through": job.valid_through,
        "salary": job.salary,
        "description": description,
        "description_html": sanitize_html(job.description_html),
        "summary": job.summary,
        "company_logo_url": job.company_logo_url,
        "advertisement_image_url": job.advertisement_image_url,
        "suggested_labels": post_labels(job, configured_labels),
        "blogger_title": post_title(job),
        "search_description": search_description(job),
        "images": job.images,
        "links": job.links,
        "status": status,
    }


def _job_from_data(data: dict) -> Job:
    if not isinstance(data, dict):
        raise APIError("A job object is required.")
    title = str(data.get("title") or "").strip()[:200]
    source_url = str(data.get("source_url") or "").strip()
    if not title or not source_url:
        raise APIError("The job title and source URL are required.")
    return Job(
        title=title,
        source_url=source_url,
        company=str(data.get("company") or "")[:300],
        organization_type=str(data.get("organization_type") or "")[:200],
        location=str(data.get("location") or "")[:500],
        employment_type=str(data.get("employment_type") or "")[:200],
        date_posted=str(data.get("date_posted") or "")[:100],
        valid_through=str(data.get("valid_through") or "")[:100],
        salary=str(data.get("salary") or "")[:200],
        description_html=str(data.get("description_html") or f"<p>{str(data.get('description') or '')}</p>"),
        summary=str(data.get("summary") or "")[:900],
        company_logo_url=str(data.get("company_logo_url") or "")[:2000],
        advertisement_image_url=str(data.get("advertisement_image_url") or "")[:2000],
    )


def _error_item(url: str, error: Exception) -> dict:
    return {"url": url, "message": str(error)[:300]}


def create_handler(cfg: dict):
    class Handler(BaseHTTPRequestHandler):
        server_version = "JobBridgeWeb/1.0"

        def _write(self, status: int, content_type: str, body: bytes):
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Referrer-Policy", "no-referrer")
            self.send_header("Content-Security-Policy", "default-src 'self'; img-src 'self' data:; style-src 'self'; script-src 'self'; connect-src 'self'; object-src 'none'; base-uri 'none'; frame-ancestors 'none'")
            self.end_headers()
            self.wfile.write(body)

        def _json(self, status: int, value: dict):
            body = json.dumps(value, ensure_ascii=False).encode("utf-8")
            self._write(status, "application/json; charset=utf-8", body)

        def _check_local_request(self, check_origin: bool = False) -> bool:
            port = self.server.server_port
            allowed_hosts = {f"127.0.0.1:{port}", f"localhost:{port}"}
            if self.headers.get("Host", "").lower() not in allowed_hosts:
                self._json(403, {"error": "Only local browser requests are allowed."})
                return False
            if check_origin:
                origin = self.headers.get("Origin")
                allowed_origins = {f"http://{host}" for host in allowed_hosts}
                if origin and origin not in allowed_origins:
                    self._json(403, {"error": "Cross-origin requests are not allowed."})
                    return False
            return True

        def do_GET(self):
            if not self._check_local_request():
                return
            path = urlparse(self.path).path
            if path in STATIC_FILES:
                filename, content_type = STATIC_FILES[path]
                content = (Path(__file__).parent / "static" / filename).read_bytes()
                self._write(200, content_type, content)
            elif path == "/api/history":
                self._json(200, {"history": [
                    {"created_at": ts, "status": status, "title": title, "url": url}
                    for ts, status, title, url in Store().history()
                ]})
            elif re.fullmatch(r"/generated/[a-f0-9]{32}\.(?:png|jpg|webp)", path):
                image_path = GENERATED_DIR / Path(path).name
                if not image_path.is_file():
                    self._json(404, {"error": "Generated image not found."})
                    return
                self._write(200, mimetypes.guess_type(image_path.name)[0] or "application/octet-stream",
                            image_path.read_bytes())
            else:
                self._json(404, {"error": "Not found."})

        def do_POST(self):
            if not self._check_local_request(check_origin=True):
                return
            try:
                try:
                    length = int(self.headers.get("Content-Length", "0"))
                except ValueError as e:
                    raise APIError("Invalid Content-Length header.") from e
                if length <= 0 or length > MAX_BODY_BYTES:
                    raise APIError("Request body must be between 1 byte and 3 MB.", 413)
                try:
                    data = json.loads(self.rfile.read(length))
                except (json.JSONDecodeError, UnicodeDecodeError) as e:
                    raise APIError("Request body must be valid JSON.") from e
                if not isinstance(data, dict):
                    raise APIError("Request body must be a JSON object.")
                path = urlparse(self.path).path
                if path == "/api/scan":
                    self._scan(data)
                elif path == "/api/job":
                    self._fetch_job(data)
                elif path == "/api/summarize":
                    self._summarize(data)
                elif path == "/api/generate-image":
                    self._generate_image(data)
                elif path == "/api/publish":
                    self._publish(data)
                elif path == "/api/history/delete":
                    self._delete_history(data)
                elif path == "/api/history/clear":
                    self._clear_history()
                else:
                    self._json(404, {"error": "Not found."})
            except APIError as e:
                self._json(e.status, {"error": str(e)})
            except Exception:
                log.exception("Web API request failed")
                self._json(500, {"error": "The request failed. Check the application logs."})

        def _scan(self, data: dict):
            url = str(data.get("url") or "").strip()
            try:
                max_items = max(1, min(int(data.get("max_items", 10)), 30))
            except (TypeError, ValueError) as e:
                raise APIError("Maximum jobs must be a number.") from e
            if not url:
                raise APIError("A listing or feed URL is required.")

            fetcher = Fetcher(cfg)
            try:
                links = collect_links({"type": "website", "url": url, "max_items": max_items}, fetcher)
            except Exception as e:
                self._json(200, {"jobs": [], "errors": [_error_item(url, e)]})
                return

            store = Store()
            jobs, errors = [], []
            for link in links:
                try:
                    final_url, page = fetcher.get(link)
                    job = parse_job(page, final_url)
                    if cfg["content"]["use_ai_summary"]:
                        if not cfg["env"]["gemini_key"]:
                            errors.append(_error_item(final_url, RuntimeError("Set GEMINI_API_KEY to use Gemini summaries.")))
                        else:
                            try:
                                job.summary = summarize(job, cfg, raise_errors=True)
                            except GeminiAPIError as e:
                                errors.append(_error_item(final_url, e))
                    jobs.append(_job_data(
                        job,
                        "already processed" if store.seen(final_url) else "ready",
                        cfg.get("blogger", {}).get("labels", []),
                    ))
                except Exception as e:
                    log.warning("Could not scan job %s: %s", link, type(e).__name__)
                    errors.append(_error_item(link, e))
            self._json(200, {"jobs": jobs, "errors": errors})

        def _fetch_job(self, data: dict):
            url = str(data.get("url") or "").strip()
            if not url:
                raise APIError("A job URL is required.")
            try:
                final_url, page = Fetcher(cfg).get(url)
            except FetchError as e:
                raise APIError(str(e), 422) from e
            job = parse_job(page, final_url)
            status = "already processed" if Store().seen(final_url) else "ready"
            self._json(200, {
                "job": _job_data(job, status, cfg.get("blogger", {}).get("labels", []))
            })

        def _summarize(self, data: dict):
            if not cfg["env"]["gemini_key"]:
                raise APIError("Set GEMINI_API_KEY in .env to use Gemini summaries.", 503)
            job = _job_from_data(data.get("job"))
            try:
                job.summary = summarize(job, cfg, raise_errors=True)
            except GeminiAPIError as e:
                raise APIError(str(e), 502) from e
            if not job.summary:
                raise APIError("Gemini returned an empty summary.", 502)
            self._json(200, {"summary": job.summary})

        def _generate_image(self, data: dict):
            job = _job_from_data(data.get("job"))
            try:
                mime_type, image_data = generate_image(job, cfg)
            except ValueError as e:
                raise APIError(str(e), 503) from e
            except RuntimeError as e:
                raise APIError(str(e), 502) from e
            suffix = {"image/png": "png", "image/jpeg": "jpg", "image/webp": "webp"}[mime_type]
            GENERATED_DIR.mkdir(parents=True, exist_ok=True)
            filename = f"{uuid.uuid4().hex}.{suffix}"
            (GENERATED_DIR / filename).write_bytes(image_data)
            self._json(200, {"image_url": f"/generated/{filename}"})

        def _publish(self, data: dict):
            job = _job_from_data(data.get("job"))
            try:
                job.source_url = validate_url(job.source_url, cfg["fetch"]["allowed_domains"] or None)
            except ValueError as e:
                raise APIError(f"Invalid source URL: {e}") from e
            store = Store()
            if store.seen(job.source_url):
                raise APIError("This job has already been processed.", 409)
            from .blogger import Blogger
            content = render_post(job, cfg["content"]["excerpt_chars"])
            title = post_title(job)
            labels = post_labels(job, cfg.get("blogger", {}).get("labels", []))
            description = search_description(job)
            try:
                post = Blogger(cfg).insert(title, content, labels, is_draft=True)
            except SystemExit as e:
                raise APIError(str(e), 503) from e
            except Exception as e:
                log.exception("Blogger draft creation failed")
                raise APIError(f"Blogger draft creation failed ({type(e).__name__}); check the application logs.", 502) from e
            store.add(job.source_url, title, "draft", post.get("id", ""))
            self._json(200, {
                "status": "draft",
                "title": post.get("title", title),
                "url": post.get("url", ""),
                "labels": post.get("labels", labels),
                "search_description": description,
                "search_description_requires_manual_entry": True,
            })

        def _delete_history(self, data: dict):
            url = str(data.get("url") or "").strip()
            if not url:
                raise APIError("A history URL is required.")
            deleted = Store().delete(url)
            self._json(200, {"deleted": deleted, "blogger_posts_affected": 0})

        def _clear_history(self):
            deleted = Store().clear_history()
            self._json(200, {"deleted": deleted, "blogger_posts_affected": 0})

        def log_message(self, fmt, *args):
            log.info("%s - %s", self.address_string(), fmt % args)

    return Handler


def serve(cfg: dict):
    server = ThreadingHTTPServer(("127.0.0.1", 8000), create_handler(cfg))
    server.daemon_threads = True
    log.info("JobBridge web interface: http://127.0.0.1:%s", server.server_port)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        log.info("Stopping the JobBridge web interface.")
    finally:
        server.server_close()
