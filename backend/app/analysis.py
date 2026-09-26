"""Versioned, project-owned scenario inputs and deterministic run snapshots."""

from __future__ import annotations

import hashlib
import json
import re
from copy import deepcopy
from decimal import Decimal, InvalidOperation
from types import SimpleNamespace
from uuid import uuid4

from fastapi import APIRouter, Body, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select

from .corpus_storage import CorpusArchive, CorpusRecord
from .db import AnalysisConfig, AnalysisRun, AnalysisScenario, AnalysisWritingEvent, Project, ProjectFact, ReportDraft, ReportVersion, RuleRecord, SessionLocal
from .rules import EvalValue, RuleError, evaluate, sort_rules, unit_dimension, unit_signature


router = APIRouter(prefix="/api/projects/{project_id}/analysis", tags=["方案推演"])

# These are audited projections of the historical R003–R006 chain. The rules and
# original values are loaded from CorpusRecord; the unit/period normalization is
# explicit because historical "套" means a first-year count in several nodes.
CY_BLUEPRINT_VERSION = "cy-capacity-s4-v1"
CY_INPUTS = (
    ("N017", "套/年", "demand"), ("N025", "条", "capacity"),
    ("N026", "天/年", "capacity"), ("N027", "班/天", "capacity"),
    ("N028", "小时/班", "capacity"), ("N029", "套/条·小时", "capacity"),
    ("N030", "", "capacity"), ("N031", "", "capacity"),
)
CY_OUTPUTS = (
    ("N032", "条·小时/年", "capacity"), ("N033", "套/年", "capacity"),
    ("N034", "套/年", "capacity"), ("N035", "套/年", "sales"),
)
CY_RULE_IDS = ("R003", "R004", "R005", "R006")


class ScenarioCreate(BaseModel):
    name: str = Field(min_length=1, max_length=160)
    source: str = Field(pattern="^(historical|project|copy|config)$")
    copy_from: str | None = None
    config_id: str | None = None


class ScenarioChange(BaseModel):
    base_revision: int = Field(ge=1)
    changes: dict[str, str | None] = Field(default_factory=dict, max_length=80)
    name: str | None = Field(default=None, min_length=1, max_length=160)


class RunCreate(BaseModel):
    scenario_revision: int = Field(ge=1)
    request_key: str = Field(min_length=8, max_length=80, pattern=r"^[A-Za-z0-9_-]+$")


class RunSelection(BaseModel):
    run_id: str
    base_version: int = Field(ge=0)


def _project(session, project_id: str) -> Project:
    project = session.get(Project, project_id)
    if project is None:
        raise HTTPException(404, "项目不存在")
    return project


def _scenario(session, project_id: str, scenario_id: str, *, lock: bool = False) -> AnalysisScenario:
    query = select(AnalysisScenario).where(AnalysisScenario.id == scenario_id,
                                           AnalysisScenario.project_id == project_id)
    item = session.scalar(query.with_for_update() if lock else query)
    if item is None:
        raise HTTPException(404, "方案不存在")
    return item


def _run(session, project_id: str, run_id: str) -> AnalysisRun:
    item = session.scalar(select(AnalysisRun).where(AnalysisRun.id == run_id,
                                                    AnalysisRun.project_id == project_id))
    if item is None:
        raise HTTPException(404, "推演记录不存在")
    return item


def _published_config(session, project_id: str, config_id: str) -> AnalysisConfig:
    item = session.scalar(select(AnalysisConfig).where(
        AnalysisConfig.id == config_id, AnalysisConfig.project_id == project_id,
        AnalysisConfig.status == "PUBLISHED"))
    if item is None:
        raise HTTPException(404, "已发布配置不存在")
    return item


def _record(session, corpus_id: str, category: int, semantic_id: str) -> CorpusRecord:
    item = session.scalar(select(CorpusRecord).where(CorpusRecord.corpus_id == corpus_id,
                      CorpusRecord.category_number == category,
                      CorpusRecord.semantic_id == semantic_id))
    if item is None:
        raise HTTPException(409, f"历史资料缺少 {semantic_id}")
    return item


def _source_ref(corpus_id: str, version: str, row: CorpusRecord) -> dict:
    return {"corpus_id": corpus_id, "corpus_version": version,
            "category_id": f"CAT-{row.category_number:02d}", "artifact_id": row.artifact_id,
            "record_id": row.record_id, "semantic_id": row.semantic_id,
            "location": row.location}


