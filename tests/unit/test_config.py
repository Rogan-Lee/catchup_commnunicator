from __future__ import annotations

from app.config import Settings


def test_task_type_list_defaults_to_korean():
    s = Settings()
    assert s.task_type_list[0] == "기능"
    assert "버그" in s.task_type_list


def test_task_type_list_parses_and_strips():
    s = Settings(task_types=" 기획 , 디자인 ,개발, ")
    assert s.task_type_list == ["기획", "디자인", "개발"]


def test_task_type_list_empty():
    s = Settings(task_types="")
    assert s.task_type_list == []
