# Job2Blogger

Collects newly posted jobs from websites/feeds (and links you copy from a WhatsApp channel) and
creates posts on your Blogger blog — as **drafts by default** — with the full job description.
Only republish descriptions you own or have permission to reproduce.

## Quick start
```bash
python -m venv .venv && source .venv/bin/activate      # Windows: .venv\Scripts\activate
pip install -r requirements.txt
cp .env.example .env                                   # fill in BLOGGER_BLOG_ID, BOT_USER_AGENT
```

### 1. Blogger / Google setup (one time)
1. Google Cloud Console → create project → enable **Blogger API v3**.
2. Credentials → **OAuth client ID → Desktop app** → download JSON as `client_secret.json`.
3. OAuth consent screen: add your Google account as a *test user*.
4. Blog ID: Blogger dashboard URL `blogger.com/blog/posts/<BLOG_ID>`.
5. `python main.py auth` (browser login; saves `token.json`).

### 2. Run
```bash
python main.py run --url https://some-job-site.com/jobs --dry-run   # preview only
python main.py run --url https://some-job-site.com/feed             # creates DRAFTS
python main.py run --publish                                        # sources from config.yaml, go live
python main.py run --job-url https://site.com/jobs/123              # single job page
python main.py run --whatsapp-file data/channel.txt                 # links from pasted WhatsApp posts
python main.py history
python main.py web                                                  # local web interface at 127.0.0.1:8000
```
Schedule it with cron / Task Scheduler, e.g. `0 */3 * * * cd /path && .venv/bin/python main.py run`.

### WhatsApp channels — what is and isn't possible
WhatsApp has **no official API for reading Channel posts**, and automated scraping breaks its Terms.
Channel links (`whatsapp.com/channel/...`) expose no posts. Workflow: copy the channel messages
(select → copy, or paste into a text file) into `data/channel.txt`; the tool extracts the job links
and processes them like any other URL. Only add channels you're allowed to use.

## Optional AI summaries (Gemini)
Put your key in `.env` as `GEMINI_API_KEY=...`, optionally set `AI_MODEL` (default `gemini-3.5-flash-lite`), then set
`content.use_ai_summary: true` in `config.yaml`. In the web interface you can also request a summary per job.
The default summary model is `gemini-3.5-flash-lite`. Image generation uses Gemini's
`gemini-nano-banana-2.1` Interactions API model by default; override it with `AI_IMAGE_MODEL`.

## Local web interface
Run `python main.py web` and open `http://127.0.0.1:8000`. Enter a public listing/feed URL to inspect jobs
individually, including extracted page links, image URLs, and available role details. You can request a
Gemini summary or generated illustration for a job, and save it as a Blogger draft. The interface listens
on loopback only; it does not automatically publish posts. Source pages are fetched with the same
robots.txt and SSRF protections as the CLI.

## How it works
`sources → discover links (RSS/Atom, else heuristic page links) → skip already-seen (SQLite) →
fetch job page → parse schema.org JobPosting JSON-LD (fallback: OpenGraph/text) → [optional Gemini
summary] → embed available job-ad image → render full description → sanitize → Blogger API`.

The job-ad image is selected from JobPosting or Open Graph/Twitter metadata, or a clearly
job-related image URL; unrelated site logos and tracking pixels are not used as the advertisement.

## Security design
| Risk | Mitigation |
|---|---|
| SSRF via malicious links | http/https only, ports 80/443, DNS resolved & private/loopback/link-local IPs rejected, re-checked on each redirect, optional domain allow-list |
| Huge / hostile responses | Timeouts, 2 MB cap, content-type check |
| XSS / injected HTML in posts | All scraped fields HTML-escaped; output passed through `bleach` allow-list |
| Prompt injection (AI step) | Scraped text wrapped as untrusted data; model has no tools; output is escaped plain text |
| Secret leakage | Secrets in `.env` / `token.json` (chmod 600, git-ignored); least-privilege OAuth only |
| Abuse of other sites | robots.txt respected (fail-closed), per-domain delay, honest User-Agent |
| Accidental bad posts | Drafts by default, dedupe DB, `--dry-run` |
Residual: DNS-rebinding between check and connect is possible in theory; run on a host with egress
filtering for stronger isolation.

## Legal / ethics checklist
Job text is usually copyrighted. Get permission from rights holders before republishing full
descriptions, honor source terms, and verify employers before publishing. Scam awareness: never pay
money to apply for a job.

## Tests
`pytest -q`