def _historical_blueprint(session, project: Project) -> tuple[list[dict], list[dict], dict, str, str]:
    if project.corpus is None:
        raise HTTPException(409, "当前项目没有内置历史资料")
    archive = session.get(CorpusArchive, project.corpus.corpus_id)
    if archive is None:
        raise HTTPException(409, "历史语料版本不存在")
    corpus_id, version = archive.id, archive.version
    definitions: list[dict] = []
    inputs: dict[str, dict] = {}
    for key, unit, group in (*CY_INPUTS, *CY_OUTPUTS):
        row = _record(session, corpus_id, 12, key)
        source = _source_ref(corpus_id, version, row)
        p = row.payload
        computed = key in {item[0] for item in CY_OUTPUTS}
        definitions.append({"key": key, "label": p.get("label") or key,
                            "data_type": "decimal" if key in {"N030", "N031"} else "integer",
                            "unit": unit, "original_unit": p.get("unit") or "",
                            "input_format": "percentage" if key in {"N030", "N031"} else "number",
                            "group": group, "computed": computed, "source_ref": source,
                            "caliber": p.get("caliber") or "", "period": p.get("period") or ""})
        if not computed:
            value = p.get("decimal_value")
            inputs[key] = {"value": str(value) if value is not None else None,
                           "origin": "historical_reference", "source_ref": source,
                           "review_status": "unverified"}
    chunk = _record(session, corpus_id, 9, "C0083")
    if "禾进装备" not in str(chunk.payload.get("text") or ""):
        raise HTTPException(409, "历史供应商来源与已审核映射不符")
    definitions.extend([
        {"key": "supplier", "label": "设备供应商", "data_type": "text", "unit": "",
         "group": "supplier", "computed": False, "source_ref": _source_ref(corpus_id, version, chunk)},
        {"key": "supplier_quote", "label": "当前方案设备报价", "data_type": "decimal", "unit": "元/条",
         "group": "supplier", "computed": False, "source_ref": None},
    ])
    inputs["supplier"] = {"value": "禾进装备", "origin": "historical_reference",
                          "source_ref": _source_ref(corpus_id, version, chunk),
                          "review_status": "unverified"}
    # A historical quote must not become a current quote merely by copying the scenario.
    inputs["supplier_quote"] = {"value": None, "origin": "missing",
                                 "source_ref": None, "review_status": "unverified"}
    rules = []
    for key in CY_RULE_IDS:
        row = _record(session, corpus_id, 13, key)
        p = row.payload
        rules.append({"id": key, "name": p.get("narrative_zh") or key,
                      "target_key": p["target"], "expression": p["expr"], "deps": p["deps"],
                      "version": hashlib.sha256(json.dumps(p, sort_keys=True, ensure_ascii=False).encode()).hexdigest()[:16],
                      "source_ref": _source_ref(corpus_id, version, row)})
    return definitions, rules, inputs, corpus_id, version


def _project_blueprint(session, project: Project) -> tuple[list[dict], list[dict], dict, None, None]:
    facts = session.scalars(select(ProjectFact).where(ProjectFact.project_id == project.id).order_by(ProjectFact.key)).all()
    project_rules = session.scalars(select(RuleRecord).where(RuleRecord.project_id == project.id)).all()
    targets = {rule.target_key for rule in project_rules}
    definitions = [{"key": fact.key, "label": fact.label, "data_type": fact.data_type,
                    "unit": fact.unit, "group": "project", "computed": fact.key in targets,
                    "source_ref": {"project_id": project.id, "fact_key": fact.key,
                                   "revision": fact.revision, "source": fact.source},
                    "caliber": fact.caliber, "as_of": fact.as_of} for fact in facts]
    inputs = {fact.key: {"value": fact.value_text, "origin": "project_fact",
                         "source_ref": {"project_id": project.id, "fact_key": fact.key,
                                        "revision": fact.revision}, "review_status": "project_fact"}
              for fact in facts if fact.key not in targets}
    rules = [{"id": rule.id, "name": rule.name, "target_key": rule.target_key,
              "expression": rule.expression, "deps": rule.deps,
              "version": hashlib.sha256(json.dumps([rule.expression, rule.deps], sort_keys=True).encode()).hexdigest()[:16],
              "source_ref": {"project_id": project.id, "rule_id": rule.id}}
             for rule in project_rules]
    return definitions, rules, inputs, None, None


