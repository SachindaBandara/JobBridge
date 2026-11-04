#!/usr/bin/env python3
"""Job2Blogger CLI.

  python main.py auth                         # one-time Google login
  python main.py run --dry-run                # preview, nothing is posted
  python main.py run                          # create DRAFT posts from config.yaml sources
  python main.py run --publish                # publish immediately
  python main.py run --url https://site/jobs  # ad-hoc website/feed link(s)
  python main.py run --whatsapp-file data/channel.txt
  python main.py web                         # local browser interface
  python main.py history
"""
import argparse
import logging
from jobbot.config import load_config


def main():
    ap = argparse.ArgumentParser(description="Add job posts from other sites to your Blogger blog")
    ap.add_argument("--config", default="config.yaml")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("auth")
    sub.add_parser("history")
    sub.add_parser("web", help="start the local web interface")
    r = sub.add_parser("run")
    r.add_argument("--url", action="append", default=[], help="listing page or feed URL (repeatable)")
    r.add_argument("--job-url", action="append", default=[], help="a single job page URL (repeatable)")
    r.add_argument("--whatsapp-file", action="append", default=[], help="text file of pasted channel messages")
    r.add_argument("--max-items", type=int, default=5)
    r.add_argument("--publish", action="store_true", help="publish instead of saving drafts")
    r.add_argument("--dry-run", action="store_true")
    a = ap.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    cfg = load_config(a.config)

    if a.cmd == "auth":
        from jobbot.blogger import authorize
        return authorize(cfg)
    if a.cmd == "history":
        from jobbot.storage import Store
        for ts, status, title, url in Store().history():
            print(f"[{status}] {title}\n   {url}")
        return
    if a.cmd == "web":
        from jobbot.web import serve
        return serve(cfg)
    sources = list(cfg["sources"]) if not (a.url or a.job_url or a.whatsapp_file) else []
    sources += [{"type": "website", "url": u, "max_items": a.max_items} for u in a.url]
    sources += [{"type": "url", "url": u} for u in a.job_url]
    sources += [{"type": "whatsapp_file", "path": p, "max_items": a.max_items} for p in a.whatsapp_file]
    if not sources:
        raise SystemExit("No sources. Add them to config.yaml or pass --url / --whatsapp-file.")
    from jobbot.pipeline import run
    for res in run(cfg, sources, publish=a.publish, dry_run=a.dry_run):
        print(f"[{res['status']}] {res['title']} -> {res['url']}")
        if a.dry_run:
            print(res["html"], "\n" + "-" * 60)


if __name__ == "__main__":
    main()
