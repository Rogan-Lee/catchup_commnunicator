"""Operational CLI.

Usage:
  uv run python -m app.cli reprocess <slack_message_ts> [--channel CHANNEL_ID]
  uv run python -m app.cli accuracy [--days 7]
  uv run python -m app.cli rules export
  uv run python -m app.cli rules import <path.json>
"""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

from sqlalchemy import delete, func, select

from app.db.models import NamingRule, StandupEntry, WorkItem
from app.db.session import session_scope
from app.deps import get_container


async def reprocess(message_ts: str, channel_id: str | None) -> int:
    container = get_container()
    async with session_scope() as session:
        stmt = select(StandupEntry).where(StandupEntry.slack_message_ts == message_ts)
        if channel_id:
            stmt = stmt.where(StandupEntry.channel_id == channel_id)
        entry = await session.scalar(stmt)
        if not entry:
            print(f"no entry found for ts={message_ts}", file=sys.stderr)
            return 1

        # Wipe existing work items, then re-run extraction.
        await session.execute(
            delete(WorkItem).where(WorkItem.standup_entry_id == entry.id)
        )
        await session.delete(entry)
        await session.commit()

        await container.extract_handler.handle_message(
            session,
            channel_id=entry.channel_id,
            message_ts=entry.slack_message_ts,
            author_slack_id=entry.author_slack_id,
            text=entry.raw_text,
            posted_at=entry.posted_at,
        )
        print(f"reprocessed {entry.channel_id}:{entry.slack_message_ts}")
        return 0


async def accuracy(days: int) -> int:
    since = datetime.now(tz=timezone.utc) - timedelta(days=days)
    async with session_scope() as session:
        rows = (
            await session.execute(
                select(WorkItem)
                .join(StandupEntry, StandupEntry.id == WorkItem.standup_entry_id)
                .where(WorkItem.status == "created")
                .where(StandupEntry.posted_at >= since)
            )
        ).scalars().all()

        total = len(rows)
        edited = sum(1 for r in rows if _was_edited_row(r))
        clean = total - edited

        total_count = (
            await session.scalar(select(func.count(WorkItem.id))) or 0
        )
        created = (
            await session.scalar(
                select(func.count(WorkItem.id)).where(WorkItem.status == "created")
            )
            or 0
        )
        failed = (
            await session.scalar(
                select(func.count(WorkItem.id)).where(WorkItem.status == "failed")
            )
            or 0
        )

    pct = (clean / total * 100) if total else 0.0
    print(f"--- last {days} days ---")
    print(f"created             : {total}")
    print(f"  unedited (clean)  : {clean} ({pct:.1f}%)")
    print(f"  edited by user    : {edited}")
    print(f"--- lifetime ---")
    print(f"work items total    : {total_count}")
    print(f"  created           : {created}")
    print(f"  failed            : {failed}")
    return 0


def _was_edited_row(wi: WorkItem) -> bool:
    extracted = wi.extracted_slots or {}
    confirmed = wi.confirmed_slots or {}
    for k, ex_k in (
        ("task_content", "task_content"),
        ("task_type", "task_type"),
        ("parent_feature", "parent_feature"),
        ("parent_issue_key", "parent_issue_hint"),
    ):
        if (confirmed.get(k) or None) != (extracted.get(ex_k) or None):
            return True
    return False


async def export_rules() -> int:
    async with session_scope() as session:
        rows = (await session.execute(select(NamingRule))).scalars().all()
        payload = [
            {
                "project_key": r.project_key,
                "task_type": r.task_type,
                "template": r.template,
            }
            for r in rows
        ]
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0


async def import_rules(path: str) -> int:
    data = json.loads(Path(path).read_text())
    if not isinstance(data, list):
        print("expected a JSON array of rules", file=sys.stderr)
        return 1
    async with session_scope() as session:
        for item in data:
            existing = await session.scalar(
                select(NamingRule).where(
                    NamingRule.project_key == item["project_key"],
                    NamingRule.task_type == item.get("task_type"),
                )
            )
            if existing:
                existing.template = item["template"]
            else:
                session.add(
                    NamingRule(
                        project_key=item["project_key"],
                        task_type=item.get("task_type"),
                        template=item["template"],
                    )
                )
        await session.commit()
    print(f"imported {len(data)} rule(s)")
    return 0


def main() -> None:
    parser = argparse.ArgumentParser(prog="app.cli")
    sub = parser.add_subparsers(dest="cmd", required=True)

    rp = sub.add_parser("reprocess", help="rerun extraction for a slack message")
    rp.add_argument("message_ts")
    rp.add_argument("--channel", default=None)

    ac = sub.add_parser("accuracy", help="show extraction accuracy report")
    ac.add_argument("--days", type=int, default=7)

    rules = sub.add_parser("rules", help="naming-rule import/export")
    rules_sub = rules.add_subparsers(dest="action", required=True)
    rules_sub.add_parser("export")
    imp = rules_sub.add_parser("import")
    imp.add_argument("path")

    args = parser.parse_args()

    if args.cmd == "reprocess":
        rc = asyncio.run(reprocess(args.message_ts, args.channel))
    elif args.cmd == "accuracy":
        rc = asyncio.run(accuracy(args.days))
    elif args.cmd == "rules":
        if args.action == "export":
            rc = asyncio.run(export_rules())
        elif args.action == "import":
            rc = asyncio.run(import_rules(args.path))
        else:
            parser.error("unknown rules action")
            return
    else:
        parser.error("unknown command")
        return

    sys.exit(rc)


if __name__ == "__main__":
    main()
