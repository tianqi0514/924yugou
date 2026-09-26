"""Reviewable, block-level updates when a report selects a newer analysis run."""

from __future__ import annotations

import re
from copy import deepcopy
from decimal import Decimal, InvalidOperation

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from .analysis import _report, _run
from .analysis_writing import _candidate, _manual_blocks
from .db import AnalysisWritingEvent, ReportVersion, SessionLocal
from .report_pipeline import change_impact, plain, validate_content


router = APIRouter(prefix="/api/projects/{project_id}/analysis/reports/{report_id}/refresh",
                   tags=["推演变化处理"])

CONDITIONS = {
    "需求高于产能": "需求超过合格能力，销售计划受产能限制。",
    "需求低于产能": "需求低于合格能力，销售计划以需求为限。",
    "需求等于产能": "需求与合格能力相等，销售计划与两者一致。",
}
LABELS = {"numbers": "更新数字与引用", "table": "更新结果表",
          "condition": "重写条件句", "config_condition": "更新条件判断", "judgement": "更新供应商判断"}


class RefreshCommit(BaseModel):
    run_id: str
    base_version: int = Field(ge=0)
    operation_ids: list[str] = Field(min_length=1, max_length=100)


class UnsafeUpdate(ValueError):
    pass


def _refs(block: dict) -> list[dict]:
    refs = list(block.get("analysis_refs", []))
    if block.get("type") == "table":
        for row in block.get("children", []):
            for cell in row.get("children", []):
                for paragraph in cell.get("children", []):
                    refs.extend(paragraph.get("analysis_refs", []))
    return refs


def _new_ref(ref: dict, run) -> dict:
    result = run.snapshot["results"].get(ref["result_key"])
    if result is None or result["value"] is None:
        raise UnsafeUpdate("新运行缺少此结果，请补充输入后重新推演")
    return {"run_id": run.id, "result_key": ref["result_key"],
            "value": result["value"], "unit": result["unit"]}


def _is_stale(ref: dict, run) -> bool:
    try:
        return _new_ref(ref, run) != ref
    except UnsafeUpdate:
        return True


def _numeric(refs: list[dict]) -> bool:
    try:
        for ref in refs:
            Decimal(ref["value"])
    except (InvalidOperation, ValueError, TypeError):
        return False
    return bool(refs)


def _substitute(node: dict, pairs: list[tuple[str, str]]) -> None:
    changes: dict[str, str] = {}
    for old, new in pairs:
        if old in changes and changes[old] != new:
            raise UnsafeUpdate("相同旧值对应多个新结果，需要人工确认位置")
        changes[old] = new
    if not changes:
        return
    pattern = re.compile(r"(?<!\d)(?:" + "|".join(re.escape(key) for key in sorted(changes, key=len, reverse=True)) + r")(?!\d)")
    counts = {key: 0 for key in changes}
    for leaf in node.get("children", []):
        if not isinstance(leaf, dict) or not isinstance(leaf.get("text"), str):
            raise UnsafeUpdate("段落包含复杂内容，请在 Plate 中核对")
        leaf["text"] = pattern.sub(lambda match: _take(match.group(), changes, counts), leaf["text"])
    if any(count == 0 for count in counts.values()):
        raise UnsafeUpdate("原数字位置已被编辑，请在 Plate 中核对")


def _take(old: str, changes: dict[str, str], counts: dict[str, int]) -> str:
    counts[old] += 1
    return changes[old]


def _updated_text(session, block: dict, run) -> dict:
    changed = deepcopy(block)
    refs = changed.get("analysis_refs", [])
    if not _numeric(refs):
        raise UnsafeUpdate("正文引用不是可安全替换的数字")
    new_refs = [_new_ref(ref, run) for ref in refs]
    if (block.get("section_id") == "S4" and _condition_phrase(block)
            and len(block.get("children", [])) == 1
            and (set(ref["result_key"] for ref in refs) >= {"N017", "N034", "N035"}
                 or set(ref["result_key"] for ref in refs) >= {"first_year_demand", "qualified_capacity", "planned_sales"})):
        generated, _ = _candidate(session, run, "S4", "computed")
        paragraph = next(item for item in generated if item["type"] == "p" and item.get("analysis_refs"))
        old_phrase = _condition_phrase(block)
        new_phrase = CONDITIONS.get(run.snapshot["condition"])
        if not new_phrase:
            raise UnsafeUpdate("本次运行没有可用的需求条件")
        changed["children"] = [{"text": plain(paragraph).replace(new_phrase, old_phrase)}]
        changed["analysis_refs"] = new_refs
        return changed
    _substitute(changed, [(f"{old['value']}{old['unit']}", f"{new['value']}{new['unit']}")
                          for old, new in zip(refs, new_refs)])
    changed["analysis_refs"] = new_refs
    return changed


