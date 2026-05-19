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

Endpoints: `GET /health`, `POST /slack/events`, `POST /slack/interactions`,
`POST /slack/options`.

Run tests:

```bash
uv run pytest
```

Ops CLI:

```bash
# Re-extract a specific Slack message
uv run python -m app.cli reprocess 1700000000.000100 --channel C0123ABCDEF

# Accuracy report (% of tickets created without user edits)
uv run python -m app.cli accuracy --days 7

# Naming-rule export / import
uv run python -m app.cli rules export > rules.json
uv run python -m app.cli rules import rules.json
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