def _canonical(value: str | None, data_type: str) -> str | None:
    if value is None or value.strip() == "":
        return None
    raw = value.strip()
    if data_type == "text":
        if len(raw) > 240:
            raise RuleError("文本输入过长")
        return raw
    if data_type == "boolean":
        if raw.lower() not in {"true", "false"}:
            raise RuleError("布尔输入必须为 true 或 false")
        return raw.lower()
    if data_type not in {"integer", "decimal"}:
        raise RuleError("方案输入类型不受支持")
    try:
        number = Decimal(raw)
    except InvalidOperation as exc:
        raise RuleError("请输入有效数值") from exc
    if not number.is_finite() or abs(number.adjusted()) > 1000:
        raise RuleError("数值超出可计算范围")
    if data_type == "integer" and number != number.to_integral_value():
        raise RuleError("此字段需要整数")
    if len(number.as_tuple().digits) > 50:
        raise RuleError("数值有效位数不能超过 50 位")
    return format(number.normalize(), "f")


def _changed_inputs(item: AnalysisScenario, changes: dict[str, str | None]) -> dict:
    definitions = {field["key"]: field for field in item.definitions if not field["computed"]}
    inputs = json.loads(json.dumps(item.inputs))
    for key, proposed in changes.items():
        if key not in definitions:
            raise HTTPException(400, f"输入 {key} 不存在或是计算结果")
        field = definitions[key]
        try:
            value = _canonical(proposed, field["data_type"])
        except RuleError as exc:
            raise HTTPException(400, f"{field['label']}：{exc}") from exc
        if key in {"N030", "N031"} and value is not None and not 0 <= Decimal(value) <= 1:
            raise HTTPException(400, f"{field['label']}应在 0% 至 100% 之间")
        if key in {"N025", "N026", "N027", "N028", "N029", "N017"} and value is not None and Decimal(value) < 0:
            raise HTTPException(400, f"{field['label']}不能为负数")
        before = inputs[key]
        if before["value"] == value:
            continue
        inputs[key] = {"value": value, "origin": "scenario_assumption" if value is not None else "missing",
                       "source_ref": None, "review_status": "unverified"}
        if key == "supplier" and "supplier_quote" in inputs:
            inputs["supplier_quote"] = {"value": None, "origin": "missing",
                                        "source_ref": None, "review_status": "unverified"}
    return inputs


