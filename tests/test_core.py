import pytest
from jobbot.security import validate_url, sanitize_html, UnsafeURL
from jobbot.extractors import parse_job, links_from_listing, links_from_feed, is_feed
from jobbot.render import render_post
from jobbot.whatsapp import extract_links
from jobbot.storage import Store, normalize


@pytest.mark.parametrize("u", ["file:///etc/passwd", "ftp://x.com", "http://127.0.0.1/", "http://localhost/",
                               "http://169.254.169.254/latest/meta-data", "http://10.0.0.5/", "http://[::1]/",
                               "http://user:pw@example.com/", "http://example.com:8080/"])
def test_ssrf_blocked(u):
    with pytest.raises(UnsafeURL):
        validate_url(u)


def test_sanitize_strips_scripts():
    out = sanitize_html('<p onclick="x()">Hi</p><script>alert(1)</script><a href="javascript:evil()">a</a>')
    assert "<script" not in out and "onclick" not in out and "javascript:" not in out


JOB_HTML = """<html><head><title>x</title><script type="application/ld+json">
{"@context":"https://schema.org","@type":"JobPosting","title":"Python Dev","description":"<p>Build APIs</p><img src=x onerror=bad()>",
"hiringOrganization":{"name":"Acme"},"datePosted":"2026-11-01",
"jobLocation":{"address":{"addressLocality":"Lahore","addressCountry":"PK"}}}</script></head><body></body></html>"""


def test_jsonld_and_render_escapes():
    job = parse_job(JOB_HTML, "https://acme.test/jobs/1")
    assert job.title == "Python Dev" and job.company == "Acme" and "Lahore" in job.location
    job.title = "<b>x</b>"
    job.company = '<img src=x onerror=alert(1)>'
    html = render_post(job)
    assert "onerror" not in html
    assert "<b>x</b>" not in html
    assert "https://acme.test/jobs/1" not in html


def test_job_logo_dates_and_full_description_render_without_metadata_or_source():
    page = """<html><head><script type="application/ld+json">
    {"@type":"JobPosting","title":"Sales Executive","datePosted":"2026-11-04T09:49:36+05:30",
     "validThrough":"2027-02-02T23:59:59+05:30",
     "hiringOrganization":{"name":"WAD International","logo":{"@type":"ImageObject","url":"/images/wad-logo.png"}},
     "image":"/job_images/sales-ad.png",
     "description":"<p>First paragraph with the complete role details.</p><ul><li>Second detail.</li></ul>"}
    </script></head><body></body></html>"""
    job = parse_job(page, "https://itpro.lk/job/1")
    post = render_post(job)
    assert job.company_logo_url == "https://itpro.lk/images/wad-logo.png"
    assert job.advertisement_image_url == "https://itpro.lk/job_images/sales-ad.png"
    assert post.index("<img") < post.index("Posted:")
    assert 'src="https://itpro.lk/images/wad-logo.png"' in post
    assert 'src="https://itpro.lk/job_images/sales-ad.png"' in post
    assert post.index('alt="WAD International logo"') < post.index('alt="Job advertisement"')
    assert "Posted:</strong> 2026-11-04" in post
    assert "Apply before:</strong> 2027-02-02" in post
    assert "First paragraph with the complete role details." in post
    assert "<li>Second detail.</li>" in post
    assert "Company:" not in post and "Location:" not in post and "Type:" not in post
    assert "View full details" not in post and "Source:" not in post
    assert "https://itpro.lk/job/1" not in post


def test_web_job_round_trip_preserves_full_sanitized_description():
    from jobbot.web import _job_data, _job_from_data
    job = parse_job(
        """<script type="application/ld+json">
        {"@type":"JobPosting","title":"Developer","hiringOrganization":{"name":"Acme","logo":"/acme.png"},
         "description":"<p>First detail.</p><ul><li>Second detail.</li></ul><a href='javascript:alert(1)'>Apply</a>"}
        </script>""",
        "https://jobs.test/job/1",
    )
    serialized = _job_data(job)
    restored = _job_from_data(serialized)
    post = render_post(restored)
    assert "First detail." in post and "<li>Second detail.</li>" in post
    assert "javascript:" not in post
    assert restored.company_logo_url == "https://jobs.test/acme.png"


def test_listing_and_feed():
    page = '<a href="/jobs/senior-engineer-123">Senior Engineer opening</a><a href="/about">About us page</a>'
    assert links_from_listing(page, "https://a.test/", 5) == ["https://a.test/jobs/senior-engineer-123"]
    feed = '<?xml version="1.0"?><rss version="2.0"><channel><item><title>t</title><link>https://a.test/j/1</link></item></channel></rss>'
    assert is_feed(feed) and links_from_feed(feed, 5) == ["https://a.test/j/1"]


def test_job_assets_are_extracted_as_absolute_http_urls():
    page = """<html><body>
    <img src="/images/role.png" srcset="/images/role-small.png 1x, https://cdn.test/role-large.png 2x">
    <a href="/apply">Apply now</a><a href="mailto:jobs@example.test">Email</a>
    <a href="javascript:alert(1)">Unsafe</a>
    </body></html>"""
    job = parse_job(page, "https://jobs.test/openings/1")
    assert job.images == [
        "https://jobs.test/images/role.png",
        "https://jobs.test/images/role-small.png",
        "https://cdn.test/role-large.png",
    ]
    assert job.links == [{"text": "Apply now", "url": "https://jobs.test/apply"}]


