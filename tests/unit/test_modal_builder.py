from __future__ import annotations

import json
import uuid

from app.db.models import StandupEntry, WorkItem
from app.services.atlassian.types import Team
from app.services.slack.modal_builder import (
    BID_ISSUE_TYPE,
    BID_PARENT_ISSUE,
    BID_PROJECT,
    BID_TASK_CONTENT,
    BID_TASK_TYPE,
    BID_TEAM,
    build_work_item_modal,
    parse_modal_values,
    unpack_metadata,
)


def _wi(**slots):
    entry = StandupEntry(
        channel_id="C1",
        slack_message_ts="1700000000.000100",
        author_slack_id="U1",
        raw_text="...",
        posted_at=None,  # type: ignore[arg-type]
    )
    entry.id = uuid.uuid4()
    wi = WorkItem(
        standup_entry_id=entry.id,
        sequence_no=1,
        extracted_slots=slots,
    )
    wi.id = uuid.uuid4()
    wi.standup_entry = entry
    wi.task_content = slots.get("task_content")
    wi.task_type = slots.get("task_type")
    wi.jira_team_id = slots.get("team_id")
    wi.jira_project_key = slots.get("project_key")
    wi.parent_feature = slots.get("parent_feature")
    wi.parent_issue_key = slots.get("parent_issue_key")
    return wi


def test_modal_has_all_blocks_and_metadata():
    wi = _wi(task_content="OAuth", task_type="Feature", parent_feature="로그인")
    teams = [Team(id="t1", name="Backend"), Team(id="t2", name="Frontend")]

    view = build_work_item_modal(wi, teams=teams, project_keys=["CATCHUP", "FRONT"])

    assert view["callback_id"] == "work_item_submit"
    meta = json.loads(view["private_metadata"])
    assert meta["work_item_id"] == str(wi.id)
    assert meta["channel_id"] == "C1"
    assert meta["thread_ts"] == "1700000000.000100"

    block_ids = {b.get("block_id") for b in view["blocks"]}
    for bid in (BID_TEAM, BID_PROJECT, BID_TASK_TYPE, BID_TASK_CONTENT, BID_PARENT_ISSUE):
        assert bid in block_ids


def test_modal_prefills_initial_values():
    wi = _wi(
        task_content="OAuth", task_type="Feature", team_id="t1", parent_feature="로그인"
    )
    teams = [Team(id="t1", name="Backend")]
    view = build_work_item_modal(wi, teams=teams, project_keys=["CATCHUP"])

    by_block = {b["block_id"]: b for b in view["blocks"] if "block_id" in b}
    assert by_block[BID_TEAM]["element"]["initial_option"]["value"] == "t1"
    assert by_block[BID_TASK_TYPE]["element"]["initial_option"]["value"] == "Feature"
    assert by_block[BID_TASK_CONTENT]["element"]["initial_value"] == "OAuth"


def test_modal_omits_initial_when_no_match():
    wi = _wi(task_content="X", task_type="Feature", team_id="unknown")
    view = build_work_item_modal(
        wi, teams=[Team(id="t1", name="Backend")], project_keys=["CATCHUP"]
    )
    by_block = {b["block_id"]: b for b in view["blocks"] if "block_id" in b}
    assert "initial_option" not in by_block[BID_TEAM]["element"]


def test_issue_type_block_present_when_types_supplied():
    wi = _wi(task_content="X")
    view = build_work_item_modal(
        wi,
        teams=[],
        project_keys=["CAM"],
        issue_types=["Story", "Task", "Bug"],
    )
    by_block = {b["block_id"]: b for b in view["blocks"] if "block_id" in b}
    assert BID_ISSUE_TYPE in by_block
    opts = by_block[BID_ISSUE_TYPE]["element"]["options"]
    assert [o["value"] for o in opts] == ["Story", "Task", "Bug"]


def test_issue_type_block_omitted_when_no_types():
    wi = _wi(task_content="X")
    view = build_work_item_modal(wi, teams=[], project_keys=["CAM"])
    by_block = {b.get("block_id") for b in view["blocks"]}
    assert BID_ISSUE_TYPE not in by_block


def test_task_type_options_are_customizable():
    wi = _wi(task_content="X")
    view = build_work_item_modal(
        wi,
        teams=[],
        project_keys=["CAM"],
        task_types=["기획", "디자인", "개발"],
    )
    by_block = {b["block_id"]: b for b in view["blocks"] if "block_id" in b}
    opts = by_block[BID_TASK_TYPE]["element"]["options"]
    assert [o["value"] for o in opts] == ["기획", "디자인", "개발"]


