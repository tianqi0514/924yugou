"""Chapter-level controls and read-only ablation of the frozen writing corpus.

The saved setting is a project preference, not a copy of historical facts.  The
simulation uses the same deterministic candidate builder as the writing flow and
never flushes a proposed fact, rule, report, or candidate.
"""

from __future__ import annotations

from collections import Counter
from copy import deepcopy
from datetime import datetime
from hashlib import sha256
import json
from typing import Any, Literal
from uuid import uuid4

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import DateTime, ForeignKey, Integer, JSON, String, UniqueConstraint, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Mapped, Session, mapped_column

from .corpus import CATEGORY_NAMES, GROUPS
from .corpus_storage import CorpusArtifact, CorpusCategoryRow, CorpusRecord
from .db import Base, Project, ProjectFact, ReportDraft, RuleRecord, SessionLocal, WritingCommitEvent, utcnow
from .writing import SLOTS
from .writing_support import GUIDED_SECTIONS, accepted_reference, build_candidate, package


router = APIRouter(prefix="/api/projects/{project_id}/writing/materials", tags=["writing-materials"])
MODES = ("auto", "review", "exclude")
NOT_GENERATED = {11, 30}
DIRECT_INPUT = {4, 10, 13, 15, 27}
REVIEW_INPUT = {9, 12, 18, 19, 23, 24, 26}


class WritingMaterialSetting(Base):
    """One atomic configuration revision per project and chapter."""

    __tablename__ = "writing_material_settings"

    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), primary_key=True)
    section_id: Mapped[str] = mapped_column(String(80), primary_key=True)
    revision: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    settings: Mapped[dict[str, str]] = mapped_column(JSON, nullable=False, default=dict)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow,
                                                  onupdate=utcnow, nullable=False)


class MaterialExperimentRun(Base):
    """Immutable observation of one preregistered, deterministic 30-category QA case."""

    __tablename__ = "material_experiment_runs"
    __table_args__ = (UniqueConstraint("project_id", "section_id", "input_sha256",
                                       name="uq_material_experiment_input"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), nullable=False, index=True)
    section_id: Mapped[str] = mapped_column(String(80), nullable=False)
    case_id: Mapped[str] = mapped_column(String(40), nullable=False)
    corpus_id: Mapped[str] = mapped_column(String(160), nullable=False)
    corpus_version: Mapped[str] = mapped_column(String(60), nullable=False)
    config_version: Mapped[int] = mapped_column(Integer, nullable=False)
    input_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    result: Mapped[dict] = mapped_column(JSON, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)


class MaterialChoice(BaseModel):
    category_id: str = Field(pattern=r"^CAT-(0[1-9]|[12][0-9]|30)$")
    mode: Literal["auto", "review", "exclude"]


class MaterialSave(BaseModel):
    base_version: int = Field(ge=0)
    settings: list[MaterialChoice] = Field(default_factory=list, max_length=30)


class MaterialSimulate(BaseModel):
    settings: list[MaterialChoice] | None = Field(default=None, max_length=30)
    selected_categories: list[str] = Field(default_factory=list, max_length=6)
    input_mode: Literal["project", "example"] = "project"
    slot_values: dict[str, Any] = Field(default_factory=dict)
    experiment_mode: Literal["remove", "equivalent"] = "remove"


class MaterialEvaluate(BaseModel):
    case_id: Literal["S4-base", "S4-zero", "S4-missing", "S4-lower", "S4-equal",
                     "S7.1-supplier", "S5.2-conflict"]


_FIXED_CASES: dict[str, tuple[str, dict[str, Any]]] = {
    "S4-base": ("S4", {"N017": 300000, "N034": 254016}),
    "S4-zero": ("S4", {"N017": 0, "N034": 254016}),
    "S4-missing": ("S4", {"N017": None, "N034": 254016}),
    "S4-lower": ("S4", {"N017": 200000, "N034": 254016}),
    "S4-equal": ("S4", {"N017": 254016, "N034": 254016}),
    "S7.1-supplier": ("S7.1", {"supplier_name": "新供应商甲", "N080": "1200.00"}),
    "S5.2-conflict": ("S5.2", {}),
}


