from __future__ import annotations

from enum import Enum

from pydantic import BaseModel, Field


class TaskType(str, Enum):
    FEATURE = "Feature"
    BUG = "Bug"
    IMPROVEMENT = "Improvement"
    REFACTORING = "Refactoring"
    TECH_DEBT = "Tech Debt"
    DOCS = "Docs"
    SPIKE = "Spike"
    DEVOPS = "DevOps"


class ExtractedWorkItem(BaseModel):
    team_id: str | None = Field(None, description="Atlassian Team ARI")
    team_name: str | None = Field(None, description="Team display name (for inference)")
    task_type: TaskType | None = None
    parent_feature: str | None = Field(None, description="Parent feature, may be Korean")
    task_content: str = Field(..., max_length=200, description="Task content summary")
    parent_issue_hint: str | None = Field(
        None, description="Jira issue key or guessed parent-task description"
    )


class ExtractionResult(BaseModel):
    items: list[ExtractedWorkItem]


class ExtractionContext(BaseModel):
    user_primary_team_id: str | None = None
    user_primary_team_name: str | None = None
    available_teams: list[tuple[str, str]] = Field(default_factory=list)
    active_epics: list[tuple[str, str]] = Field(default_factory=list)
    project_keys: list[str] = Field(default_factory=list)


class Prompt(BaseModel):
    system: str
    user: str
