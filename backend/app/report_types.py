"""Small, versioned starter outlines. They are editable writing structures, not approvals."""

from __future__ import annotations

from copy import deepcopy

from fastapi import HTTPException


# These are navigation defaults. A project still needs its own facts, sources and
# published chapter contract before any chapter can be drafted or reviewed.
_TEMPLATES = {
    "government_feasibility": {
        "label": "政府投资可行性研究报告", "stage": "可行性研究", "version": "2026-09-27.1",
        "sections": [
            {"id": "overview", "title": "项目概况", "kind": "mixed"},
            {"id": "necessity", "title": "建设背景与必要性", "kind": "narrative"},
            {"id": "demand", "title": "需求分析与建设规模", "kind": "mixed"},
            {"id": "site", "title": "选址与建设条件", "kind": "narrative"},
            {"id": "plan", "title": "建设方案", "kind": "mixed"},
            {"id": "environment", "title": "环境与资源影响", "kind": "narrative"},
            {"id": "investment", "title": "投资估算与资金筹措", "kind": "mixed"},
            {"id": "risk", "title": "风险与实施安排", "kind": "narrative"},
            {"id": "conclusion", "title": "结论与待核事项", "kind": "narrative"},
        ],
    },
    "enterprise_feasibility": {
        "label": "企业投资可行性研究报告", "stage": "可行性研究", "version": "2026-09-27.1",
        "sections": [
            {"id": "overview", "title": "项目概况", "kind": "mixed"},
            {"id": "market", "title": "市场需求与项目定位", "kind": "mixed"},
            {"id": "capacity", "title": "建设规模与产品方案", "kind": "mixed"},
            {"id": "site", "title": "选址与要素保障", "kind": "narrative"},
            {"id": "technology", "title": "技术与建设方案", "kind": "mixed"},
            {"id": "operation", "title": "运营与实施方案", "kind": "narrative"},
            {"id": "investment", "title": "投资与财务分析", "kind": "mixed"},
            {"id": "risk", "title": "风险分析", "kind": "narrative"},
            {"id": "conclusion", "title": "结论与待核事项", "kind": "narrative"},
        ],
    },
    "custom": {"label": "自定义报告", "stage": "自定义", "version": "2026-09-27.1", "sections": []},
}


def report_types() -> list[dict]:
    return [{"id": key, **deepcopy(value)} for key, value in _TEMPLATES.items()]


def report_type(key: str) -> dict:
    if key not in _TEMPLATES:
        raise HTTPException(400, "报告类型不存在")
    return {"id": key, **deepcopy(_TEMPLATES[key])}