def _project(session: Session, project_id: str, *, lock: bool = False) -> Project:
    statement = select(Project).where(Project.id == project_id)
    if lock:
        statement = statement.with_for_update()
    project = session.scalar(statement)
    if project is None:
        raise HTTPException(404, "项目不存在")
    return project


def _raw_package(session: Session, project_id: str, section_id: str) -> dict:
    try:
        return package(session, project_id, section_id, apply_settings=False)
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from exc


def _setting(session: Session, project_id: str, section_id: str) -> WritingMaterialSetting | None:
    return session.get(WritingMaterialSetting, (project_id, section_id))


def _mode_map(choices: list[MaterialChoice] | None, saved: dict[str, str] | None = None) -> dict[str, str]:
    result = dict(saved or {})
    seen: set[str] = set()
    for choice in choices or []:
        if choice.category_id in seen:
            raise HTTPException(422, f"类别 {choice.category_id} 重复配置")
        seen.add(choice.category_id)
        result[choice.category_id] = choice.mode
    return {key: value for key, value in result.items() if value != "auto"}


def _configure_pack(raw: dict, settings: dict[str, str]) -> dict:
    configured = deepcopy(raw)
    items = []
    affected_required = []
    for item in configured["items"]:
        mode = settings.get(item["category_id"], "auto")
        item["configured_mode"] = mode
        if mode == "exclude":
            if item.get("required"):
                affected_required.append(item["item_id"])
            continue
        if mode == "review" and item["decision"] == "selectable":
            item["decision"] = "review_only"
            item["reason"] = "本章配置为仅核对，不进入写作输入"
            item["review_status"] = "configuration_review_only"
            if item.get("required"):
                affected_required.append(item["item_id"])
        items.append(item)
    configured["items"] = items
    if affected_required:
        configured["issues"].append({
            "code": "REQUIRED_MATERIAL_DISABLED", "severity": "block",
            "message": "本章写作实现依赖的结构或规则已设为仅核对/不使用，当前不能生成候选",
            "item_ids": affected_required,
        })
    return configured


def apply_package_configuration(session: Session, project_id: str, section_id: str, pack: dict) -> dict:
    """Apply the persisted preference at the package boundary; preserve required IDs."""
    row = _setting(session, project_id, section_id)
    configured = _configure_pack(pack, dict(row.settings or {}) if row else {})
    configured["material_config_version"] = row.revision if row else 0
    return configured


def _chapter_role(number: int, chapter_items: list[dict], section_id: str) -> str:
    if number == 11:
        return "向量零行，本章没有向量输入"
    if number == 30:
        return "结构特征可核对，向量与匹配权重未生成"
    if not chapter_items:
        return "本章没有已验证的记录消费路径"
    semantic_ids = [str(item.get("semantic_id") or "") for item in chapter_items]
    if number == 4:
        return "定位本章层级和标题"
    if number == 9:
        return "回看历史原文位置，不复制原句"
    if number == 10:
        return "提供本章段落槽位与格式"
    if number == 12:
        return "核对历史节点与本项目事实映射"
    if number == 13:
        return "核对受限计算规则" if section_id == "S4" and "R006" in semantic_ids else "核对相邻规则"
    if number == 15:
        return "提供“需求不是订单”的受限提醒" if section_id == "S4" and "L002" in semantic_ids else "核对历史论断"
    if number == 18:
        return "核对历史证据及完整原件状态，不能证明新项目"
    if number == 19:
        return "回看历史证据摘录与来源位置"
    if number == 23:
        return "披露历史未解决冲突，不能替代新项目核证" if section_id == "S5.2" else "核对历史冲突是否影响本章"
    if number == 24:
        return "核对历史资料缺口"
    if number == 26:
        return "模型起草可选的语气与数字格式；规则起草未消费"
    if number == 27:
        return "提供本章顺序、表格和标题结构"
    return "核对本章关联记录与使用边界"