def _calculate(item: AnalysisScenario, inputs: dict) -> dict:
    definitions = {field["key"]: field for field in item.definitions}
    rules = [SimpleNamespace(**rule) for rule in item.rules]
    try:
        ordered = sort_rules(rules, set(definitions))
    except RuleError as exc:
        raise HTTPException(400, str(exc)) from exc
    state = {key: {"key": key, "label": field["label"], "unit": field["unit"],
                   "source_ref": field.get("source_ref"),
                   "value": None if field["computed"] else inputs[key]["value"],
                   "status": "PENDING" if field["computed"] else
                             "UNDEFINED" if inputs[key]["value"] is None else "PROVIDED",
                   "origin": "computed" if field["computed"] else inputs[key]["origin"]}
             for key, field in definitions.items()}
    trace = []
    for rule in ordered:
        missing = [key for key in rule.deps if state[key]["value"] is None]
        target = state[rule.target_key]
        if missing:
            target["status"] = "UNEVALUABLE"
            trace.append({"rule_id": rule.id, "rule_version": rule.version,
                          "expression": rule.expression, "target": rule.target_key,
                          "inputs": {key: state[key]["value"] for key in rule.deps},
                          "status": "UNEVALUABLE", "missing": missing, "result": None,
                          "source_ref": rule.source_ref})
            continue
        try:
            values = {key: EvalValue(
                state[key]["value"] == "true" if definitions[key]["data_type"] == "boolean"
                else Decimal(state[key]["value"]),
                unit_dimension(state[key]["unit"]), state[key]["unit"])
                for key in rule.deps}
            result = evaluate(rule.expression, values)
            expected = target["unit"]
            if result.dimension != unit_dimension(expected) or unit_signature(result.unit_tag) != unit_signature(expected):
                raise RuleError(f"{target['label']}结果单位不匹配")
            value = _canonical(str(result.value), definitions[rule.target_key]["data_type"])
        except (RuleError, InvalidOperation) as exc:
            raise HTTPException(400, f"规则 {rule.id}：{exc}") from exc
        target["value"] = value
        target["status"] = "COMPUTED"
        trace.append({"rule_id": rule.id, "rule_version": rule.version,
                      "expression": rule.expression, "target": rule.target_key,
                      "inputs": {key: state[key]["value"] for key in rule.deps},
                      "status": "COMPUTED", "missing": [], "result": value,
                      "source_ref": rule.source_ref})
    demand_key = "N017" if "N017" in state else "first_year_demand"
    capacity_key = "N034" if "N034" in state else "qualified_capacity"
    demand, capacity = state.get(demand_key), state.get(capacity_key)
    comparison = "不可评估"
    if demand and capacity and demand["value"] is not None and capacity["value"] is not None:
        d, c = Decimal(demand["value"]), Decimal(capacity["value"])
        comparison = "需求低于产能" if d < c else "需求高于产能" if d > c else "需求等于产能"
        if unit_signature(demand["unit"]) != unit_signature(capacity["unit"]):
            raise HTTPException(400, "需求与产能单位不一致")
        state["demand_gap"] = {"key": "demand_gap", "label": "未满足需求量", "unit": demand["unit"],
                               "value": format(max(d - c, Decimal(0)), "f"), "status": "COMPUTED",
                               "origin": f"derived_from_{demand_key}_{capacity_key}", "source_ref": None}
    issues = []
    if item.corpus_id:
        issues.append({"code": "HISTORICAL_INPUT", "severity": "note",
                       "message": "历史资料和方案假设尚未作为本项目事实独立核实"})
        issues.append({"code": "X001", "severity": "note",
                       "message": "配电资料 800 kW 与 650 kW 存在未解决分歧；不作适配结论"})
    if "supplier_quote" in inputs and inputs["supplier_quote"]["value"] is None:
        issues.append({"code": "QUOTE_MISSING", "severity": "note", "message": "当前方案设备报价待补"})
    status = "UNEVALUABLE" if any(x["status"] == "UNEVALUABLE" for x in state.values()) else "COMPUTED"
    if not demand or not capacity:
        comparison = "部分结果不可评估" if status == "UNEVALUABLE" else "已完成计算"
    return {"scenario_id": item.id, "scenario_revision": item.revision,
            "blueprint_version": item.blueprint_version,
            "corpus_id": item.corpus_id, "corpus_version": item.corpus_version,
            "definitions": item.definitions, "rules": item.rules, "inputs": inputs,
            "results": state, "trace": trace, "condition": comparison,
            "issues": issues, "status": status}


def _scenario_dict(item: AnalysisScenario) -> dict:
    return {"id": item.id, "project_id": item.project_id, "name": item.name,
            "revision": item.revision, "blueprint_version": item.blueprint_version,
            "corpus_id": item.corpus_id, "corpus_version": item.corpus_version,
            "definitions": item.definitions, "rules": item.rules, "inputs": item.inputs,
            "updated_at": item.updated_at.isoformat()}


def _run_dict(item: AnalysisRun) -> dict:
    return {"id": item.id, "project_id": item.project_id, "scenario_id": item.scenario_id,
            "scenario_revision": item.scenario_revision, "status": item.status,
            "input_sha256": item.input_sha256, "snapshot": item.snapshot,
            "created_at": item.created_at.isoformat()}


def _report(session, project_id: str, report_id: str, *, lock: bool = False) -> ReportDraft:
    query = select(ReportDraft).where(ReportDraft.id == report_id, ReportDraft.project_id == project_id)
    row = session.scalar(query.with_for_update() if lock else query)
    if row is None:
        raise HTTPException(404, "报告不存在")
    return row


def _run_impacts(report: ReportDraft, run: AnalysisRun) -> list[dict]:
    impacts = []
    for index, block in enumerate(report.content, 1):
        refs = list(block.get("analysis_refs", []))
        if block.get("type") == "table":
            for row in block.get("children", []):
                for cell in row.get("children", []):
                    for paragraph in cell.get("children", []):
                        refs.extend(paragraph.get("analysis_refs", []))
        seen: set[tuple[str, str, str, str]] = set()
        for ref in refs:
            identity = (ref["run_id"], ref["result_key"], ref["value"], ref["unit"])
            if identity in seen:
                continue
            seen.add(identity)
            proposed = run.snapshot["results"].get(ref["result_key"])
            if (proposed is None or proposed["value"] != ref["value"]
                    or proposed["unit"] != ref["unit"] or run.id != ref["run_id"]):
                impacts.append({"position": index, "block_id": block.get("id"),
                                "result_key": ref["result_key"], "before": ref["value"],
                                "after": proposed["value"] if proposed else None,
                                "status": proposed["status"] if proposed else "UNAVAILABLE"})
    return impacts


