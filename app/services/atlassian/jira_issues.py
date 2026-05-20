from __future__ import annotations

from typing import Any

import httpx
from pydantic import BaseModel

from app.core.errors import JiraError
from app.services.atlassian.adf import text_to_adf


class CreateIssuePayload(BaseModel):
    project_key: str
    summary: str
    description: str | None = None
    issue_type: str = "Task"
    parent_key: str | None = None
    assignee_account_id: str | None = None
    team_id: str | None = None


class CreatedIssue(BaseModel):
    key: str
    id: str
    url: str


# Task-type label (used in modal & naming) → Jira issue type name.
# Override per workspace by editing this map (small, infrequent).
TASK_TYPE_TO_ISSUE_TYPE: dict[str, str] = {
    "Feature": "Story",
    "Bug": "Bug",
    "Improvement": "Story",
    "Refactoring": "Task",
    "Tech Debt": "Task",
    "Docs": "Task",
    "Spike": "Task",
    "DevOps": "Task",
}


def task_type_to_issue_type(task_type: str | None) -> str:
    if not task_type:
        return "Task"
    return TASK_TYPE_TO_ISSUE_TYPE.get(task_type, "Task")


class JiraIssueService:
    def __init__(self, http: httpx.AsyncClient, team_field_id: str | None = None):
        self.http = http
        self.team_field_id = team_field_id

    async def list_issue_types(self, project_key: str) -> list[str]:
        """Issue type names creatable in a project (subtasks excluded).

        Uses the createmeta issuetypes endpoint so the modal only ever offers
        types Jira will actually accept for this project.
        """
        resp = await self.http.get(
            f"/rest/api/3/issue/createmeta/{project_key}/issuetypes"
        )
        resp.raise_for_status()
        data = resp.json()
        items = data.get("values") or data.get("issueTypes") or []
        names = [
            it["name"]
            for it in items
            if it.get("name") and not it.get("subtask", False)
        ]
        # De-dupe while preserving order.
        seen: set[str] = set()
        return [n for n in names if not (n in seen or seen.add(n))]

    async def create_issue(self, payload: CreateIssuePayload) -> CreatedIssue:
        fields: dict[str, Any] = {
            "project": {"key": payload.project_key},
            "summary": payload.summary,
            "issuetype": {"name": payload.issue_type},
        }
        if payload.description:
            fields["description"] = text_to_adf(payload.description)
        if payload.parent_key:
            fields["parent"] = {"key": payload.parent_key}
        if payload.assignee_account_id:
            fields["assignee"] = {"accountId": payload.assignee_account_id}
        if payload.team_id and self.team_field_id:
            fields[self.team_field_id] = payload.team_id

        resp = await self.http.post("/rest/api/3/issue", json={"fields": fields})
        if resp.status_code >= 400:
            raise JiraError(
                f"Issue create failed: {resp.status_code} {resp.text[:500]}"
            )
        data = resp.json()
        return CreatedIssue(
            key=data["key"],
            id=data["id"],
            url=f"{str(self.http.base_url).rstrip('/')}/browse/{data['key']}",
        )