def _effective_role(number: int, chapter_items: list[dict], mode: str) -> str:
    if number in NOT_GENERATED:
        return "not_generated"
    if mode == "exclude" or not chapter_items:
        return "unused"
    if mode == "review":
        return "review"
    if number in DIRECT_INPUT and any(item["decision"] == "selectable" for item in chapter_items):
        return "input"
    return "review" if number in REVIEW_INPUT or chapter_items else "unused"


def _catalog(session: Session, pack: dict, settings: dict[str, str]) -> tuple[list[dict], list[dict]]:
    corpus_id = pack["corpus_id"]
    rows = session.scalars(select(CorpusCategoryRow).where(CorpusCategoryRow.corpus_id == corpus_id)
                           .order_by(CorpusCategoryRow.number)).all()
    if len(rows) != 30:
        raise HTTPException(503, "语料类别未完整入库")
    artifacts = Counter(session.scalars(select(CorpusArtifact.category_number).where(
        CorpusArtifact.corpus_id == corpus_id, CorpusArtifact.category_number.is_not(None))).all())
    by_category: dict[int, list[dict]] = {}
    for item in pack["items"]:
        by_category.setdefault(item["category_number"], []).append(item)
    output = []
    for row in rows:
        number = row.number
        category_id = f"CAT-{number:02d}"
        payload = row.payload or {}
        chapter_items = by_category.get(number, [])
        mode = settings.get(category_id, "auto")
        output.append({
            "category_id": category_id, "number": number,
            "name": str(payload.get("name") or CATEGORY_NAMES[number - 1]),
            "group": str(payload.get("group") or next(g["name"] for g in GROUPS if number in g["numbers"])),
            "record_count": row.record_count, "artifact_count": artifacts[number],
            "configured_mode": mode, "effective_role": _effective_role(number, chapter_items, mode),
            "purpose": str(payload.get("description") or payload.get("actual_content") or ""),
            "chapter_role": _chapter_role(number, chapter_items, pack["section"]["id"]),
            "limitations": str(payload.get("boundary") or ""),
            "record_ids": [item["record_id"] for item in chapter_items],
        })
    groups = [{"name": group["name"], "numbers": group["numbers"], "count": len(group["numbers"])}
              for group in GROUPS]
    return groups, output


def _display_slots(pack: dict, section_id: str) -> list[dict]:
    if section_id not in {"S4", "S7.1"}:
        return []
    eligible = {slot["id"] for slot in SLOTS if slot["chunk_id"] == GUIDED_SECTIONS[section_id]}
    return [{"slot_id": slot["slot_id"], "label": slot["label"], "unit": slot["unit"],
             "value": slot.get("value"), "status": slot.get("status"), "fact_key": slot.get("fact_key")}
            for slot in pack["slots"] if slot["slot_id"] in eligible]


@router.get("")
def get_materials(project_id: str, section_id: str = Query(..., min_length=1, max_length=80)) -> dict:
    with SessionLocal() as session:
        _project(session, project_id)
        pack = _raw_package(session, project_id, section_id)
        row = _setting(session, project_id, section_id)
        groups, items = _catalog(session, pack, dict(row.settings or {}) if row else {})
        return {"project_id": project_id, "section_id": section_id,
                "config_version": row.revision if row else 0,
                "groups": groups, "items": items, "slots": _display_slots(pack, section_id)}


@router.put("/{section_id}")
def save_materials(project_id: str, section_id: str, body: MaterialSave) -> dict:
    with SessionLocal.begin() as session:
        project = _project(session, project_id, lock=True)
        pack = _raw_package(session, project_id, section_id)
        row = _setting(session, project_id, section_id)
        current_revision = row.revision if row else 0
        if body.base_version != current_revision:
            raise HTTPException(409, "配置版本已变化，请刷新后重试")
        modes = _mode_map(body.settings, dict(row.settings or {}) if row else {})
        if row is None:
            row = WritingMaterialSetting(project_id=project_id, section_id=section_id,
                                         revision=1, settings=modes)
            session.add(row)
        else:
            row.settings = modes
            row.revision += 1
        project.version += 1
        project.updated_at = utcnow()
        groups, items = _catalog(session, pack, modes)
        return {"project_id": project_id, "section_id": section_id,
                "config_version": row.revision, "project_version": project.version,
                "groups": groups, "items": items, "slots": _display_slots(pack, section_id)}


