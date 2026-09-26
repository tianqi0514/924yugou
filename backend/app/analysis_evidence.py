"""Check project-owned source bindings for versioned chapter requirements."""

from __future__ import annotations

from decimal import Decimal, InvalidOperation

from sqlalchemy import select

from .db import ProjectFact


def _same_value(left: str | None, right: str | None) -> bool:
    if left is None or right is None:
        return left == right
    try:
        return Decimal(left) == Decimal(right)
    except InvalidOperation:
        return left == right


def section_evidence(session, project_id: str, run, section: dict) -> list[dict]:
    from .main import _reviewed_fact_binding

    rows: list[dict] = []
    for key in section.get("evidence_keys", []):
        result = run.snapshot["results"].get(key)
        fact = session.scalar(select(ProjectFact).where(ProjectFact.project_id == project_id,
                                                        ProjectFact.key == key))
        reason = None
        binding = None
        if result is None or result["value"] is None:
            reason = "本次运行缺少字段值"
        elif fact is None:
            reason = "本项目尚未建立同名事实"
        elif not _same_value(fact.value_text, result["value"]) or fact.unit != result["unit"]:
            reason = "项目事实与本次方案值或单位不一致"
        else:
            binding = _reviewed_fact_binding(session, project_id, fact)
            if binding is None:
                reason = "项目事实原件位置尚未核对"
        rows.append({"key": key, "label": result["label"] if result else key,
                     "status": "VERIFIED" if reason is None else "MISSING",
                     "reason": reason, "fact_revision": fact.revision if fact else None,
                     "document_id": binding[1].id if binding else None,
                     "source_refs": binding[0].source_refs if binding else []})
    return rows
