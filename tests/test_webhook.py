"""Tests for webhook_server.py — verify_signature, get_or_clone_repo,
post_pr_comment, handle_webhook endpoint."""

import hashlib
import hmac
import pathlib
from unittest.mock import MagicMock

import httpx
import pytest
from fastapi.testclient import TestClient


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def client():
    from webhook_server import app
    return TestClient(app)


# ---------------------------------------------------------------------------
# verify_signature
# ---------------------------------------------------------------------------

class TestVerifySignature:
    def test_verify_signature_no_secret(self, mocker):
        """Empty WEBHOOK_SECRET skips verification → always True."""
        mocker.patch("webhook_server.WEBHOOK_SECRET", "")
        from webhook_server import verify_signature
        assert verify_signature(b"any body", "sha256=whatever") is True

    def test_verify_signature_missing(self, mocker):
        """Secret configured but no signature provided → False."""
        mocker.patch("webhook_server.WEBHOOK_SECRET", "mysecret")
        from webhook_server import verify_signature
        assert verify_signature(b"body", None) is False

    def test_verify_signature_valid(self, mocker):
        """Correct HMAC-SHA256 signature → True."""
        secret = "mysecret"
        body = b"test payload"
        sig = "sha256=" + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
        mocker.patch("webhook_server.WEBHOOK_SECRET", secret)
        from webhook_server import verify_signature
        assert verify_signature(body, sig) is True

    def test_verify_signature_invalid(self, mocker):
        """Wrong signature → False."""
        mocker.patch("webhook_server.WEBHOOK_SECRET", "mysecret")
        from webhook_server import verify_signature
        assert verify_signature(b"body", "sha256=deadbeef") is False


# ---------------------------------------------------------------------------
# get_or_clone_repo
# ---------------------------------------------------------------------------

class TestGetOrCloneRepo:
    def test_get_or_clone_repo_clone(self, mocker, tmp_path):
        """Repo dir absent → git clone is called."""
        mocker.patch("webhook_server.REPO_CLONE_DIR", str(tmp_path))
        mocker.patch("webhook_server.BITBUCKET_BASE_URL", "http://bitbucket.example.com")
        mocker.patch("webhook_server.BITBUCKET_TOKEN", "")
        mock_run = mocker.patch("webhook_server.subprocess.run")

        from webhook_server import get_or_clone_repo
        get_or_clone_repo("PROJ", "my-repo")

        args = mock_run.call_args[0][0]
        assert args[0] == "git"
        assert args[1] == "clone"

    def test_get_or_clone_repo_fetch(self, mocker, tmp_path):
        """Repo dir exists → git fetch --all is called."""
        repo_dir = tmp_path / "PROJ" / "my-repo"
        repo_dir.mkdir(parents=True)
        mocker.patch("webhook_server.REPO_CLONE_DIR", str(tmp_path))
        mocker.patch("webhook_server.BITBUCKET_BASE_URL", "http://bitbucket.example.com")
        mock_run = mocker.patch("webhook_server.subprocess.run")

        from webhook_server import get_or_clone_repo
        get_or_clone_repo("PROJ", "my-repo")

        args = mock_run.call_args[0][0]
        assert args[0] == "git"
        assert args[1] == "fetch"

    def test_get_or_clone_repo_path_structure(self, mocker, tmp_path):
        """Returned path embeds project_key and repo_slug."""
        mocker.patch("webhook_server.REPO_CLONE_DIR", str(tmp_path))
        mocker.patch("webhook_server.BITBUCKET_BASE_URL", "http://bitbucket.example.com")
        mocker.patch("webhook_server.BITBUCKET_TOKEN", "")
        mocker.patch("webhook_server.subprocess.run")

        from webhook_server import get_or_clone_repo
        result = get_or_clone_repo("MYPROJECT", "awesome-repo")

        assert "MYPROJECT" in result
        assert "awesome-repo" in result


# ---------------------------------------------------------------------------
# post_pr_comment
# ---------------------------------------------------------------------------