def _clone_fact(fact: ProjectFact) -> ProjectFact:
    return ProjectFact(id=fact.id, project_id=fact.project_id, key=fact.key, label=fact.label,
                       data_type=fact.data_type, value_text=fact.value_text,
                       value_status=fact.value_status, unit=fact.unit, caliber=fact.caliber,
                       as_of=fact.as_of, source=fact.source, revision=fact.revision,
                       created_at=fact.created_at, updated_at=fact.updated_at)


def _simulation_inputs(session: Session, project_id: str, mode: str,
                       slot_values: dict[str, Any]) -> tuple[dict[str, dict], list[RuleRecord], dict[str, str], list[dict]]:
    from .main import _writing_rows, canonical_value, simulate
    from .rules import RuleError

    slot_specs = {slot["id"]: slot for slot in SLOTS}
    unknown = set(slot_values) - set(slot_specs)
    if unknown:
        raise HTTPException(422, "未知写作槽位：" + "、".join(sorted(unknown)))
    if "N035" in slot_values:
        raise HTTPException(422, "计划销售量由 min 规则计算，不能直接填写")
    try:
        if mode == "example":
            defaults: dict[str, Any] = {"N017": 300000, "N034": 254016,
                                        "supplier_name": "示例供应商A", "N080": "1200.00"}
            defaults.update(slot_values)
            bindings = {slot["id"]: slot["suggested_key"] for slot in SLOTS}
            facts = []
            for slot in SLOTS:
                value = None if slot["id"] == "N035" else canonical_value(defaults.get(slot["id"]), slot["data_type"])
                facts.append(ProjectFact(
                    id=f"simulation:{slot['id']}", project_id=project_id, key=slot["suggested_key"],
                    label=slot["label"], data_type=slot["data_type"], unit=slot["unit"],
                    value_text=value, value_status="UNDEFINED" if value is None else "PROVIDED",
                    caliber="", as_of="", source="模拟输入（未入库）", revision=0,
                ))
            rules = [RuleRecord(id="simulation:R006", project_id=project_id,
                                name="示例首年计划销售量", target_key="planned_sales",
                                expression="min(first_year_demand, qualified_capacity)",
                                deps=["first_year_demand", "qualified_capacity"])]
        else:
            stored_facts, rules, bindings = _writing_rows(session, project_id)
            facts = [_clone_fact(fact) for fact in stored_facts]
            by_key = {fact.key: fact for fact in facts}
            for slot_id, proposed in slot_values.items():
                fact = by_key.get(bindings.get(slot_id, ""))
                if fact is None:
                    raise HTTPException(409, f"槽位 {slot_id} 尚未映射项目事实")
                value = canonical_value(proposed, fact.data_type)
                fact.value_text = value
                fact.value_status = "UNDEFINED" if value is None else "PROVIDED"
                fact.source = "模拟输入（未入库）"
        state, trace = simulate(facts, rules)
        return state, rules, bindings, trace
    except RuleError as exc:
        raise HTTPException(422, str(exc)) from exc


def _plain(children: list[dict]) -> str:
    parts = []
    for child in children:
        if child.get("type") == "fact_ref":
            parts.append(str(child.get("display") or ""))
        elif "text" in child:
            parts.append(str(child["text"]))
        else:
            parts.append(_plain(child.get("children") or []))
    return "".join(parts)


