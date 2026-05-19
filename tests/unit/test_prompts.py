from __future__ import annotations

from app.services.llm.prompts import build_extraction_prompt
from app.services.llm.schemas import ExtractionContext


def test_prompt_includes_user_team_and_epics():
    ctx = ExtractionContext(
        user_primary_team_id="ari:cloud:identity::team/abc",
        user_primary_team_name="Backend",
        available_teams=[
            ("ari:cloud:identity::team/abc", "Backend"),
            ("ari:cloud:identity::team/def", "Frontend"),
        ],
        active_epics=[("CATCHUP-1", "OAuth 통합")],
        project_keys=["CATCHUP"],
    )
    p = build_extraction_prompt("OAuth 마무리할게요", ctx)
    assert "Backend" in p.system
    assert "ari:cloud:identity::team/abc" in p.system
    assert "CATCHUP-1: OAuth 통합" in p.system
    assert p.user == "OAuth 마무리할게요"


def test_prompt_handles_empty_context():
    ctx = ExtractionContext()
    p = build_extraction_prompt("점심 메뉴", ctx)
    assert "미지정" in p.system
    assert "(없음)" in p.system