@router.get("/scenarios")
def scenario_list(project_id: str):
    with SessionLocal() as session:
        _project(session, project_id)
        rows = session.scalars(select(AnalysisScenario).where(AnalysisScenario.project_id == project_id)
                               .order_by(AnalysisScenario.updated_at.desc())).all()
        return [_scenario_dict(row) for row in rows]


@router.post("/scenarios", status_code=201)
def scenario_create(project_id: str, body: ScenarioCreate):
    with SessionLocal.begin() as session:
        project = _project(session, project_id)
        if body.source == "copy":
            if not body.copy_from:
                raise HTTPException(400, "请选择要复制的方案")
            base = _scenario(session, project_id, body.copy_from)
            definitions, rules, inputs = base.definitions, base.rules, base.inputs
            corpus_id, corpus_version, blueprint = base.corpus_id, base.corpus_version, base.blueprint_version
        elif body.source == "config":
            if not body.config_id:
                raise HTTPException(400, "请选择已发布配置")
            config = _published_config(session, project_id, body.config_id)
            definitions, rules = deepcopy(config.definitions), deepcopy(config.rules)
            inputs = {field["key"]: {"value": None, "origin": "missing", "source_ref": None,
                                     "review_status": "unverified"}
                      for field in definitions if not field["computed"]}
            if body.copy_from:
                previous = _scenario(session, project_id, body.copy_from)
                previous_fields = {field["key"]: field for field in previous.definitions}
                for field in definitions:
                    key = field["key"]
                    old_field = previous_fields.get(key)
                    if (field["computed"] or old_field is None or old_field["computed"]
                            or old_field["data_type"] != field["data_type"]
                            or old_field["unit"] != field["unit"]):
                        continue
                    value = previous.inputs.get(key, {}).get("value")
                    if value is not None:
                        inputs[key] = {"value": value, "origin": "scenario_assumption",
                                       "source_ref": None, "review_status": "unverified"}
            corpus_id, corpus_version, blueprint = None, None, f"config:{config.id}"
        elif body.source == "historical":
            definitions, rules, inputs, corpus_id, corpus_version = _historical_blueprint(session, project)
            blueprint = CY_BLUEPRINT_VERSION
        else:
            definitions, rules, inputs, corpus_id, corpus_version = _project_blueprint(session, project)
            blueprint = "project-facts-v1"
        item = AnalysisScenario(project_id=project_id, name=body.name.strip(),
                                blueprint_version=blueprint, corpus_id=corpus_id,
                                corpus_version=corpus_version, definitions=definitions,
                                rules=rules, inputs=inputs)
        session.add(item)
        session.flush()
        return _scenario_dict(item)


@router.get("/scenarios/{scenario_id}")
def scenario_get(project_id: str, scenario_id: str):
    with SessionLocal() as session:
        return _scenario_dict(_scenario(session, project_id, scenario_id))


@router.put("/scenarios/{scenario_id}")
def scenario_update(project_id: str, scenario_id: str, body: ScenarioChange):
    with SessionLocal.begin() as session:
        item = _scenario(session, project_id, scenario_id, lock=True)
        if item.revision != body.base_revision:
            raise HTTPException(409, "方案已有新修订，请刷新后重试")
        proposed = _changed_inputs(item, body.changes)
        name = body.name.strip() if body.name else item.name
        if proposed != item.inputs or name != item.name:
            item.inputs = proposed
            item.name = name
            item.revision += 1
        session.flush()
        return _scenario_dict(item)


@router.post("/scenarios/{scenario_id}/preview")
def scenario_preview(project_id: str, scenario_id: str, body: ScenarioChange):
    with SessionLocal() as session:
        item = _scenario(session, project_id, scenario_id)
        if item.revision != body.base_revision:
            raise HTTPException(409, "方案已有新修订，请刷新后重试")
        proposed = _changed_inputs(item, body.changes)
        return {"preview_only": True, "run": _calculate(item, proposed)}


