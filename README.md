# Standup → Jira

Slack standup messages get auto-extracted into Jira tickets. A free-text
standup post becomes a thread-bound preview card; one click opens a
pre-filled modal; one more click creates the Jira issue.

## Stack

- Python 3.11+, FastAPI (async), SQLAlchemy 2.0 + Alembic, PostgreSQL
- Redis for external API caching
- Gemini 2.0 Flash for slot extraction (free tier)
- `slack-sdk` for Slack, `httpx` for Atlassian / Jira

## Local development

```bash
uv sync --extra dev
cp .env.example .env  # then fill in real values
uv run alembic upgrade head
uv run uvicorn app.main:app --reload
```

Health probe: `GET /health`. Slack events: `POST /slack/events`.

Run tests:

```bash
uv run pytest
```

## Layout

```
app/
  main.py            FastAPI factory + lifespan
  config.py          Pydantic settings
  core/              logging, errors
  db/                models, async session
  routers/           Slack HTTP endpoints
  services/slack/    signature verification, etc.
alembic/             migrations
tests/unit/          unit tests
```

See the implementation spec for the full milestone breakdown (W1 → W3).
