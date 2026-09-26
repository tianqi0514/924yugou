"""Version-safe chapter operations on the existing Plate document structure."""

from __future__ import annotations

from copy import deepcopy
from uuid import uuid4

from .report_pipeline import plain


def _groups(content: list[dict]) -> tuple[list[dict], list[list[dict]]]:
    starts = [index for index, block in enumerate(content) if block.get("type") == "h2"]
    if not starts:
        return content, []
    return content[:starts[0]], [content[start:starts[index + 1] if index + 1 < len(starts) else len(content)]
                                for index, start in enumerate(starts)]


def change_section(content: list[dict], action: str, *, heading_id: str | None = None,
                   title: str | None = None, direction: str | None = None) -> tuple[list[dict], str]:
    """Reorder whole H2 groups without changing any existing block identity or link."""
    updated = deepcopy(content)
    before, groups = _groups(updated)
    if action == "add":
        name = (title or "").strip()
        if not name or len(name) > 80 or "\n" in name:
            raise ValueError("章节标题须为 1 至 80 个字符")
        if any(plain(group[0]).strip() == name for group in groups):
            raise ValueError("章节标题已存在")
        section_id = f"manual-{uuid4()}"
        new_id = str(uuid4())
        groups.append([
            {"type": "h2", "id": new_id, "section_id": section_id,
             "origin": "manual", "children": [{"text": name}]},
            {"type": "p", "id": str(uuid4()), "section_id": section_id,
             "children": [{"text": ""}]},
        ])
        if not groups[:-1] and len(before) == 2 and before[1].get("type") == "p" and not plain(before[1]).strip():
            before = before[:1]
        return [*before, *[block for group in groups for block in group]], new_id
    position = next((index for index, group in enumerate(groups)
                     if group[0].get("id") == heading_id and heading_id), None)
    if position is None:
        raise ValueError("章节不存在或尚未保存，请刷新后重试")
    if action == "rename":
        name = (title or "").strip()
        if not name or len(name) > 80 or "\n" in name:
            raise ValueError("章节标题须为 1 至 80 个字符")
        if any(index != position and plain(group[0]).strip() == name for index, group in enumerate(groups)):
            raise ValueError("章节标题已存在")
        heading = groups[position][0]
        heading["children"] = [{"text": name}]
        heading["origin"] = "manual"
    elif action == "move":
        offset = {"up": -1, "down": 1}.get(direction)
        if offset is None or not 0 <= position + offset < len(groups):
            raise ValueError("章节已在边界位置")
        groups[position], groups[position + offset] = groups[position + offset], groups[position]
    else:
        raise ValueError("不支持的章节操作")
    return [*before, *[block for group in groups for block in group]], heading_id
