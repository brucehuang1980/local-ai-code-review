#!/usr/bin/env python3
"""Bitbucket Server webhook receiver — auto-triggers code review on PR open/update."""

import hashlib
import hmac
import os
import subprocess
import tempfile
from pathlib import Path
from typing import Optional

import httpx
from dotenv import load_dotenv
from fastapi import FastAPI, Header, HTTPException, Request
from review import run_review

load_dotenv()

app = FastAPI(title="AI Code Review Webhook")

WEBHOOK_SECRET = os.getenv("WEBHOOK_SECRET", "")
BITBUCKET_BASE_URL = os.getenv("BITBUCKET_BASE_URL", "")   # e.g. http://co-git
BITBUCKET_TOKEN = os.getenv("BITBUCKET_TOKEN", "")         # personal access token
REPO_CLONE_DIR = os.getenv("REPO_CLONE_DIR", "/tmp/code-review-repos")


def verify_signature(body: bytes, signature: Optional[str]) -> bool:
    """Verify Bitbucket webhook HMAC-SHA256 signature."""
    if not WEBHOOK_SECRET:
        return True  # skip verification if secret not configured
    if not signature:
        return False
    expected = "sha256=" + hmac.new(
        WEBHOOK_SECRET.encode(), body, hashlib.sha256
    ).hexdigest()
    return hmac.compare_digest(expected, signature)


def get_or_clone_repo(project_key: str, repo_slug: str) -> str:
    """Return local path of cloned repo; clone or fetch if needed."""
    repo_dir = Path(REPO_CLONE_DIR) / project_key / repo_slug
    clone_url = (
        f"{BITBUCKET_BASE_URL}/scm/{project_key.lower()}/{repo_slug}.git"
    )
    auth_url = clone_url.replace(
        "://", f"://x-token-auth:{BITBUCKET_TOKEN}@"
    ) if BITBUCKET_TOKEN else clone_url

    if repo_dir.exists():
        subprocess.run(["git", "fetch", "--all"], cwd=repo_dir, check=True, capture_output=True)
    else:
        repo_dir.mkdir(parents=True, exist_ok=True)
        subprocess.run(["git", "clone", auth_url, str(repo_dir)], check=True, capture_output=True)

    return str(repo_dir)


def post_pr_comment(project_key: str, repo_slug: str, pr_id: int, text: str):
    """Post a comment to a Bitbucket Server PR."""
    url = (
        f"{BITBUCKET_BASE_URL}/rest/api/1.0/projects/{project_key}"
        f"/repos/{repo_slug}/pull-requests/{pr_id}/comments"
    )
    headers = {
        "Content-Type": "application/json",
        **({"Authorization": f"Bearer {BITBUCKET_TOKEN}"} if BITBUCKET_TOKEN else {}),
    }
    resp = httpx.post(url, json={"text": text}, headers=headers, timeout=30)
    resp.raise_for_status()


@app.post("/webhook")
async def handle_webhook(
    request: Request,
    x_hub_signature_256: Optional[str] = Header(None),
):
    body = await request.body()

    if not verify_signature(body, x_hub_signature_256):
        raise HTTPException(status_code=401, detail="Invalid signature")

    payload = await request.json()
    event_key = payload.get("eventKey", "")

    # Only handle PR opened or source-branch updated
    if event_key not in ("pr:opened", "pr:from_ref_updated"):
        return {"status": "ignored", "eventKey": event_key}

    pr = payload.get("pullRequest", {})
    pr_id = pr.get("id")
    project_key = pr["toRef"]["repository"]["project"]["key"]
    repo_slug = pr["toRef"]["repository"]["slug"]
    from_commit = pr["fromRef"]["latestCommit"]
    to_commit = pr["toRef"]["latestCommit"]

    print(f"[webhook] PR #{pr_id} {project_key}/{repo_slug} {from_commit[:8]}..{to_commit[:8]}")

    try:
        repo_path = get_or_clone_repo(project_key, repo_slug)

        with tempfile.NamedTemporaryFile(suffix=".md", delete=False, mode="w") as tmp:
            tmp_path = tmp.name

        # Run review inside the cloned repo directory
        import os as _os
        original_dir = _os.getcwd()
        _os.chdir(repo_path)
        try:
            report = run_review(
                from_commit=from_commit,
                to_commit=to_commit,
                output_file=tmp_path,
            )
        finally:
            _os.chdir(original_dir)

        comment = Path(tmp_path).read_text(encoding="utf-8")
        Path(tmp_path).unlink(missing_ok=True)

        if comment.strip():
            post_pr_comment(project_key, repo_slug, pr_id, comment)
            print(f"[webhook] Review posted to PR #{pr_id}")

    except Exception as exc:
        print(f"[webhook] Error: {exc}")
        raise HTTPException(status_code=500, detail=str(exc))

    return {"status": "ok", "pr": pr_id}


if __name__ == "__main__":
    import uvicorn
    port = int(os.getenv("WEBHOOK_PORT", "8000"))
    uvicorn.run("webhook_server:app", host="0.0.0.0", port=port, reload=False)
