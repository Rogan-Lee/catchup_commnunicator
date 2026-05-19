from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.config import get_settings
from app.core.logging import configure_logging, get_logger
from app.db.session import dispose_engine
from app.routers import slack_events


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = get_settings()
    configure_logging(level=settings.log_level, environment=settings.environment)
    log = get_logger("app.startup")
    log.info(
        "app.starting",
        environment=settings.environment,
        dry_run=settings.dry_run,
        standup_channels=sorted(settings.standup_channel_ids),
    )
    try:
        yield
    finally:
        await dispose_engine()
        log.info("app.stopped")


def create_app() -> FastAPI:
    settings = get_settings()
    configure_logging(level=settings.log_level, environment=settings.environment)

    app = FastAPI(
        title="Standup → Jira",
        version="0.1.0",
        lifespan=lifespan,
    )
    app.include_router(slack_events.router)

    @app.get("/health")
    async def health() -> dict[str, str]:
        return {"status": "ok", "environment": settings.environment}

    return app


app = create_app()
