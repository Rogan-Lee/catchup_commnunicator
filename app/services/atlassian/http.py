from __future__ import annotations

import base64

import httpx

# Atlassian Teams API lives at a different host than the per-tenant Jira host.
ATLASSIAN_TEAMS_BASE = "https://api.atlassian.com"


def _basic_auth_header(email: str, token: str) -> str:
    raw = f"{email}:{token}".encode("utf-8")
    return "Basic " + base64.b64encode(raw).decode("ascii")


def build_jira_client(
    base_url: str,
    email: str,
    api_token: str,
    timeout: float = 10.0,
) -> httpx.AsyncClient:
    """HTTP client for per-tenant Jira REST API (https://<tenant>.atlassian.net)."""
    return httpx.AsyncClient(
        base_url=base_url.rstrip("/"),
        timeout=timeout,
        headers={
            "Authorization": _basic_auth_header(email, api_token),
            "Accept": "application/json",
            "Content-Type": "application/json",
        },
    )


def build_teams_client(
    email: str,
    api_token: str,
    timeout: float = 10.0,
) -> httpx.AsyncClient:
    """HTTP client for Atlassian Teams API (api.atlassian.com)."""
    return httpx.AsyncClient(
        base_url=ATLASSIAN_TEAMS_BASE,
        timeout=timeout,
        headers={
            "Authorization": _basic_auth_header(email, api_token),
            "Accept": "application/json",
            "Content-Type": "application/json",
        },
    )
