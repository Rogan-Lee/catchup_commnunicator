from __future__ import annotations

import httpx
import pytest

from app.core.errors import JiraError
from app.services.atlassian.jira_issues import (
    CreateIssuePayload,
    JiraIssueService,
    task_type_to_issue_type,
)


def _mock_client(handler) -> httpx.AsyncClient:
    transport = httpx.MockTransport(handler)
    return httpx.AsyncClient(
        base_url="https://example.atlassian.net",
        transport=transport,
    )


@pytest.mark.asyncio
async def test_create_issue_sends_expected_payload_and_parses_response():
    captured: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/rest/api/3/issue"
        captured["body"] = request.read()
        return httpx.Response(
            201, json={"id": "10456", "key": "CATCHUP-456", "self": "https://x/y"}
        )

    client = _mock_client(handler)
    svc = JiraIssueService(client, team_field_id="customfield_10001")

    created = await svc.create_issue(
        CreateIssuePayload(
            project_key="CATCHUP",
            summary="[로그인] Story | OAuth",
            description="line1\nline2",
            issue_type="Story",
            parent_key="AUTH-1",
            assignee_account_id="acc-1",
            team_id="ari:team/abc",
        )
    )
    await client.aclose()

    assert created.key == "CATCHUP-456"
    assert created.url == "https://example.atlassian.net/browse/CATCHUP-456"

    import json as _json

    sent = _json.loads(captured["body"])["fields"]
    assert sent["project"] == {"key": "CATCHUP"}
    assert sent["summary"] == "[로그인] Story | OAuth"
    assert sent["issuetype"] == {"name": "Story"}
    assert sent["parent"] == {"key": "AUTH-1"}
    assert sent["assignee"] == {"accountId": "acc-1"}
    assert sent["customfield_10001"] == "ari:team/abc"
    assert sent["description"]["type"] == "doc"


@pytest.mark.asyncio
async def test_create_issue_raises_on_4xx():
    def handler(request):
        return httpx.Response(400, json={"errors": {"summary": "is required"}})

    client = _mock_client(handler)
    svc = JiraIssueService(client)

    with pytest.raises(JiraError):
        await svc.create_issue(
            CreateIssuePayload(project_key="X", summary="y", issue_type="Task")
        )
    await client.aclose()


@pytest.mark.asyncio
async def test_team_field_skipped_when_no_field_id_configured():
    captured: dict = {}

    def handler(request):
        captured["body"] = request.read()
        return httpx.Response(201, json={"id": "1", "key": "X-1"})

    client = _mock_client(handler)
    svc = JiraIssueService(client, team_field_id=None)
    await svc.create_issue(
        CreateIssuePayload(
            project_key="X",
            summary="y",
            issue_type="Task",
            team_id="ari:team/x",
        )
    )
    await client.aclose()

    import json as _json

    fields = _json.loads(captured["body"])["fields"]
    assert "customfield_10001" not in fields
    assert not any(k.startswith("customfield") for k in fields)


def test_task_type_mapping():
    assert task_type_to_issue_type("Feature") == "Story"
    assert task_type_to_issue_type("Bug") == "Bug"
    assert task_type_to_issue_type("Refactoring") == "Task"
    assert task_type_to_issue_type(None) == "Task"
    assert task_type_to_issue_type("Unknown") == "Task"