def test_batch_modal_has_block_per_item_and_metadata():
    from app.services.slack.modal_builder import build_batch_modal

    items = [_wi(task_content="A", task_type="기능"), _wi(task_content="B")]
    view = build_batch_modal(
        items, project_keys=["CAM"], issue_types=["Story", "Task"], task_types=["기능", "버그"]
    )
    assert view["callback_id"] == "work_item_batch_submit"
    meta = json.loads(view["private_metadata"])
    assert meta["item_ids"] == [str(items[0].id), str(items[1].id)]
    block_ids = {b.get("block_id") for b in view["blocks"]}
    assert {"b_tc_1", "b_pf_1", "b_tt_1", "b_it_1", "b_tc_2"} <= block_ids


def test_parse_batch_values_maps_by_index():
    from app.services.slack.modal_builder import (
        BID_PROJECT,
        build_batch_modal,
        parse_batch_values,
    )

    items = [_wi(task_content="A"), _wi(task_content="B")]
    build_batch_modal(items, project_keys=["CAM"])
    view = {
        "private_metadata": json.dumps(
            {"item_ids": [str(items[0].id), str(items[1].id)]}
        ),
        "state": {
            "values": {
                BID_PROJECT: {"project_select": {"selected_option": {"value": "CAM"}}},
                "b_tc_1": {"e_tc": {"value": "수정된 A"}},
                "b_pf_1": {"e_pf": {"value": "로그인"}},
                "b_tt_1": {"e_tt": {"selected_option": {"value": "기능"}}},
                "b_tc_2": {"e_tc": {"value": "B"}},
            }
        },
    }
    rows = parse_batch_values(view)
    assert len(rows) == 2
    assert rows[0]["work_item_id"] == str(items[0].id)
    assert rows[0]["task_content"] == "수정된 A"
    assert rows[0]["parent_feature"] == "로그인"
    assert rows[0]["task_type"] == "기능"
    assert rows[0]["project_key"] == "CAM"
    assert rows[1]["task_content"] == "B"


def test_parse_modal_values_reads_issue_type():
    view = {
        "state": {
            "values": {
                BID_ISSUE_TYPE: {
                    "issue_type_select": {"selected_option": {"value": "Story"}}
                },
                BID_TASK_CONTENT: {"task_content_input": {"value": "X"}},
            }
        }
    }
    assert parse_modal_values(view)["issue_type"] == "Story"


def test_parse_modal_values_reads_state():
    view = {
        "state": {
            "values": {
                BID_TEAM: {"team_select": {"selected_option": {"value": "t1"}}},
                BID_PROJECT: {"project_select": {"selected_option": {"value": "CATCHUP"}}},
                BID_TASK_TYPE: {"task_type_select": {"selected_option": {"value": "Bug"}}},
                "parent_feature_block": {"parent_feature_input": {"value": "로그인"}},
                BID_TASK_CONTENT: {"task_content_input": {"value": " OAuth "}},
                BID_PARENT_ISSUE: {
                    "parent_issue_input": {"selected_option": {"value": "CATCHUP-42"}}
                },
            }
        }
    }
    out = parse_modal_values(view)
    assert out == {
        "team_id": "t1",
        "project_key": "CATCHUP",
        "task_type": "Bug",
        "issue_type": None,
        "parent_feature": "로그인",
        "task_content": "OAuth",
        "parent_issue_key": "CATCHUP-42",
    }


def test_modal_parent_block_is_external_select_with_candidates():
    wi = _wi(task_content="X")
    view = build_work_item_modal(
        wi,
        teams=[Team(id="t1", name="Backend")],
        project_keys=["CATCHUP"],
        parent_candidates=[("CATCHUP-1", "OAuth 통합"), ("CATCHUP-2", "결제")],
    )
    parent_block = next(b for b in view["blocks"] if b.get("block_id") == BID_PARENT_ISSUE)
    assert parent_block["element"]["type"] == "external_select"
    assert parent_block["element"]["min_query_length"] == 1
    assert parent_block["optional"] is True


def test_modal_parent_initial_option_when_key_provided():
    wi = _wi(task_content="X", parent_issue_key="CATCHUP-42")
    view = build_work_item_modal(
        wi,
        teams=[Team(id="t1", name="Backend")],
        project_keys=["CATCHUP"],
        parent_candidates=[("CATCHUP-42", "OAuth")],
    )
    parent_block = next(b for b in view["blocks"] if b.get("block_id") == BID_PARENT_ISSUE)
    assert parent_block["element"]["initial_option"]["value"] == "CATCHUP-42"


def test_parse_handles_empty_state():
    out = parse_modal_values({"state": {"values": {}}})
    assert all(v is None for v in out.values())


def test_unpack_metadata_invalid_json():
    assert unpack_metadata({"private_metadata": "{not json"}) == {}