def _candidate_snapshot(session: Session, raw: dict, settings: dict[str, str],
                        state: dict[str, dict], rules: list[RuleRecord], bindings: dict[str, str]) -> dict:
    pack = _configure_pack(raw, settings)
    approved = [item["item_id"] for item in pack["items"] if item["decision"] == "selectable"]
    try:
        result = build_candidate(session, pack, state, rules, bindings, approved, "guided")
    except (ValueError, KeyError) as exc:
        return {"status": "blocked", "text": "", "used_categories": [], "used_records": [],
                "issues": [{"severity": "block", "message": str(exc)}]}
    by_id = {item["item_id"]: item for item in raw["items"]}
    used = [item for item in result["used_items"] if item in by_id]
    return {"status": "ready",
            "text": "\n".join(_plain(block.get("children") or []) for block in result["paragraphs"]),
            "used_categories": sorted({by_id[item]["category_id"] for item in used}),
            "used_records": sorted(used), "issues": result["issues"]}


def _facts_for_display(raw: dict, state: dict[str, dict], bindings: dict[str, str]) -> list[dict]:
    result = []
    for slot in _display_slots(raw, raw["section"]["id"]):
        key = bindings.get(slot["slot_id"])
        fact = state.get(key or "")
        result.append({"slot_id": slot["slot_id"], "label": slot["label"], "unit": slot["unit"],
                       "fact_key": key, "value": fact["value"] if fact else None,
                       "status": fact["status"] if fact else "UNBOUND"})
    return result


@router.post("/{section_id}/simulate")
def simulate_materials(project_id: str, section_id: str, body: MaterialSimulate) -> dict:
    selected = body.selected_categories
    if len(selected) != len(set(selected)) or any(
        not (len(item) == 6 and item.startswith("CAT-") and item[4:].isdigit() and 1 <= int(item[4:]) <= 30)
        for item in selected
    ):
        raise HTTPException(422, "请从 30 类中选择不重复的类别")
    with SessionLocal() as session:
        _project(session, project_id)
        raw = _raw_package(session, project_id, section_id)
        row = _setting(session, project_id, section_id)
        settings = _mode_map(body.settings, dict(row.settings or {}) if row else {})
        if section_id not in GUIDED_SECTIONS:
            unadapted = {"status": "unadapted", "text": "", "used_categories": [],
                         "used_records": [], "issues": [{"severity": "block", "message": "本章尚未适配写作模拟"}]}
            return {"baseline": unadapted, "configured": unadapted,
                    "experiments": [{"category_id": category,
                                     "status": "not_supported" if body.experiment_mode == "equivalent" else "not_consumed",
                                     "reason": "本章尚未适配写作模拟，不能判断此类的写作贡献",
                                     "before_text": "", "after_text": "", "used_records": []}
                                    for category in selected],
                    "trace": [], "facts": [], "simulation_only": True,
                    "experiment_mode": body.experiment_mode}
        state, rules, bindings, trace = _simulation_inputs(session, project_id, body.input_mode, body.slot_values)
        baseline = _candidate_snapshot(session, raw, {}, state, rules, bindings)
        configured = _candidate_snapshot(session, raw, settings, state, rules, bindings)
        experiments = []
        for category in selected:
            if body.experiment_mode == "equivalent":
                from .writing_contract import compare_equivalent

                number = int(category[4:])
                record_ids = [record_id for record_id in configured["used_records"]
                              if record_id.startswith(f"{number:02d}:")]
                payloads = []
                for record_id in record_ids:
                    record = session.get(CorpusRecord, (raw["corpus_id"], number, record_id))
                    if record is not None:
                        payloads.append(record.payload)
                experiments.append(compare_equivalent(section_id, number, configured,
                                                      state, rules, bindings, record_ids, payloads))
                continue
            without = dict(settings)
            without[category] = "exclude"
            after = _candidate_snapshot(session, raw, without, state, rules, bindings)
            consumed = category in configured["used_categories"]
            if configured["status"] != "ready":
                status, reason = "not_consumed", "当前输入尚未形成候选，无法判断此类对成稿的贡献"
            elif not consumed:
                status, reason = "not_consumed", "当前章节写作路径未消费此类记录"
            elif after["status"] == "blocked":
                status, reason = "blocked", "移除后当前实现无法成稿；这是实现依赖，不能单独证明语义内容必要"
            elif after["text"] != configured["text"]:
                status, reason = "changed", "移除后候选正文发生变化"
            else:
                status, reason = "same", ("正文未变，但来源或核对链发生变化" if
                                          after["used_records"] != configured["used_records"] else
                                          "移除后正文及已用记录均未变化")
            experiments.append({"category_id": category, "status": status, "reason": reason,
                                "before_text": configured["text"], "after_text": after["text"],
                                "used_records": [item for item in configured["used_records"]
                                                 if item.startswith(f"{int(category[4:]):02d}:")]})
        return {"baseline": baseline, "configured": configured,
                "experiments": experiments, "trace": trace,
                "facts": _facts_for_display(raw, state, bindings), "simulation_only": True,
                "experiment_mode": body.experiment_mode}