def _updated_table(block: dict, run) -> dict:
    changed = deepcopy(block)
    if not changed.get("analysis_refs"):
        raise UnsafeUpdate("结果表没有绑定运行")
    changed["analysis_refs"] = [_new_ref(ref, run) for ref in changed["analysis_refs"]]
    for row in changed.get("children", []):
        for cell in row.get("children", []):
            for paragraph in cell.get("children", []):
                refs = paragraph.get("analysis_refs", [])
                if not refs:
                    continue
                if not _numeric(refs):
                    raise UnsafeUpdate("表格中包含非数值引用")
                new_refs = [_new_ref(ref, run) for ref in refs]
                _substitute(paragraph, [(f"{old['value']}{old['unit']}", f"{new['value']}{new['unit']}")
                                        for old, new in zip(refs, new_refs)])
                paragraph["analysis_refs"] = new_refs
    return changed


def _condition_phrase(block: dict) -> str | None:
    text = plain(block)
    found = [phrase for phrase in CONDITIONS.values() if phrase in text]
    return found[0] if len(found) == 1 else None


def _updated_condition(block: dict, run) -> dict:
    old = _condition_phrase(block)
    new = CONDITIONS.get(run.snapshot["condition"])
    if not old or not new or old == new:
        raise UnsafeUpdate("条件句需要人工核对")
    changed = deepcopy(block)
    _substitute(changed, [(old, new)])
    return changed


def _configured_condition(block: dict, run) -> tuple[dict, dict] | None:
    configuration = run.snapshot.get("configuration") or {}
    section = next((item for item in configuration.get("sections", [])
                    if item["id"] == block.get("section_id")), None)
    if section is None or block.get("type") != "p":
        return None
    for condition in section.get("conditions", []):
        if plain(block) in {condition["when_true"], condition["when_false"]}:
            outcome = run.snapshot.get("condition_results", {}).get(
                f"{section['id']}:{condition['id']}")
            if outcome is not None:
                return condition, outcome
    return None


def _updated_config_condition(block: dict, run) -> dict:
    matched = _configured_condition(block, run)
    if matched is None or matched[1]["text"] is None:
        raise UnsafeUpdate("条件配置或本次结果不可用，请人工核对")
    _, outcome = matched
    changed = deepcopy(block)
    changed["children"] = [{"text": outcome["text"]}]
    changed["analysis_refs"] = [_new_ref({"result_key": key}, run) for key in outcome["deps"]]
    return changed


def _updated_judgement(session, block: dict, run) -> dict:
    generated, _ = _candidate(session, run, "S7.1", "computed")
    new = next(item for item in generated if item["type"] == "p")
    changed = deepcopy(block)
    changed["children"] = new["children"]
    if new.get("analysis_refs"):
        changed["analysis_refs"] = new["analysis_refs"]
    else:
        changed.pop("analysis_refs", None)
    changed.pop("source_refs", None)
    return changed


def _transform(session, block: dict, run, kind: str) -> dict:
    if kind == "numbers":
        return _updated_text(session, block, run)
    if kind == "table":
        return _updated_table(block, run)
    if kind == "condition":
        return _updated_condition(block, run)
    if kind == "config_condition":
        return _updated_config_condition(block, run)
    if kind == "judgement":
        return _updated_judgement(session, block, run)
    raise UnsafeUpdate("更新动作不存在")


def _actions(session, report, run) -> list[dict]:
    protected: set[str] = set()
    for section in {block.get("section_id") for block in report.content if block.get("section_id")}:
        protected.update(block.get("id") for block in _manual_blocks(session, report, section)[0])
    actions = []
    for position, block in enumerate(report.content, 1):
        block_id = block.get("id")
        if not block_id:
            continue
        refs = _refs(block)
        stale = any(_is_stale(ref, run) for ref in refs)
        configured = _configured_condition(block, run)
        config_condition_stale = bool(configured and configured[1]["text"] != plain(block))
        condition_stale = (block.get("section_id") == "S4" and
                           _condition_phrase(block) is not None and
                           _condition_phrase(block) != CONDITIONS.get(run.snapshot["condition"]))
        if not stale and not condition_stale and not config_condition_stale:
            continue
        manual = block.get("origin") != "guided" or block_id in protected
        if manual:
            actions.append({"id": f"manual:{block_id}", "kind": "manual", "label": "手工核对",
                            "block_id": block_id, "position": position,
                            "section_id": block.get("section_id"), "before": plain(block),
                            "after": None, "selectable": False,
                            "reason": "此段经过人工修改，请在正文中核对新运行结果与判断"})
            continue
        kinds = []
        if configured and (stale or config_condition_stale):
            kinds.append("config_condition")
        elif stale:
            if block.get("type") == "table":
                kinds.append("table")
            elif block.get("section_id") == "S7.1" and any(ref["result_key"] == "supplier" for ref in refs):
                kinds.append("judgement")
            elif block.get("type") == "p" and _numeric(refs):
                kinds.append("numbers")
        if condition_stale:
            kinds.append("condition")
        if not kinds:
            actions.append({"id": f"manual:{block_id}", "kind": "manual", "label": "手工核对",
                            "block_id": block_id, "position": position,
                            "section_id": block.get("section_id"), "before": plain(block),
                            "after": None, "selectable": False,
                            "reason": "当前引用无法安全替换，请在正文中处理"})
        for kind in kinds:
            try:
                changed = _transform(session, block, run, kind)
                actions.append({"id": f"{kind}:{block_id}", "kind": kind,
                                "label": LABELS[kind], "block_id": block_id,
                                "position": position, "section_id": block.get("section_id"),
                                "before": plain(block), "after": plain(changed),
                                "selectable": True, "reason": None,
                                "result_keys": sorted({ref["result_key"] for ref in refs})})
            except UnsafeUpdate as exc:
                actions.append({"id": f"manual:{block_id}", "kind": "manual", "label": "手工核对",
                                "block_id": block_id, "position": position,
                                "section_id": block.get("section_id"), "before": plain(block),
                                "after": None, "selectable": False, "reason": str(exc)})
    unique: list[dict] = []
    seen: set[str] = set()
    for action in actions:
        if action["id"] not in seen:
            unique.append(action)
            seen.add(action["id"])
    return unique


