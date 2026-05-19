from __future__ import annotations

from typing import Any

from app.core.logging import get_logger

_log = get_logger("metrics")


def emit(name: str, **fields: Any) -> None:
    """Emit a structured metrics event.

    For now this is a structlog line — downstream log pipelines (e.g. Datadog,
    Grafana Loki) can scrape on `metric=<name>`. A real Prometheus exporter
    can wrap this later without changing call sites.
    """
    _log.info("metric", metric=name, **fields)
