from __future__ import annotations

from app.services.atlassian.adf import text_to_adf


def test_simple_paragraph():
    doc = text_to_adf("hello")
    assert doc["type"] == "doc"
    assert doc["version"] == 1
    paragraphs = doc["content"]
    assert len(paragraphs) == 1
    assert paragraphs[0]["content"] == [{"type": "text", "text": "hello"}]


def test_multiple_paragraphs():
    doc = text_to_adf("first\n\nsecond")
    assert len(doc["content"]) == 2
    assert doc["content"][0]["content"][0]["text"] == "first"
    assert doc["content"][1]["content"][0]["text"] == "second"


def test_hard_break_within_paragraph():
    doc = text_to_adf("line1\nline2")
    nodes = doc["content"][0]["content"]
    assert {"type": "hardBreak"} in nodes
    texts = [n["text"] for n in nodes if n["type"] == "text"]
    assert texts == ["line1", "line2"]


def test_empty_string_returns_valid_doc():
    doc = text_to_adf("")
    assert doc["type"] == "doc"
    assert doc["content"][0]["type"] == "paragraph"
