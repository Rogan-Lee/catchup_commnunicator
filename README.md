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

## Deploy (Fly.io)

A `Dockerfile` and `fly.toml` are included. The release step runs
`alembic upgrade head`; one machine is kept warm so Slack interactions never
hit a cold start (`min_machines_running` in `fly.toml`).

```bash
# one-time
fly launch --no-deploy        # or `fly apps create <name>` and edit fly.toml `app`

# set secrets (DB/Redis/Slack/Atlassian/ingest token, etc.)
fly secrets set \
  DATABASE_URL='postgresql+psycopg://...supabase...?sslmode=require' \
  REDIS_URL='rediss://...upstash...' \
  SLACK_BOT_TOKEN='xoxb-...' \
  SLACK_SIGNING_SECRET='...' \
  SLACK_STANDUP_CHANNELS='C0...' \
  ATLASSIAN_BASE_URL='https://<workspace>.atlassian.net' \
  ATLASSIAN_EMAIL='you@example.com' \
  ATLASSIAN_API_TOKEN='ATATT...' \
  ATLASSIAN_TEAM_FIELD_ID='customfield_10001' \
  TEAM_PROJECT_MAP='{"ari:cloud:identity::team/...":"CAM"}' \
  STANDUP_INGEST_TOKEN='<same value as the edge function>' \
  EXTRACTION_MODE='rule'

fly deploy
```

After deploy, point the Supabase edge function's `STANDUP_INGEST_URL` at the
Fly URL (e.g. `https://<app>.fly.dev`) and stop the local uvicorn/ngrok.
Supabase Postgres and Upstash Redis stay as-is — only this app moves to Fly.

## Atlassian setup

The app authenticates to Jira via HTTP Basic with an API token. Follow these
steps once per environment to populate the `ATLASSIAN_*` and `TEAM_PROJECT_MAP`
entries in `.env`.

### 1. Issue an API token

1. Open https://id.atlassian.com/manage-profile/security/api-tokens
2. **Create API token**, label it (e.g. `catchup-communicator-local`), copy the
   value (`ATATT...`) immediately — it cannot be viewed again.
3. Set in `.env`:
   ```env
   ATLASSIAN_BASE_URL=https://<workspace>.atlassian.net
   ATLASSIAN_EMAIL=<account-email>
   ATLASSIAN_API_TOKEN=ATATT...
   ```

Smoke-test:
```bash
set -a; source .env; set +a
curl -s -u "$ATLASSIAN_EMAIL:$ATLASSIAN_API_TOKEN" \
  "$ATLASSIAN_BASE_URL/rest/api/3/myself" | jq .accountId
```

### 2. Find the Team custom field ID

The default `customfield_10001` matches Atlassian's standard Team field. To
confirm for your site:
```bash
curl -s -u "$ATLASSIAN_EMAIL:$ATLASSIAN_API_TOKEN" \
  "$ATLASSIAN_BASE_URL/rest/api/3/field" \
  | jq '.[] | select(.name=="Team") | {id, schema: .schema.type}'
```
Set the result in `.env`:
```env
ATLASSIAN_TEAM_FIELD_ID=customfield_10001
```

### 3. Resolve Team ARIs and project keys

`TEAM_PROJECT_MAP` is a JSON object whose keys are Atlassian Team ARIs and
values are Jira project keys.

**Project keys** — list projects matching a name fragment:
```bash
curl -s -u "$ATLASSIAN_EMAIL:$ATLASSIAN_API_TOKEN" \
  "$ATLASSIAN_BASE_URL/rest/api/3/project/search?query=<fragment>" \
  | jq '.values[] | {key, name}'
```

**Team ARIs** — for teams that already have at least one Jira issue with the
Team field set:
```bash
curl -s -u "$ATLASSIAN_EMAIL:$ATLASSIAN_API_TOKEN" \
  --get "$ATLASSIAN_BASE_URL/rest/api/3/search/jql" \
  --data-urlencode 'jql=cf[10001] is not EMPTY ORDER BY updated DESC' \
  --data-urlencode "fields=customfield_10001" \
  --data-urlencode "maxResults=100" \
  | jq -r '.issues[].fields.customfield_10001 | "\(.id)\t\(.name)"' | sort -u
```
For teams with no existing tickets, grab the ID from the Teams directory
(`https://<workspace>.atlassian.net/jira/people/teams` → click team → trailing
path segment is the team ID). The ARI format is
`ari:cloud:identity::team/<team-id>`.

Compose the final mapping as a single line of JSON in `.env`:
```env
TEAM_PROJECT_MAP={"ari:cloud:identity::team/<team-id-1>":"<PROJECT_KEY>","ari:cloud:identity::team/<team-id-2>":"<PROJECT_KEY>"}
```

Notes:
- `/rest/api/3/search` was removed in 2025; use `/rest/api/3/search/jql`.
- Use `curl --get --data-urlencode` to keep `&` / spaces out of shell parsing.
- Multiple teams may map to the same project key — issues are still
  distinguished by the Team custom field.


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