def test_job_advertisement_image_uses_og_image_not_unrelated_page_images():
    page = """<html><head>
    <meta property="og:image" content="/job_images/sales-ad.png">
    </head><body><img src="/assets/site-logo.svg" alt="Site logo"></body></html>"""
    job = parse_job(page, "https://jobs.test/jobs/1")
    assert job.advertisement_image_url == "https://jobs.test/job_images/sales-ad.png"
    assert job.advertisement_image_url != job.images[0]


def test_whatsapp_links():
    t = "Hiring! https://acme.test/jobs/1. Join https://whatsapp.com/channel/abc and https://acme.test/jobs/1"
    assert extract_links(t) == ["https://acme.test/jobs/1"]


def test_dedupe(tmp_path):
    s = Store(str(tmp_path / "t.db"))
    s.add("https://A.test/x/", "t", "draft")
    assert s.seen("https://a.test/x") and normalize("https://A.test/x/") == "https://a.test/x"


def test_blogger_update_uses_existing_resource_metadata():
    from jobbot.blogger import Blogger

    calls = {}

    class Request:
        def execute(self):
            return {"id": "post-1", "status": "DRAFT"}

    class Posts:
        def update(self, **kwargs):
            calls.update(kwargs)
            return Request()

    class Service:
        def posts(self):
            return Posts()

    blogger = object.__new__(Blogger)
    blogger.blog_id = "blog-1"
    blogger.svc = Service()
    existing_post = {"id": "post-1", "title": "New title", "content": "<p>Body</p>", "labels": ["Jobs"]}
    result = blogger.update(existing_post)
    assert result["status"] == "DRAFT"
    assert calls == {"blogId": "blog-1", "postId": "post-1", "body": existing_post}


def test_gemini_summary_uses_header_and_parses(monkeypatch):
    from jobbot import ai
    from jobbot.extractors import Job
    seen = {}

    class R:
        ok = True
        def raise_for_status(self): pass
        def json(self): return {"candidates": [{"content": {"parts": [{"text": "Short summary."}]}}]}

    def fake_post(url, json=None, timeout=None, headers=None):
        seen.update(url=url, headers=headers)
        return R()

    monkeypatch.setattr(ai.requests, "post", fake_post)
    cfg = {"env": {"gemini_key": "SECRET", "ai_model": "gemini-2.5-flash"}}
    out = ai.summarize(Job(title="t", source_url="https://a.test/1", description_html="<p>x</p>"), cfg)
    assert out == "Short summary." and "SECRET" not in seen["url"] and seen["headers"]["x-goog-api-key"] == "SECRET"
    assert ai.summarize(Job(title="t", source_url="u"), {"env": {"gemini_key": "", "ai_model": "m"}}) == ""


def test_gemini_image_generation_parses_inline_image(monkeypatch):
    import base64
    from jobbot import ai
    from jobbot.extractors import Job
    seen = {}

    class R:
        ok = True
        def json(self):
            return {"output_image": {
                "mime_type": "image/png",
                "data": base64.b64encode(b"image-bytes").decode(),
            }}

    def fake_post(url, json=None, timeout=None, headers=None):
        seen.update(url=url, json=json, headers=headers)
        return R()

    monkeypatch.setattr(ai.requests, "post", fake_post)
    mime, content = ai.generate_image(
        Job(title="Python Developer", source_url="https://a.test/1"),
        {"env": {"gemini_key": "SECRET", "ai_image_model": "gemini-nano-banana-2.1"}},
    )
    assert mime == "image/png" and content == b"image-bytes"
    assert seen["headers"]["x-goog-api-key"] == "SECRET"
    assert "SECRET" not in seen["url"] and seen["url"].endswith("/v1beta/interactions")
    assert seen["json"]["model"] == "gemini-nano-banana-2.1"
    assert "Python Developer" in seen["json"]["input"]
    assert seen["headers"]["Api-Revision"] == "2026-05-20"


def test_gemini_http_errors_are_actionable_and_redacted(monkeypatch, caplog):
    from jobbot import ai
    from jobbot.extractors import Job

    class R:
        ok = False
        status_code = 403
        def json(self): return {"error": {"message": "API key SECRET is not authorized"}}

    monkeypatch.setattr(ai.requests, "post", lambda *args, **kwargs: R())
    job = Job(title="Python developer", source_url="https://a.test/1", description_html="<p>Build APIs</p>")
    cfg = {"env": {"gemini_key": "SECRET", "ai_model": "gemini-3.5-flash-lite"}}

    with pytest.raises(ai.GeminiAPIError, match=r"HTTP 403") as error:
        ai.summarize(job, cfg, raise_errors=True)
    assert "SECRET" not in str(error.value)
    with pytest.raises(ai.GeminiAPIError, match=r"HTTP 403") as error:
        ai.generate_image(
            job,
            {"env": {"gemini_key": "SECRET", "ai_image_model": "gemini-nano-banana-2.1"}},
        )
    assert "SECRET" not in str(error.value)
    assert "SECRET" not in caplog.text