def _digest(value: dict) -> str:
    return sha256(json.dumps(value, sort_keys=True, ensure_ascii=False, default=str,
                             separators=(",", ":")).encode()).hexdigest()


def _issue_codes(snapshot: dict) -> list[str]:
    return sorted({str(issue.get("code") or issue.get("message") or "")
                   for issue in snapshot.get("issues", [])})


def _experiment_result(session: Session, raw: dict, settings: dict[str, str],
                       state: dict[str, dict], rules: list[RuleRecord], bindings: dict[str, str]) -> dict:
    """Observe all categories without treating an empty ablation as proof of no value."""
    from .writing_contract import SUPPORTED, compare_equivalent

    section_id = raw["section"]["id"]
    baseline = _candidate_snapshot(session, raw, {}, state, rules, bindings)
    configured = _candidate_snapshot(session, raw, settings, state, rules, bindings)
    by_category: dict[int, list[dict]] = {}
    for item in raw["items"]:
        by_category.setdefault(item["category_number"], []).append(item)
    categories = []
    for number in range(1, 31):
        category_id = f"CAT-{number:02d}"
        items = by_category.get(number, [])
        record_ids = {item["record_id"] for item in items}
        used_ids = record_ids.intersection(configured["used_records"])
        record_refs = [{"record_id": item["record_id"], "artifact_id": item["artifact_id"],
                        "location": item.get("location"), "decision": item["decision"]}
                       for item in items]
        if configured["status"] != "ready":
            removal = {"status": "not_tested", "reason": "当前输入未形成候选，无法检验移除影响"}
        elif not items:
            removal = {"status": "not_in_package", "reason": "本章没有该类记录，未执行空遮蔽"}
        else:
            without = dict(settings)
            without[category_id] = "exclude"
            after = _candidate_snapshot(session, raw, without, state, rules, bindings)
            removal = {
                "status": ("implementation_coupling" if after["status"] == "blocked" and used_ids
                           else "blocked_without_consumption" if after["status"] == "blocked"
                           else "changed" if after["text"] != configured["text"]
                           else "source_or_issue_changed" if (
                               after["used_records"] != configured["used_records"]
                               or _issue_codes(after) != _issue_codes(configured)) else "same"),
                "reason": ("构造器依赖，不能证明信息内容或独立文件必需" if after["status"] == "blocked"
                           else "仅观察本章确定性候选；其他用户任务未检验"),
                "text_changed": after["text"] != configured["text"],
                "source_refs_changed": after["used_records"] != configured["used_records"],
                "issue_codes_changed": _issue_codes(after) != _issue_codes(configured),
                "after_status": after["status"],
            }
        equivalent = {"status": "not_tested", "reason": "本章未消费该类记录或尚无等价适配"}
        if configured["status"] == "ready" and used_ids and number in SUPPORTED.get(section_id, set()):
            payloads = []
            for record_id in sorted(used_ids):
                record = session.get(CorpusRecord, (raw["corpus_id"], number, record_id))
                if record is not None:
                    payloads.append(record.payload)
            comparison = compare_equivalent(section_id, number, configured, state, rules,
                                            bindings, sorted(used_ids), payloads)
            equivalent = {"status": comparison["status"], "reason": comparison["reason"],
                          "checks": comparison.get("checks"),
                          "information_origin": (comparison.get("contract") or {}).get("information_origin")}
        categories.append({
            "category_id": category_id, "name": CATEGORY_NAMES[number - 1],
            "database_record_count": session.get(CorpusCategoryRow, (raw["corpus_id"], number)).record_count,
            "package_records": record_refs, "candidate_record_ids": sorted(used_ids),
            "stages": {"database_present": True, "chapter_package": bool(items),
                       "builder_passed": bool(items) and settings.get(category_id) != "exclude",
                       "candidate_referenced": bool(used_ids),
                       "report_adopted": None, "human_reviewed": None,
                       "export_audited": None},
            "removal": removal, "equivalent": equivalent,
            "limitation": "此记录仅为确定性模拟；用户入稿、人工核对、模型效果与导出须另查实际事件",
        })
    return {"schema_version": "material-contribution/v2", "section_id": section_id,
            "baseline": baseline, "configured": configured, "categories": categories,
            "category_count": len(categories), "method": "deterministic_guided_synthetic_QA",
            "simulation_only": True}