class TestPostPrComment:
    def test_post_pr_comment_url(self, mocker):
        """POST URL contains project_key, repo_slug, and pr_id."""
        mocker.patch("webhook_server.BITBUCKET_BASE_URL", "http://bb.example.com")
        mocker.patch("webhook_server.BITBUCKET_TOKEN", "mytoken")
        mock_post = mocker.patch("webhook_server.httpx.post")
        mock_post.return_value.raise_for_status.return_value = None

        from webhook_server import post_pr_comment
        post_pr_comment("PROJ", "my-repo", 42, "Review text")

        url = mock_post.call_args[0][0]
        assert "PROJ" in url
        assert "my-repo" in url
        assert "42" in url

    def test_post_pr_comment_auth_header(self, mocker):
        """Bearer token is included when BITBUCKET_TOKEN is set."""
        mocker.patch("webhook_server.BITBUCKET_BASE_URL", "http://bb.example.com")
        mocker.patch("webhook_server.BITBUCKET_TOKEN", "mytoken")
        mock_post = mocker.patch("webhook_server.httpx.post")
        mock_post.return_value.raise_for_status.return_value = None

        from webhook_server import post_pr_comment
        post_pr_comment("PROJ", "my-repo", 1, "text")

        headers = mock_post.call_args[1]["headers"]
        assert "Authorization" in headers
        assert "mytoken" in headers["Authorization"]

    def test_post_pr_comment_no_auth(self, mocker):
        """No Authorization header when BITBUCKET_TOKEN is empty."""
        mocker.patch("webhook_server.BITBUCKET_BASE_URL", "http://bb.example.com")
        mocker.patch("webhook_server.BITBUCKET_TOKEN", "")
        mock_post = mocker.patch("webhook_server.httpx.post")
        mock_post.return_value.raise_for_status.return_value = None

        from webhook_server import post_pr_comment
        post_pr_comment("PROJ", "my-repo", 1, "text")

        headers = mock_post.call_args[1]["headers"]
        assert "Authorization" not in headers

    def test_post_pr_comment_raises_on_error(self, mocker):
        """raise_for_status() propagates HTTP errors."""
        mocker.patch("webhook_server.BITBUCKET_BASE_URL", "http://bb.example.com")
        mocker.patch("webhook_server.BITBUCKET_TOKEN", "")
        mock_post = mocker.patch("webhook_server.httpx.post")
        mock_post.return_value.raise_for_status.side_effect = httpx.HTTPStatusError(
            message="500 Server Error",
            request=httpx.Request("POST", "http://bb.example.com"),
            response=httpx.Response(500),
        )

        from webhook_server import post_pr_comment
        with pytest.raises(httpx.HTTPStatusError):
            post_pr_comment("PROJ", "my-repo", 1, "text")


# ---------------------------------------------------------------------------
# handle_webhook (FastAPI endpoint)
# ---------------------------------------------------------------------------

class TestHandleWebhook:
    def test_webhook_invalid_signature(self, client, mocker):
        """Invalid HMAC signature → 401."""
        mocker.patch("webhook_server.WEBHOOK_SECRET", "mysecret")
        body = b'{"eventKey": "pr:opened"}'
        resp = client.post(
            "/webhook",
            content=body,
            headers={"X-Hub-Signature-256": "sha256=invalid"},
        )
        assert resp.status_code == 401

    def test_webhook_ignores_unknown_event(self, client, mocker):
        """Non-PR event → 200 with status=ignored."""
        mocker.patch("webhook_server.WEBHOOK_SECRET", "")
        resp = client.post("/webhook", json={"eventKey": "repo:push"})
        assert resp.status_code == 200
        assert resp.json()["status"] == "ignored"
        assert resp.json()["eventKey"] == "repo:push"

    def test_webhook_pr_opened(self, client, mocker, sample_pr_payload, tmp_path):
        """pr:opened triggers review and posts comment → 200 ok."""
        mocker.patch("webhook_server.WEBHOOK_SECRET", "")
        mocker.patch("webhook_server.get_or_clone_repo", return_value=str(tmp_path))
        mock_post_comment = mocker.patch("webhook_server.post_pr_comment")

        def _write_review(from_commit, to_commit, output_file=None, **kwargs):
            if output_file:
                pathlib.Path(output_file).write_text("## Review", encoding="utf-8")
            return "## Review"

        mocker.patch("webhook_server.run_review", side_effect=_write_review)

        resp = client.post("/webhook", json=sample_pr_payload)

        assert resp.status_code == 200
        assert resp.json() == {"status": "ok", "pr": 42}
        mock_post_comment.assert_called_once()

    def test_webhook_pr_updated(self, client, mocker, sample_pr_payload, tmp_path):
        """pr:from_ref_updated also triggers review → 200 ok."""
        sample_pr_payload["eventKey"] = "pr:from_ref_updated"
        mocker.patch("webhook_server.WEBHOOK_SECRET", "")
        mocker.patch("webhook_server.get_or_clone_repo", return_value=str(tmp_path))
        mocker.patch("webhook_server.post_pr_comment")

        def _write_review(from_commit, to_commit, output_file=None, **kwargs):
            if output_file:
                pathlib.Path(output_file).write_text("## Review", encoding="utf-8")
            return "## Review"

        mocker.patch("webhook_server.run_review", side_effect=_write_review)

        resp = client.post("/webhook", json=sample_pr_payload)

        assert resp.status_code == 200
        assert resp.json()["status"] == "ok"

    def test_webhook_no_diff(self, client, mocker, sample_pr_payload, tmp_path):
        """Empty review result → post_pr_comment is NOT called."""
        mocker.patch("webhook_server.WEBHOOK_SECRET", "")
        mocker.patch("webhook_server.get_or_clone_repo", return_value=str(tmp_path))
        mock_post_comment = mocker.patch("webhook_server.post_pr_comment")
        # run_review returns "" and does NOT write to output_file → temp file stays empty
        mocker.patch("webhook_server.run_review", return_value="")

        resp = client.post("/webhook", json=sample_pr_payload)

        assert resp.status_code == 200
        mock_post_comment.assert_not_called()
