import json
import threading
from http.server import ThreadingHTTPServer
from urllib.request import Request, urlopen

import pytest

from jobbot.security import validate_url, sanitize_html, UnsafeURL
from jobbot.extractors import parse_job, links_from_listing, links_from_feed, is_feed
from jobbot.render import render_post
from jobbot.post_metadata import job_category, post_labels, post_title, search_description
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
    assert "style=" not in sanitize_html('<p style="color:red;background:url(javascript:alert(1))">Safe text</p>')


@pytest.mark.parametrize("weight", ["bold", "bolder", "600", "700", "900", "700 !important"])
def test_sanitizer_preserves_bold_inline_style_as_safe_strong(weight):
    out = sanitize_html(f'<p>Normal <span style="font-weight: {weight}; color:red" onclick="bad()">emphasis</span></p>')
    assert "<strong>emphasis</strong>" in out
    assert "font-weight" not in out and "color:red" not in out and "onclick" not in out


def test_sanitizer_keeps_semantic_bold_tags_and_ignores_normal_weight():
    out = sanitize_html('<p><b>Bold B</b> <strong>Bold strong</strong> <span style="font-weight:400">Regular</span></p>')
    assert "<b>Bold B</b>" in out
    assert "<strong>Bold strong</strong>" in out
    assert "<span>Regular</span>" in out


@pytest.mark.parametrize("class_name", ["bold", "text-bold", "fw-bold", "font-weight-bold"])
def test_sanitizer_preserves_bold_class_as_safe_strong(class_name):
    out = sanitize_html(f'<span class="{class_name}">Emphasis</span>')
    assert out == "<strong>Emphasis</strong>"


def test_sanitizer_decodes_encoded_bold_markup_before_sanitizing():
    out = sanitize_html("&lt;strong&gt;Encoded emphasis&lt;/strong&gt;")
    assert out == "<strong>Encoded emphasis</strong>"


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
    assert "<img src=x onerror" not in html
    assert "&lt;b&gt;x&lt;/b&gt;" in html
    assert "<b>x</b>" not in html
    assert "https://acme.test/jobs/1" not in html
    assert 'font-family:Montserrat,Arial,Helvetica,sans-serif' in html


def test_job_logo_dates_and_full_description_render_without_metadata_or_source():
    page = """<html><head><script type="application/ld+json">
    {"@type":"JobPosting","title":"Sales Executive","datePosted":"2026-11-04T09:49:36+05:30",
     "validThrough":"2027-02-02T23:59:59+05:30",
     "employmentType":"FULL_TIME",
     "jobLocation":{"@type":"Place","address":{"addressLocality":"Colombo","addressCountry":"LK"}},
     "hiringOrganization":{"name":"WAD International","logo":{"@type":"ImageObject","url":"/images/wad-logo.png"}},
     "image":"/job_images/sales-ad.png",
     "description":"<p>First paragraph with the complete role details.</p><ul><li>Second detail.</li></ul>"}
    </script></head><body></body></html>"""
    job = parse_job(page, "https://itpro.lk/job/1")
    post = render_post(job)
    assert job.company_logo_url == "https://itpro.lk/images/wad-logo.png"
    assert job.advertisement_image_url == "https://itpro.lk/job_images/sales-ad.png"
    assert post.index("<img") < post.index(">Posted</span>")
    assert 'src="https://itpro.lk/images/wad-logo.png"' in post
    assert 'src="https://itpro.lk/job_images/sales-ad.png"' not in post
    assert 'alt="Job advertisement"' not in post
    assert ">Posted</span>" in post and ">2026-11-04</strong>" in post
    assert ">Job position</span>" in post and ">Sales Executive</strong>" in post
    assert ">Location</span>" in post and ">Colombo, LK</strong>" in post
    assert ">Employment type</span>" in post and ">Full Time</strong>" in post
    assert ">Company</span>" not in post
    assert "Apply before" not in post and "2027-02-02" not in post
    assert "family=Montserrat" in post and "font-family:Montserrat,Arial,Helvetica,sans-serif" in post
    assert "Job description</h2>" in post
    assert "background-color:#f3f8f4" in post
    assert "First paragraph with the complete role details." in post
    assert "<li>Second detail.</li>" in post
    assert "Company:" not in post and "Type:" not in post
    assert "View full details" not in post and "Source:" not in post
    assert "https://itpro.lk/job/1" not in post