def _experiment_dict(row: MaterialExperimentRun) -> dict:
    return {"id": row.id, "project_id": row.project_id, "section_id": row.section_id,
            "case_id": row.case_id, "corpus_id": row.corpus_id,
            "corpus_version": row.corpus_version, "config_version": row.config_version,
            "input_sha256": row.input_sha256, "created_at": row.created_at.isoformat(),
            "result": row.result}


@router.post("/{section_id}/evaluate")
def evaluate_materials(project_id: str, section_id: str, body: MaterialEvaluate) -> dict:
    case = _FIXED_CASES[body.case_id]
    if case[0] != section_id:
        raise HTTPException(422, "案例与章节不一致")
    with SessionLocal.begin() as session:
        _project(session, project_id)
        raw = _raw_package(session, project_id, section_id)
        row = _setting(session, project_id, section_id)
        settings = dict(row.settings or {}) if row else {}
        state, rules, bindings, trace = _simulation_inputs(session, project_id, "example", case[1])
        input_sha256 = _digest({"method": "material-contribution/v2", "case_id": body.case_id,
                                "project_id": project_id, "corpus_id": raw["corpus_id"],
                                "corpus_version": raw["corpus_version"], "config_version": row.revision if row else 0,
                                "settings": settings, "slot_values": case[1],
                                "rules": [{"id": rule.id, "revision": rule.revision,
                                           "expression": rule.expression, "deps": rule.deps} for rule in rules]})
        existing = session.scalar(select(MaterialExperimentRun).where(
            MaterialExperimentRun.project_id == project_id,
            MaterialExperimentRun.section_id == section_id,
            MaterialExperimentRun.input_sha256 == input_sha256))
        if existing is not None:
            return _experiment_dict(existing)
        result = _experiment_result(session, raw, settings, state, rules, bindings)
        result["case_inputs"] = _facts_for_display(raw, state, bindings)
        result["trace"] = trace
        stmt = pg_insert(MaterialExperimentRun).values(
            id=str(uuid4()), project_id=project_id, section_id=section_id,
            case_id=body.case_id, corpus_id=raw["corpus_id"], corpus_version=raw["corpus_version"],
            config_version=row.revision if row else 0, input_sha256=input_sha256,
            result=json.loads(json.dumps(result, ensure_ascii=False, default=str)), created_at=utcnow(),
        ).on_conflict_do_nothing(constraint="uq_material_experiment_input")
        session.execute(stmt)
        saved = session.scalar(select(MaterialExperimentRun).where(
            MaterialExperimentRun.project_id == project_id,
            MaterialExperimentRun.section_id == section_id,
            MaterialExperimentRun.input_sha256 == input_sha256))
        return _experiment_dict(saved)


