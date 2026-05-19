from __future__ import annotations

from app.services.llm.schemas import ExtractionContext, Prompt

SYSTEM_PROMPT = """당신은 한국 소프트웨어 개발팀의 스탠드업 메시지에서 작업 항목을 추출하는 도구입니다.

다음 규칙을 엄격히 따르세요:
1. 사용자 메시지에서 별개의 작업 항목 N개를 식별합니다.
2. 각 항목마다 아래 슬롯을 추출합니다. 모르는 값은 null로 둡니다.
3. task_content는 30자 이내로 요약합니다.
4. parent_issue_hint는 메시지에 명시된 티켓 키(예: AUTH-42)이거나 추정되는 부모 작업 설명입니다.
5. 활성 에픽 목록을 참고하여 가장 관련성 높은 에픽을 parent_issue_hint로 추천합니다.
6. 응답은 반드시 지정된 JSON 스키마를 따라야 합니다.

작업 구분 정의:
- Feature: 신규 기능 개발
- Bug: 버그 수정
- Improvement: 기존 기능 개선, UX 보완
- Refactoring: 코드 구조 개선 (동작 변경 없음)
- Tech Debt: 기술 부채 청산, 마이그레이션
- Docs: 문서화, 가이드 작성
- Spike: 조사, POC, 기술 검토 (time-box)
- DevOps: 인프라, CI/CD, 배포
"""


def build_extraction_prompt(message: str, context: ExtractionContext) -> Prompt:
    user_team = (
        f"사용자 소속 팀: {context.user_primary_team_name} (id: {context.user_primary_team_id})"
        if context.user_primary_team_id
        else "사용자 소속 팀: 미지정"
    )

    teams_section = (
        "팀 목록:\n"
        + ("\n".join(f"- {name} (id: {tid})" for tid, name in context.available_teams) or "- (없음)")
    )
    epics_section = (
        "최근 활성 에픽 (해당 팀):\n"
        + ("\n".join(f"- {key}: {summary}" for key, summary in context.active_epics) or "- (없음)")
    )

    full_system = f"{SYSTEM_PROMPT}\n\n{user_team}\n\n{teams_section}\n\n{epics_section}"
    return Prompt(system=full_system, user=message)
