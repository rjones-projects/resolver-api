"""
FileServiceClient

Routes all GitHub file access through a configurable local service instead of
calling GitHub directly. Configure the service URL via the FILE_SERVICE_URL
environment variable (default: https://repo-api-479677124022.europe-west2.run.app).

repo-api sits behind Cloud Run IAM and requires a Google-signed identity token
on every request. In production, google-auth mints one from the Cloud Run
runtime service account via the metadata server (audience = repo-api's URL).
Locally, where there's usually no ADC set up, it falls back to `gcloud auth
print-identity-token` — the same token a developer's own manual curl testing
would use.
"""

from __future__ import annotations

import base64
import json
import os
import subprocess
import threading
import time
from typing import Any, Optional

import google.auth.exceptions
import google.auth.transport.requests
import google.oauth2.id_token
import httpx
import yaml

FILE_SERVICE_URL: str = os.getenv("FILE_SERVICE_URL", "https://repo-api-479677124022.europe-west2.run.app")

# The file service intermittently returns 5xx or times out on otherwise-valid
# requests; GET is idempotent so retrying a couple of times clears most of these
# transient blips. 4xx (e.g. 404 for a missing file) is not retried — it's a real
# answer, not a hiccup.
_GET_RETRY_ATTEMPTS = 3
_GET_RETRY_BACKOFF_SECONDS = 0.5

# Identity tokens are valid for ~1h; cache per audience and refetch a minute
# before expiry instead of minting a fresh one on every call.
_token_lock = threading.Lock()
_cached_tokens: dict[str, tuple[str, float]] = {}  # audience -> (token, exp_epoch)


def _identity_token(audience: str) -> str:
    with _token_lock:
        cached = _cached_tokens.get(audience)
        if cached and time.time() < cached[1] - 60:
            return cached[0]
        try:
            token = google.oauth2.id_token.fetch_id_token(
                google.auth.transport.requests.Request(), audience
            )
        except google.auth.exceptions.DefaultCredentialsError:
            token = _gcloud_identity_token(audience)
        _cached_tokens[audience] = (token, _token_expiry(token))
        return token


def _gcloud_identity_token(audience: str) -> str:
    # `--audiences` only works for a service account (via impersonation); a human
    # `gcloud auth login` session can't mint one, so for the local-dev fallback we
    # request the same un-audienced user identity token the manual curl workflow uses.
    del audience
    try:
        result = subprocess.run(
            ["gcloud", "auth", "print-identity-token"],
            capture_output=True, text=True, check=True, shell=(os.name == "nt"),
        )
    except (OSError, subprocess.CalledProcessError) as exc:
        raise RuntimeError(
            "No Google credentials available for repo-api: no metadata server, no "
            "ADC, and `gcloud auth print-identity-token` failed. Run `gcloud auth "
            "login` or set GOOGLE_APPLICATION_CREDENTIALS."
        ) from exc
    return result.stdout.strip()


def _token_expiry(token: str) -> float:
    payload_b64 = token.split(".")[1]
    payload_b64 += "=" * (-len(payload_b64) % 4)
    payload = json.loads(base64.urlsafe_b64decode(payload_b64))
    return float(payload["exp"])


class FileServiceClient:
    """HTTP client that fetches files via the local GitHub file service."""

    def __init__(self, base_url: Optional[str] = None):
        self.base_url = (base_url or FILE_SERVICE_URL).rstrip("/")

    # ------------------------------------------------------------------
    # Low-level request
    # ------------------------------------------------------------------

    def _auth_headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {_identity_token(self.base_url)}"}

    def _get(self, endpoint: str, **params: Any) -> httpx.Response:
        url = f"{self.base_url}{endpoint}"
        filtered = {k: v for k, v in params.items() if v is not None}
        for attempt in range(_GET_RETRY_ATTEMPTS):
            try:
                with httpx.Client(timeout=15) as client:
                    resp = client.get(url, params=filtered, headers=self._auth_headers())
                resp.raise_for_status()
                return resp
            except httpx.HTTPStatusError as exc:
                if exc.response.status_code < 500 or attempt == _GET_RETRY_ATTEMPTS - 1:
                    raise
            except httpx.TimeoutException:
                if attempt == _GET_RETRY_ATTEMPTS - 1:
                    raise
            time.sleep(_GET_RETRY_BACKOFF_SECONDS * (attempt + 1))

    def _post(self, endpoint: str, json_body: dict[str, Any]) -> httpx.Response:
        url = f"{self.base_url}{endpoint}"
        with httpx.Client(timeout=30) as client:
            resp = client.post(url, json=json_body, headers=self._auth_headers())
        resp.raise_for_status()
        return resp

    # ------------------------------------------------------------------
    # File access helpers used by the resolver
    # ------------------------------------------------------------------

    def get_file_content(
        self, owner: str, repo: str, path: str, ref: str = "HEAD"
    ) -> Any:
        """
        Return the parsed `content` field from the file envelope.
        - YAML/JSON files   → already-parsed Python dict / list
        - Plain-text files  → string
        """
        resp = self._get(f"/repos/{owner}/{repo}/file", path=path, ref=ref)
        envelope = yaml.safe_load(resp.text) or {}
        return envelope.get("content")

    def get_text_file(
        self, owner: str, repo: str, path: str, ref: str = "HEAD"
    ) -> str:
        """
        Fetch a plain-text file (e.g. .tf, .hcl) as a raw string.
        Uses raw=true so the service returns the file bytes without re-parsing.
        """
        resp = self._get(
            f"/repos/{owner}/{repo}/file", path=path, ref=ref, raw="true"
        )
        data = yaml.safe_load(resp.text) or {}
        content = data.get("content", "")
        return content if isinstance(content, str) else yaml.dump(content, allow_unicode=True)

    def commit_files(
        self,
        owner: str,
        repo: str,
        files: dict[str, str],
        message: str,
        destination: str = "infra",
    ) -> dict[str, Any]:
        """
        Commit files to a GitHub repository via the repo-api. The repo is
        auto-created if it doesn't exist; the repo-api writes the files under
        `destination/`, commits them on a new branch, and opens a pull request.

        `files` maps each path (relative to `destination/`) to its full text
        content. Returns the parsed CommitResponse: {repo, branch, commit_sha,
        files_committed, created_repo, pull_request_url, workflow_path}.
        """
        body: dict[str, Any] = {"message": message, "files": files}
        if destination:
            body["destination"] = destination
        resp = self._post(f"/repos/{owner}/{repo}/commit", body)
        return resp.json()

    def proxy_catalog_file(
        self,
        owner: str,
        repo: str,
        path: str,
        ref: str = "HEAD",
    ) -> list[dict]:
        """
        Fetch a multi-document YAML catalog file, returning a list of parsed docs.
        Uses get_file_content so the service handles GitHub auth.
        """
        content = self.get_file_content(owner, repo, path, ref)
        if isinstance(content, list):
            return [d for d in content if d is not None]
        if isinstance(content, dict):
            return [content]
        # Fallback: content came back as a string — parse it
        if isinstance(content, str):
            return [d for d in yaml.safe_load_all(content) if d is not None]
        return []


def get_client() -> FileServiceClient:
    """Return a FileServiceClient configured from FILE_SERVICE_URL."""
    return FileServiceClient(FILE_SERVICE_URL)