@router.get("/preview")
def refresh_preview(project_id: str, report_id: str):
    with SessionLocal() as session:
        report = _report(session, project_id, report_id)
        if not report.analysis_run_id:
            raise HTTPException(409, "报告尚未选择推演结果")
        run = _run(session, project_id, report.analysis_run_id)
        return {"report_version": report.version, "run_id": run.id,
                "actions": _actions(session, report, run)}


@router.post("/commit")
def refresh_commit(project_id: str, report_id: str, body: RefreshCommit):
    if len(body.operation_ids) != len(set(body.operation_ids)):
        raise HTTPException(400, "请勿重复选择同一更新动作")
    with SessionLocal.begin() as session:
        report = _report(session, project_id, report_id, lock=True)
        run = _run(session, project_id, body.run_id)
        if report.version != body.base_version or report.analysis_run_id != run.id:
            raise HTTPException(409, "报告或推演依据已变化，请重新预览")
        offered = {action["id"]: action for action in _actions(session, report, run)
                   if action["selectable"]}
        if any(operation_id not in offered for operation_id in body.operation_ids):
            raise HTTPException(409, "更新动作已失效，请重新预览")
        touched_sections = {offered[operation_id]["section_id"] for operation_id in body.operation_ids}
        manual_ids = {section: {block.get("id") for block in _manual_blocks(session, report, section)[0]}
                      for section in touched_sections if section}
        content = deepcopy(report.content)
        by_id = {block.get("id"): block for block in content}
        ordered = sorted((offered[operation_id] for operation_id in body.operation_ids),
                         key=lambda action: (action["position"],
                                             {"numbers": 0, "table": 0, "judgement": 0,
                                              "condition": 1, "config_condition": 1}[action["kind"]]))
        for action in ordered:
            block = by_id[action["block_id"]]
            changed = _transform(session, block, run, action["kind"])
            content[action["position"] - 1] = changed
            by_id[action["block_id"]] = changed
        validate_content(content)
        from .main import (_analysis_report_state, _current_facts, _report_dict,
                           _validate_corpus_article_content, _validate_writing_refs)
        _validate_writing_refs(session, project_id, content)
        _validate_corpus_article_content(session, project_id, report_id, content)
        _analysis_report_state(session, report, content)
        changes = change_impact(report.content, content)
        report.content = content
        report.reviewed_hash = None
        report.version += 1
        session.add(ReportVersion(report_id=report.id, version=report.version, content=content,
                                  bound_facts=report.bound_facts, analysis_run_id=run.id))
        session.add(AnalysisWritingEvent(project_id=project_id, report_id=report_id,
                    report_version=report.version, run_id=run.id, action="apply_refresh",
                    payload={"operations": [{"id": action["id"], "kind": action["kind"],
                                             "block_id": action["block_id"],
                                             "before": action["before"],
                                             "after": plain(by_id[action["block_id"]])}
                                            for action in ordered]}))
        for section in touched_sections:
            if not section:
                continue
            session.add(AnalysisWritingEvent(project_id=project_id, report_id=report_id,
                        report_version=report.version, run_id=run.id, action="refresh_baseline",
                        section_id=section,
                        payload={"block_ids": [block.get("id") for block in content
                                               if block.get("section_id") == section],
                                 "manual_block_ids": sorted(manual_ids[section])}))
        session.flush()
        return {"report": _report_dict(report, _current_facts(session, project_id), session),
                "changes": changes}
