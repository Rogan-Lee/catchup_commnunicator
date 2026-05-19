from __future__ import annotations

from typing import Any

from pydantic import BaseModel


class Team(BaseModel):
    id: str
    name: str
    description: str | None = None

    @classmethod
    def from_api(cls, data: dict[str, Any]) -> Team:
        # Atlassian Teams API uses teamId + displayName.
        return cls(
            id=data.get("teamId") or data.get("id"),
            name=data.get("displayName") or data.get("name", ""),
            description=data.get("description"),
        )


class TeamMember(BaseModel):
    account_id: str
    name: str | None = None
    email: str | None = None

    @classmethod
    def from_api(cls, data: dict[str, Any]) -> TeamMember:
        return cls(
            account_id=data.get("accountId") or data.get("id", ""),
            name=data.get("name") or data.get("displayName"),
            email=data.get("email"),
        )


class Issue(BaseModel):
    key: str
    id: str | None = None
    summary: str = ""
    issue_type: str | None = None
    status: str | None = None
    project_key: str | None = None

    @classmethod
    def from_api(cls, data: dict[str, Any]) -> Issue:
        fields = data.get("fields", {}) or {}
        issue_type = (fields.get("issuetype") or {}).get("name")
        status = (fields.get("status") or {}).get("name")
        project_key = (fields.get("project") or {}).get("key")
        return cls(
            key=data["key"],
            id=data.get("id"),
            summary=fields.get("summary") or "",
            issue_type=issue_type,
            status=status,
            project_key=project_key,
        )


class AtlassianUser(BaseModel):
    account_id: str
    display_name: str | None = None
    email: str | None = None

    @classmethod
    def from_api(cls, data: dict[str, Any]) -> AtlassianUser:
        return cls(
            account_id=data.get("accountId", ""),
            display_name=data.get("displayName"),
            email=data.get("emailAddress"),
        )
