from __future__ import annotations

from typing import Any


def text_to_adf(text: str) -> dict[str, Any]:
    """Convert plain text (with newlines) to a minimal ADF document.

    Each paragraph (separated by blank line) becomes a paragraph node; line
    breaks within a paragraph become hard_break nodes.
    """
    paragraphs = [p for p in (text or "").split("\n\n")]
    content: list[dict[str, Any]] = []
    for para in paragraphs:
        nodes: list[dict[str, Any]] = []
        lines = para.split("\n")
        for i, line in enumerate(lines):
            if line:
                nodes.append({"type": "text", "text": line})
            if i < len(lines) - 1:
                nodes.append({"type": "hardBreak"})
        if not nodes:
            nodes = [{"type": "text", "text": ""}]
        content.append({"type": "paragraph", "content": nodes})

    if not content:
        content = [{"type": "paragraph", "content": [{"type": "text", "text": ""}]}]

    return {"type": "doc", "version": 1, "content": content}