def test_web_job_round_trip_preserves_full_sanitized_description():
    from jobbot.web import _job_data, _job_from_data
    job = parse_job(
        """<script type="application/ld+json">
        {"@type":"JobPosting","title":"Developer","hiringOrganization":{"@type":"GovernmentOrganization","name":"Acme","logo":"/acme.png"},
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
    assert restored.organization_type == "GovernmentOrganization"
    assert serialized["suggested_labels"][-1] == "Government Jobs"
    assert serialized["blogger_title"] == "Developer - Acme"
    assert serialized["search_description"] == "Government Jobs: Developer at Acme"


def test_blogger_template_preserves_bold_description_text_through_web_round_trip():
    from jobbot.web import _job_data, _job_from_data
    job = parse_job(
        """<script type="application/ld+json">
        {"@type":"JobPosting","title":"Developer","description":
         "<p><strong>Required qualifications</strong></p><p><span class='fw-bold'>Five years experience</span></p><p>&lt;strong&gt;Encoded requirement&lt;/strong&gt;</p>"}
        </script>""",
        "https://jobs.test/job/1",
    )
    restored = _job_from_data(_job_data(job))
    post = render_post(restored)
    assert "<strong>Required qualifications</strong>" in post
    assert "<strong>Five years experience</strong>" in post
    assert "<strong>Encoded requirement</strong>" in post
    assert "font-weight:700" not in post


def test_job_blogger_metadata_includes_company_position_and_job_category():
    from jobbot.extractors import Job

    job = Job(
        title="Data Analyst",
        source_url="https://careers.example.com/analyst",
        company="Ministry of Health",
        organization_type="GovernmentOrganization",
    )
    labels = post_labels(job, ["Jobs", "Public Roles"])
    description = search_description(job)
    assert labels == ["Public Roles", "Ministry of Health", "Data Analyst", "Government Jobs"]
    assert description == "Government Jobs: Data Analyst at Ministry of Health"
    assert job_category(job) == "Government Jobs"


def test_post_template_always_shows_all_three_information_fields():
    from jobbot.extractors import Job

    job = Job(title="Office Assistant", source_url="https://jobs.example.com/office")
    post = render_post(job)
    assert ">Posted</span>" in post and ">Not specified</strong>" in post
    assert ">Job position</span>" in post and ">Office Assistant</strong>" in post
    assert ">Location</span>" in post
    assert ">Employment type</span>" in post
    assert post.count(">Not specified</strong>") == 3


def test_job_blogger_metadata_defaults_to_private_for_unmarked_employers():
    from jobbot.extractors import Job

    job = Job(
        title="Software Engineer",
        source_url="https://jobs.example.com/engineer",
        company="Acme Ltd",
    )
    assert job_category(job) == "Private Jobs"
    assert "Private Jobs" in post_labels(job)
    assert search_description(job) == "Private Jobs: Software Engineer at Acme Ltd"


def test_blogger_title_uses_position_and_company():
    from jobbot.extractors import Job

    job = Job(
        title="  Senior Engineer ",
        source_url="https://jobs.example.com/engineer",
        company=" Acme Ltd ",
    )
    assert post_title(job) == "Senior Engineer - Acme Ltd"


def test_blogger_title_falls_back_to_available_value_and_respects_blogger_limit():
    from jobbot.extractors import Job

    without_company = Job(title="Data Analyst", source_url="https://jobs.example.com/analyst")
    without_position = Job(title="", source_url="https://jobs.example.com/analyst", company="Acme Ltd")
    long_title = Job(title="P" * 190, source_url="https://jobs.example.com/analyst", company="C" * 30)
    assert post_title(without_company) == "Data Analyst"
    assert post_title(without_position) == "Acme Ltd"
    assert len(post_title(long_title)) == 200


def test_post_labels_remove_generic_jobs_label_when_specific_labels_exist():
    from jobbot.extractors import Job

    job = Job(
        title="Software Engineer",
        source_url="https://jobs.example.com/engineer",
        company="Acme Ltd",
    )
    labels = post_labels(job, ["Jobs", "Engineering"])
    assert labels == ["Engineering", "Acme Ltd", "Software Engineer", "Private Jobs"]
    assert all(label.casefold() != "jobs" for label in labels)


def test_post_labels_remove_case_insensitive_generic_jobs_label():
    from jobbot.extractors import Job

    job = Job(
        title="Data Analyst",
        source_url="https://jobs.example.com/analyst",
        company="Acme",
    )
    assert all(label.casefold() != "jobs" for label in post_labels(job, ["jobs"]))


@pytest.mark.parametrize(
    "employment_type",
    ["Intern", "INTERNSHIP", "Internships", "Intern - Full Time"],
)
def test_post_labels_add_internships_for_intern_employment_type(employment_type):
    from jobbot.extractors import Job

    job = Job(
        title="Software Engineer Intern",
        source_url="https://jobs.example.com/intern",
        company="Acme Ltd",
        employment_type=employment_type,
    )
    labels = post_labels(job, ["Jobs"])
    assert labels == ["Acme Ltd", "Software Engineer Intern", "Private Jobs", "Internships"]
    assert labels.count("Internships") == 1
    assert all(label.casefold() != "jobs" for label in labels)


def test_post_labels_do_not_add_internships_for_non_intern_employment_type():
    from jobbot.extractors import Job

    job = Job(
        title="Software Engineer",
        source_url="https://jobs.example.com/engineer",
        company="Acme Ltd",
        employment_type="FULL_TIME",
    )
    assert "Internships" not in post_labels(job, ["Jobs"])


def test_internship_label_survives_job_json_round_trip():
    from jobbot.web import _job_data, _job_from_data

    job = parse_job(
        """<script type="application/ld+json">
        {"@type":"JobPosting","title":"Marketing Intern","employmentType":"INTERN",
         "hiringOrganization":{"name":"FAITE"},"description":"<p>Intern role</p>"}
        </script>""",
        "https://jobs.example.com/marketing-intern",
    )
    serialized = _job_data(job, configured_labels=["Jobs"])
    restored = _job_from_data(serialized)
    labels = post_labels(restored, ["Jobs"])
    assert serialized["employment_type"] == "INTERN"
    assert serialized["suggested_labels"] == ["FAITE", "Marketing Intern", "Private Jobs", "Internships"]
    assert labels == serialized["suggested_labels"]


def test_search_description_is_limited_and_includes_job_fields():
    from jobbot.extractors import Job

    job = Job(
        title="Senior " + "Platform Engineer " * 8,
        source_url="https://jobs.example.com/engineer",
        company="A Very Large " + "Technology Company " * 8,
    )
    description = search_description(job)
    assert len(description) <= 150
    assert description.startswith("Private Jobs:")
    assert " at " in description


def test_listing_and_feed():
    page = '<a href="/jobs/senior-engineer-123">Senior Engineer opening</a><a href="/about">About us page</a>'
    assert links_from_listing(page, "https://a.test/", 5) == ["https://a.test/jobs/senior-engineer-123"]
    feed = '<?xml version="1.0"?><rss version="2.0"><channel><item><title>t</title><link>https://a.test/j/1</link></item></channel></rss>'
    assert is_feed(feed) and links_from_feed(feed, 5) == ["https://a.test/j/1"]


def test_listing_links_are_ordered_by_posted_date_not_keyword_score():
    page = """
    <article class="job-listing"><time datetime="2026-10-01">Oct 1</time>
      <a href="/jobs/new-role">New role with a long title</a></article>
    <article class="job-listing"><time datetime="2026-11-04">Nov 4</time>
      <a href="/jobs/opening/job-123">Older keyword-heavy job opening</a></article>
    """
    assert links_from_listing(page, "https://jobs.test/jobs", 2) == [
        "https://jobs.test/jobs/opening/job-123",
        "https://jobs.test/jobs/new-role",
    ]


def test_listing_links_keep_page_order_when_dates_are_missing():
    page = """
    <a href="/jobs/new-role">Newest role on page</a>
    <a href="/jobs/older-role">Older role on page</a>
    """
    assert links_from_listing(page, "https://jobs.test/jobs", 2) == [
        "https://jobs.test/jobs/new-role",
        "https://jobs.test/jobs/older-role",
    ]


def test_feed_links_are_sorted_by_published_date():
    feed = """<?xml version="1.0"?><rss version="2.0"><channel>
      <item><title>New role</title><link>https://jobs.test/new</link><pubDate>Wed, 04 Nov 2026 10:00:00 +0000</pubDate></item>
      <item><title>Old role</title><link>https://jobs.test/old</link><pubDate>Tue, 03 Nov 2026 10:00:00 +0000</pubDate></item>
    </channel></rss>"""
    assert links_from_feed(feed, 2) == ["https://jobs.test/new", "https://jobs.test/old"]


def test_fallback_job_description_keeps_full_formatted_article():
    long_details = "Complete requirement. " * 400
    page = f"""<html><body>
      <nav>Navigation text must not be copied.</nav>
      <article><h1>Senior Engineer</h1><div class="job-description">
        <p>{long_details}</p><ul><li>Five years of experience</li></ul>
      </div><footer>Site footer must not be copied.</footer></article>
    </body></html>"""
    job = parse_job(page, "https://jobs.test/job/1")
    assert len(job.description_html) > 6000
    assert "Five years of experience" in job.description_html
    assert "Navigation text" not in job.description_html
    assert "Site footer" not in job.description_html


def test_web_scan_fetches_discovered_jobs_in_order_and_keeps_full_descriptions(monkeypatch, tmp_path):
    from jobbot import web

    urls = ["https://jobs.test/newest", "https://jobs.test/next"]
    description = "Complete job responsibility. " * 400
    pages = {
        urls[0]: f'<script type="application/ld+json">{{"@type":"JobPosting","title":"Newest","description":"<p>{description}</p>"}}</script>',
        urls[1]: f'<script type="application/ld+json">{{"@type":"JobPosting","title":"Next","description":"<p>{description}</p>"}}</script>',
    }

    class FakeFetcher:
        def __init__(self):
            self.requested = []

        def get(self, url):
            self.requested.append(url)
            return url, pages[url]

    fetcher = FakeFetcher()
    monkeypatch.setattr(web, "Fetcher", lambda cfg: fetcher)
    monkeypatch.setattr(web, "collect_links", lambda source, client: urls)
    database_path = str(tmp_path / "scan-history.db")
    monkeypatch.setattr(web, "Store", lambda: Store(database_path))
    cfg = {
        "content": {"use_ai_summary": False},
        "env": {"gemini_key": ""},
        "fetch": {},
        "blogger": {"labels": ["Jobs"]},
    }
    server = ThreadingHTTPServer(("127.0.0.1", 0), web.create_handler(cfg))
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    base_url = f"http://127.0.0.1:{server.server_port}"
    try:
        request = Request(
            base_url + "/api/scan",
            data=json.dumps({"url": "https://jobs.test/listing", "max_items": "2"}).encode(),
            headers={"Content-Type": "application/json", "Origin": base_url},
            method="POST",
        )
        with urlopen(request) as response:
            result = json.loads(response.read())
        assert fetcher.requested == urls
        assert [job["title"] for job in result["jobs"]] == ["Newest", "Next"]
        assert all(len(job["description"]) > 6000 for job in result["jobs"])
        assert all("Jobs" not in job["suggested_labels"] for job in result["jobs"])
    finally:
        server.shutdown()
        server.server_close()
        thread.join()


def test_web_new_post_publishes_template_and_job_labels(monkeypatch, tmp_path):
    from jobbot import blogger, web

    captured = {}

    class FakeBlogger:
        def __init__(self, cfg):
            pass

        def insert(self, title, content, labels, is_draft=True):
            captured.update(title=title, content=content, labels=labels, is_draft=is_draft)
            return {"id": "post-123", "url": "https://blog.example/post", "labels": labels}

    monkeypatch.setattr(blogger, "Blogger", FakeBlogger)
    monkeypatch.setattr(web, "validate_url", lambda url, allowed_domains=None: url)
    monkeypatch.setattr(web, "Store", lambda: Store(str(tmp_path / "publish-history.db")))
    cfg = {
        "content": {"excerpt_chars": 600},
        "fetch": {"allowed_domains": []},
        "blogger": {"labels": ["Jobs"]},
    }
    server = ThreadingHTTPServer(("127.0.0.1", 0), web.create_handler(cfg))
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    base_url = f"http://127.0.0.1:{server.server_port}"
    job = {
        "title": "Public Health Inspector",
        "source_url": "https://jobs.example.com/inspector",
        "company": "Ministry of Health",
        "organization_type": "GovernmentOrganization",
        "employment_type": "FULL_TIME",
        "date_posted": "2026-11-04",
        "valid_through": "2027-02-02",
        "description_html": "<p>Public service role.</p>",
    }
    try:
        request = Request(
            base_url + "/api/publish",
            data=json.dumps({"job": job}).encode(),
            headers={"Content-Type": "application/json", "Origin": base_url},
            method="POST",
        )
        with urlopen(request) as response:
            result = json.loads(response.read())
        assert captured["is_draft"] is True
        assert captured["title"] == "Public Health Inspector - Ministry of Health"
        assert captured["labels"] == [
            "Ministry of Health", "Public Health Inspector", "Government Jobs"
        ]
        assert ">Posted</span>" in captured["content"]
        assert ">2026-11-04</strong>" in captured["content"]
        assert ">Job position</span>" in captured["content"]
        assert ">Public Health Inspector</strong>" in captured["content"]
        assert ">Employment type</span>" in captured["content"]
        assert ">Full Time</strong>" in captured["content"]
        assert "Apply before" not in captured["content"]
        assert result["labels"] == captured["labels"]
        assert result["title"] == captured["title"]
        assert result["search_description"] == "Government Jobs: Public Health Inspector at Ministry of Health"
        assert result["search_description_requires_manual_entry"] is True
    finally:
        server.shutdown()
        server.server_close()
        thread.join()


def test_pipeline_sends_automatic_job_labels_to_blogger(monkeypatch, tmp_path):
    from jobbot import blogger, pipeline

    captured = {}
    page = """<script type="application/ld+json">
    {"@type":"JobPosting","title":"Systems Analyst","employmentType":"FULL_TIME",
     "hiringOrganization":{"@type":"GovernmentOrganization","name":"National Water Board"},
     "description":"<p>Government role.</p>"}
    </script>"""

    class FakeFetcher:
        def __init__(self, cfg):
            pass

        def get(self, url):
            return url, page

    class FakeBlogger:
        def __init__(self, cfg):
            pass

        def insert(self, title, content, labels, is_draft=True):
            captured.update(title=title, labels=labels, is_draft=is_draft)
            return {"id": "post-456", "url": "https://blog.example/analyst"}

    monkeypatch.setattr(pipeline, "Fetcher", FakeFetcher)
    monkeypatch.setattr(blogger, "Blogger", FakeBlogger)
    monkeypatch.setattr(pipeline, "Store", lambda: Store(str(tmp_path / "pipeline-history.db")))
    cfg = {
        "blogger": {"labels": ["Jobs", "Sri Lanka Jobs"], "publish_immediately": False},
        "fetch": {},
        "content": {"use_ai_summary": False, "excerpt_chars": 600},
    }

    result = pipeline.run(cfg, [{"type": "url", "url": "https://jobs.example/analyst"}])
    assert captured == {
        "title": "Systems Analyst - National Water Board",
        "labels": ["Sri Lanka Jobs", "National Water Board", "Systems Analyst", "Government Jobs"],
        "is_draft": True,
    }
    assert result[0]["labels"] == captured["labels"]
    assert result[0]["status"] == "draft"


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


def test_history_delete_and_clear_only_remove_local_records(tmp_path):
    store = Store(str(tmp_path / "history.db"))
    store.add("https://jobs.test/one", "One", "draft", "blogger-1")
    store.add("https://jobs.test/two", "Two", "published", "blogger-2")

    assert store.delete("https://JOBS.test/one/") == 1
    assert not store.seen("https://jobs.test/one")
    assert store.seen("https://jobs.test/two")
    assert store.clear_history() == 1
    assert store.history() == []
    assert store.clear_history() == 0


def test_history_delete_and_clear_api_affect_local_store_only(monkeypatch, tmp_path):
    from jobbot import web

    database_path = str(tmp_path / "api-history.db")
    store = Store(database_path)
    store.add("https://jobs.test/one", "One", "draft", "blogger-1")
    store.add("https://jobs.test/two", "Two", "draft", "blogger-2")
    monkeypatch.setattr(web, "Store", lambda: Store(database_path))
    cfg = {"content": {}, "env": {}, "fetch": {}, "blogger": {}}
    server = ThreadingHTTPServer(("127.0.0.1", 0), web.create_handler(cfg))
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    base_url = f"http://127.0.0.1:{server.server_port}"
    try:
        delete_request = Request(
            base_url + "/api/history/delete",
            data=json.dumps({"url": "https://jobs.test/one"}).encode(),
            headers={"Content-Type": "application/json", "Origin": base_url},
            method="POST",
        )
        with urlopen(delete_request) as response:
            deleted = json.loads(response.read())
        assert deleted == {"deleted": 1, "blogger_posts_affected": 0}
        assert Store(database_path).seen("https://jobs.test/two")

        clear_request = Request(
            base_url + "/api/history/clear",
            data=b"{}",
            headers={"Content-Type": "application/json", "Origin": base_url},
            method="POST",
        )
        with urlopen(clear_request) as response:
            cleared = json.loads(response.read())
        assert cleared == {"deleted": 1, "blogger_posts_affected": 0}
        assert Store(database_path).history() == []
    finally:
        server.shutdown()
        server.server_close()
        thread.join()


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


def test_blogger_insert_sends_job_labels_as_blogger_labels():
    from jobbot.blogger import Blogger

    calls = {}

    class Request:
        def execute(self):
            return {"id": "post-1", "labels": calls["body"]["labels"]}

    class Posts:
        def insert(self, **kwargs):
            calls.update(kwargs)
            return Request()

    class Service:
        def posts(self):
            return Posts()

    blogger = object.__new__(Blogger)
    blogger.blog_id = "blog-1"
    blogger.svc = Service()
    labels = ["Acme Ltd", "Engineer", "Private Jobs"]
    result = blogger.insert("Engineer", "<p>Body</p>", labels)
    assert calls["blogId"] == "blog-1"
    assert calls["isDraft"] is True
    assert calls["body"]["labels"] == labels
    assert result["labels"] == labels


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
