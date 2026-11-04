"""Polite, size-limited, SSRF-safe HTTP fetcher with robots.txt support."""
import logging
import time
from urllib.parse import urljoin, urlparse
from urllib.robotparser import RobotFileParser
import requests
from .security import validate_url, UnsafeURL

log = logging.getLogger(__name__)
MAX_REDIRECTS = 5


class FetchError(Exception):
    pass


class Fetcher:
    def __init__(self, cfg: dict):
        f = cfg["fetch"]
        self.timeout = f["timeout_seconds"]
        self.max_bytes = f["max_bytes"]
        self.delay = f["delay_per_domain_seconds"]
        self.respect_robots = f["respect_robots_txt"]
        self.allowed = f["allowed_domains"] or None
        self.ua = cfg["env"]["user_agent"]
        self.session = requests.Session()
        self.session.headers.update({
            "User-Agent": self.ua,
            "Accept": "text/html,application/xhtml+xml,application/xml,application/rss+xml,application/atom+xml;q=0.9,*/*;q=0.5"})
        self._robots: dict[str, RobotFileParser | None] = {}
        self._last_hit: dict[str, float] = {}

    def _raw_get(self, url: str) -> tuple[str, int, str]:
        for _ in range(MAX_REDIRECTS + 1):
            url = validate_url(url, self.allowed)   # re-validated on EVERY redirect hop
            host = urlparse(url).netloc
            wait = self.delay - (time.time() - self._last_hit.get(host, 0))
            if wait > 0:
                time.sleep(wait)
            self._last_hit[host] = time.time()
            r = self.session.get(url, stream=True, timeout=self.timeout, allow_redirects=False)
            if r.is_redirect and r.headers.get("Location"):
                url = urljoin(url, r.headers["Location"])
                r.close()
                continue
            ctype = r.headers.get("Content-Type", "").lower()
            if r.status_code == 200 and not any(t in ctype for t in ("text/", "xml", "json")):
                r.close()
                raise FetchError(f"Unsupported content type: {ctype}")
            chunks, size = [], 0
            for chunk in r.iter_content(65536):
                size += len(chunk)
                if size > self.max_bytes:
                    r.close()
                    raise FetchError("Response too large")
                chunks.append(chunk)
            enc = r.encoding or "utf-8"
            return url, r.status_code, b"".join(chunks).decode(enc, errors="replace")
        raise FetchError("Too many redirects")

    def _robots_ok(self, url: str) -> bool:
        if not self.respect_robots:
            return True
        p = urlparse(url)
        base = f"{p.scheme}://{p.netloc}"
        if base not in self._robots:
            rp = RobotFileParser()
            try:
                _, status, body = self._raw_get(base + "/robots.txt")
                if status == 200:
                    rp.parse(body.splitlines())
                    self._robots[base] = rp
                elif 400 <= status < 500:
                    self._robots[base] = None          # no robots.txt -> allowed
                else:
                    rp.disallow_all = True
                    self._robots[base] = rp
            except (FetchError, UnsafeURL, requests.RequestException):
                rp.disallow_all = True                  # fail closed
                self._robots[base] = rp
        rp = self._robots[base]
        return True if rp is None else rp.can_fetch(self.ua, url)

    def get(self, url: str) -> tuple[str, str]:
        try:
            url = validate_url(url, self.allowed)
            if not self._robots_ok(url):
                raise FetchError(f"Blocked by robots.txt: {url}")
            final, status, body = self._raw_get(url)
        except (UnsafeURL, requests.RequestException) as e:
            raise FetchError(str(e)) from e
        if status != 200:
            raise FetchError(f"HTTP {status} for {final}")
        return final, body
