"""Configuration loading: secrets from environment/.env, behaviour from config.yaml."""
import os
from pathlib import Path
import yaml
from dotenv import load_dotenv

DEFAULTS = {
    "blogger": {"publish_immediately": False, "labels": ["Jobs"]},
    "fetch": {
        "timeout_seconds": 15,
        "max_bytes": 2_000_000,
        "delay_per_domain_seconds": 3,
        "respect_robots_txt": True,
        "allowed_domains": [],
    },
    "content": {"excerpt_chars": 600, "use_ai_summary": False},
    "sources": [],
}


def _merge(base: dict, over: dict) -> dict:
    out = dict(base)
    for k, v in (over or {}).items():
        out[k] = _merge(base[k], v) if isinstance(v, dict) and isinstance(base.get(k), dict) else v
    return out


def load_config(path: str = "config.yaml") -> dict:
    load_dotenv()
    cfg = dict(DEFAULTS)
    p = Path(path)
    if p.exists():
        cfg = _merge(DEFAULTS, yaml.safe_load(p.read_text(encoding="utf-8")) or {})
    cfg["env"] = {
        "blog_id": os.getenv("BLOGGER_BLOG_ID", ""),
        "client_secret_file": os.getenv("GOOGLE_CLIENT_SECRET_FILE", "client_secret.json"),
        "token_file": os.getenv("GOOGLE_TOKEN_FILE", "token.json"),
        "user_agent": os.getenv("BOT_USER_AGENT", "Job2BloggerBot/1.0"),
        "gemini_key": os.getenv("GEMINI_API_KEY", ""),
        "ai_model": os.getenv("AI_MODEL", "gemini-3.5-flash-lite"),
        "ai_image_model": os.getenv("AI_IMAGE_MODEL", "gemini-nano-banana-2.1"),
    }
    return cfg