@router.get("/{section_id}/experiments")
def list_material_experiments(project_id: str, section_id: str) -> dict:
    with SessionLocal() as session:
        _project(session, project_id)
        rows = session.scalars(select(MaterialExperimentRun).where(
            MaterialExperimentRun.project_id == project_id,
            MaterialExperimentRun.section_id == section_id).order_by(
                MaterialExperimentRun.created_at.desc()).limit(1)).all()
        return {"runs": [_experiment_dict(row) for row in rows]}


@router.get("/{section_id}/categories/{category_id}/usage")
def material_usage(project_id: str, section_id: str, category_id: str,
                   report_id: str = Query(..., min_length=1)) -> dict:
    if not (len(category_id) == 6 and category_id.startswith("CAT-") and category_id[4:].isdigit()
            and 1 <= int(category_id[4:]) <= 30):
        raise HTTPException(422, "类别编号无效")
    with SessionLocal() as session:
        _project(session, project_id)
        report = session.get(ReportDraft, report_id)
        if report is None or report.project_id != project_id:
            raise HTTPException(404, "报告不存在")
        raw = _raw_package(session, project_id, section_id)
        package_items = [item for item in raw["items"] if item["category_id"] == category_id]
        events = session.scalars(select(WritingCommitEvent).where(
            WritingCommitEvent.project_id == project_id,
            WritingCommitEvent.report_id == report_id,
            WritingCommitEvent.section_id == section_id).order_by(
                WritingCommitEvent.report_version.desc())).all()
        by_block: dict[str, WritingCommitEvent] = {}
        for event in events:
            for block_id in event.block_ids:
                by_block.setdefault(block_id, event)
        uses = []
        current_block_ids = {block.get("id") for block in report.content if block.get("section_id") == section_id}
        model_sent = []
        for event in events:
            if event.mode != "model" or not current_block_ids.intersection(event.block_ids):
                continue
            audit = event.model_audit or {}
            if not audit.get("model_call"):
                continue
            for material in (audit.get("model_input") or {}).get("materials", []):
                source = material.get("source_ref") or {}
                if (source.get("category_id") == category_id and source.get("corpus_id") == raw["corpus_id"]
                        and source.get("corpus_version") == raw["corpus_version"]):
                    model_sent.append({"record_id": source.get("record_id"),
                                       "artifact_id": source.get("artifact_id"),
                                       "commit_event_id": event.id,
                                       "committed_report_version": event.report_version})
        for block in report.content:
            if block.get("section_id") != section_id:
                continue
            for ref in block.get("source_refs", []):
                if (ref.get("category_id") != category_id or ref.get("corpus_id") != raw["corpus_id"]
                        or ref.get("corpus_version") != raw["corpus_version"]):
                    continue
                record = session.get(CorpusRecord, (raw["corpus_id"], int(category_id[4:]),
                                                    ref.get("record_id", "")))
                event = by_block.get(block.get("id"))
                if event and not any(
                    binding.get("block_id") == block.get("id") and ref in binding.get("refs", [])
                    for binding in event.source_refs
                ):
                    event = None
                uses.append({"report_id": report.id, "report_version": report.version,
                             "section_id": section_id, "block_id": block.get("id"),
                             "record_id": ref.get("record_id"), "artifact_id": ref.get("artifact_id"),
                             "corpus_id": raw["corpus_id"], "corpus_version": raw["corpus_version"],
                             "location": record.location if record else None,
                             "use": ref.get("use"), "commit_event_id": event.id if event else None,
                             "committed_report_version": event.report_version if event else None})
        return {"project_id": project_id, "report_id": report_id, "report_version": report.version,
                "section_id": section_id, "category_id": category_id, "current_uses": uses,
                "model_sent": model_sent,
                "package_records": [{"record_id": item["record_id"],
                                     "artifact_id": item["artifact_id"],
                                     "decision": item["decision"], "reason": item["reason"]}
                                    for item in package_items],
                "status": "in_current_report" if uses else "package_only" if package_items else "not_in_package"}
