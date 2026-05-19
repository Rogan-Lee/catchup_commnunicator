from __future__ import annotations

import json
from functools import lru_cache
from typing import Literal

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    # Application
    app_base_url: str = "http://localhost:8000"
    log_level: str = "INFO"
    environment: Literal["development", "production", "test"] = "development"

    # Slack
    slack_bot_token: str = ""
    slack_signing_secret: str = ""
    slack_standup_channels: str = ""

    # Bearer token for the /standup/ingest webhook (edge function → us).
    # When empty, the endpoint refuses all requests.
    standup_ingest_token: str = ""

    # Atlassian
    atlassian_base_url: str = ""
    atlassian_email: str = ""
    atlassian_api_token: str = ""
    atlassian_team_field_id: str = "customfield_10001"

    # Team → Project mapping (JSON string in env)
    team_project_map: str = "{}"

    # Gemini
    gemini_api_key: str = ""
    gemini_model: str = "gemini-2.0-flash-exp"

    # Database
    database_url: str = "postgresql+psycopg://user:pass@localhost:5432/standup_jira"

    # Redis
    redis_url: str = "redis://localhost:6379/0"
    redis_default_ttl: int = 3600

    # Feature flags
    enable_llm_extraction: bool = True
    enable_auto_publish: bool = False
    dry_run: bool = True

    @field_validator("log_level")
    @classmethod
    def _upper_log_level(cls, v: str) -> str:
        return v.upper()

    @property
    def standup_channel_ids(self) -> set[str]:
        return {c.strip() for c in self.slack_standup_channels.split(",") if c.strip()}

    @property
    def team_to_project_map(self) -> dict[str, str]:
        if not self.team_project_map:
            return {}
        try:
            return json.loads(self.team_project_map)
        except json.JSONDecodeError:
            return {}


@lru_cache
def get_settings() -> Settings:
    return Settings()