@router.post("/scenarios/{scenario_id}/runs", status_code=201)
def scenario_run(project_id: str, scenario_id: str, body: RunCreate):
    with SessionLocal.begin() as session:
        item = _scenario(session, project_id, scenario_id, lock=True)
        existing = session.scalar(select(AnalysisRun).where(AnalysisRun.scenario_id == scenario_id,
                         AnalysisRun.request_key == body.request_key))
        if existing is not None:
            if existing.scenario_revision != body.scenario_revision:
                raise HTTPException(409, "该请求标识已经用于其他方案修订")
            return _run_dict(existing)
        if item.revision != body.scenario_revision:
            raise HTTPException(409, "方案已变化，请重新推演")
        snapshot = _calculate(item, item.inputs)
        if item.blueprint_version.startswith("config:"):
            config = _published_config(session, project_id, item.blueprint_version[7:])
            snapshot["configuration"] = {"id": config.id, "version": config.version,
                                          "checksum": config.checksum,
                                          "sections": deepcopy(config.sections)}
        raw = json.dumps(snapshot, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        run = AnalysisRun(project_id=project_id, scenario_id=scenario_id,
                          scenario_revision=item.revision, request_key=body.request_key,
                          input_sha256=hashlib.sha256(raw.encode()).hexdigest(),
                          snapshot=snapshot, status=snapshot["status"])
        session.add(run)
        session.flush()
        return _run_dict(run)


@router.get("/scenarios/{scenario_id}/runs")
def scenario_runs(project_id: str, scenario_id: str):
    with SessionLocal() as session:
        _scenario(session, project_id, scenario_id)
        rows = session.scalars(select(AnalysisRun).where(AnalysisRun.project_id == project_id,
                AnalysisRun.scenario_id == scenario_id).order_by(AnalysisRun.created_at.desc())).all()
        return [_run_dict(row) for row in rows]


@router.get("/runs/{run_id}")
def run_get(project_id: str, run_id: str):
    with SessionLocal() as session:
        return _run_dict(_run(session, project_id, run_id))


@router.post("/runs/compare")
def run_compare(project_id: str, run_ids: list[str] = Body(..., min_length=2, max_length=3)):
    if not 2 <= len(run_ids) <= 3 or len(set(run_ids)) != len(run_ids):
        raise HTTPException(400, "请选择两到三份不同的推演结果")
    with SessionLocal() as session:
        rows = [_run(session, project_id, run_id) for run_id in run_ids]
        keys = sorted(set().union(*(row.snapshot["results"].keys() for row in rows)))
        return {"runs": [_run_dict(row) for row in rows], "rows": [{"key": key,
                 "values": [row.snapshot["results"].get(key) for row in rows]} for key in keys]}


@router.get("/reports/{report_id}/impact/{run_id}")
def report_run_impact(project_id: str, report_id: str, run_id: str):
    with SessionLocal() as session:
        report, run = _report(session, project_id, report_id), _run(session, project_id, run_id)
        return {"report_version": report.version, "current_run_id": report.analysis_run_id,
                "proposed_run_id": run_id, "impacts": _run_impacts(report, run)}


@router.post("/reports/{report_id}/select")
def report_run_select(project_id: str, report_id: str, body: RunSelection):
    with SessionLocal.begin() as session:
        report = _report(session, project_id, report_id, lock=True)
        run = _run(session, project_id, body.run_id)
        if report.version != body.base_version:
            raise HTTPException(409, "报告已有新版本，请刷新后选择")
        if run.status != "COMPUTED":
            raise HTTPException(409, "该推演仍有不可评估结果，请补充输入")
        impacts = _run_impacts(report, run)
        if report.analysis_run_id != run.id:
            report.analysis_run_id = run.id
            report.reviewed_hash = None
            report.version += 1
            session.add(ReportVersion(report_id=report.id, version=report.version,
                                      content=report.content, bound_facts=report.bound_facts,
                                      analysis_run_id=run.id))
            session.add(AnalysisWritingEvent(project_id=project_id, report_id=report_id,
                      report_version=report.version, run_id=run.id, action="select_run",
                      payload={"impacts": impacts}))
        return {"report_id": report_id, "report_version": report.version,
                "analysis_run_id": report.analysis_run_id, "impacts": impacts}
