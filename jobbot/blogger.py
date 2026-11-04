"""Blogger API v3 client (OAuth2). Posts are created as drafts unless told otherwise."""
import os
import time
from pathlib import Path
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError

SCOPES = ["https://www.googleapis.com/auth/blogger"]


def authorize(cfg):
    """One-time interactive login; stores a refresh token with 0600 permissions."""
    env = cfg["env"]
    flow = InstalledAppFlow.from_client_secrets_file(env["client_secret_file"], SCOPES)
    creds = flow.run_local_server(port=0)
    token = Path(env["token_file"])
    token.write_text(creds.to_json())
    os.chmod(token, 0o600)
    print(f"Saved token to {token}")


def _creds(cfg) -> Credentials:
    token = Path(cfg["env"]["token_file"])
    if not token.exists():
        raise SystemExit("No token found. Run: python main.py auth")
    creds = Credentials.from_authorized_user_file(str(token), SCOPES)
    if not creds.valid and creds.refresh_token:
        creds.refresh(Request())
        token.write_text(creds.to_json())
        os.chmod(token, 0o600)
    return creds


class Blogger:
    def __init__(self, cfg):
        self.blog_id = cfg["env"]["blog_id"]
        if not self.blog_id:
            raise SystemExit("BLOGGER_BLOG_ID is not set")
        self.svc = build("blogger", "v3", credentials=_creds(cfg), cache_discovery=False)

    def insert(self, title: str, content: str, labels: list, is_draft: bool = True) -> dict:
        body = {"kind": "blogger#post", "title": title[:200], "content": content, "labels": labels}
        for attempt in range(3):
            try:
                return self.svc.posts().insert(blogId=self.blog_id, body=body, isDraft=is_draft).execute()
            except HttpError as e:
                if e.resp.status in (429, 500, 503) and attempt < 2:
                    time.sleep(2 ** attempt * 2)
                    continue
                raise

    def update(self, post: dict) -> dict:
        """Update an existing post using its full resource to preserve its metadata."""
        post_id = post.get("id")
        if not post_id:
            raise ValueError("A Blogger post ID is required for updates.")
        for attempt in range(3):
            try:
                return self.svc.posts().update(
                    blogId=self.blog_id,
                    postId=post_id,
                    body=post,
                ).execute()
            except HttpError as e:
                if e.resp.status in (429, 500, 503) and attempt < 2:
                    time.sleep(2 ** attempt * 2)
                    continue
                raise
