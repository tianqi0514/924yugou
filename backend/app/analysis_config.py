"""Versioned project contracts for inputs, safe rules, and chapter material mapping."""

from __future__ import annotations

import ast
import hashlib
import hmac
import json
import re
import secrets
import time
from copy import deepcopy
from types import SimpleNamespace

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import func, select

from .analysis import _calculate, _canonical, _project, _scenario
from .analysis_conditions import condition_results
from .db import AnalysisConfig, SessionLocal, utcnow
from .rules import RuleError, parse_expression, sort_rules


router = APIRouter(prefix="/api/projects/{project_id}/analysis/configs", tags=["推演写作配置"])
_secret = secrets.token_bytes(32)
_key = re.compile(r"^[A-Za-z][A-Za-z0-9_]*$")
_rule_id = re.compile(r"^[A-Za-z][A-Za-z0-9_-]*$")
_section_id = re.compile(r"^[A-Za-z][A-Za-z0-9_.-]*$")


class ConfigCreate(BaseModel):
    name: str = Field(min_length=1, max_length=160)
    from_config_id: str | None = None
    from_scenario_id: str | None = None


class ConfigUpdate(BaseModel):
    revision: int = Field(ge=1)
    name: str = Field(min_length=1, max_length=160)
    definitions: list[dict] = Field(max_length=80)
    rules: list[dict] = Field(max_length=80)
    sections: list[dict] = Field(max_length=80)


class ConfigTest(BaseModel):
    revision: int = Field(ge=1)
    sample_inputs: dict[str, str | None] = Field(max_length=80)


class ConfigPublish(BaseModel):
    revision: int = Field(ge=1)
    test_token: str


def _config(session, project_id: str, config_id: str, *, lock: bool = False) -> AnalysisConfig:
    query = select(AnalysisConfig).where(AnalysisConfig.id == config_id,
                                         AnalysisConfig.project_id == project_id)
    item = session.scalar(query.with_for_update() if lock else query)
    if item is None:
        raise HTTPException(404, "配置不存在")
    return item


def _digest(name: str, definitions: list[dict], rules: list[dict], sections: list[dict]) -> str:
    raw = json.dumps([name, definitions, rules, sections], ensure_ascii=False,
                     sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(raw.encode()).hexdigest()


def _dict(item: AnalysisConfig) -> dict:
    return {"id": item.id, "project_id": item.project_id, "version": item.version,
            "revision": item.revision, "status": item.status, "name": item.name,
            "definitions": item.definitions, "rules": item.rules, "sections": item.sections,
            "checksum": item.checksum, "created_at": item.created_at.isoformat(),
            "published_at": item.published_at.isoformat() if item.published_at else None}


def _normalize(definitions: list[dict], rules: list[dict], sections: list[dict],
               *, complete: bool = False) -> tuple[list[dict], list[dict], list[dict]]:
    fields = []
    keys: set[str] = set()
    for raw in definitions:
        key = str(raw.get("key", "")).strip()
        label = str(raw.get("label", "")).strip()
        data_type = raw.get("data_type")
        unit = str(raw.get("unit", "")).strip()
        if not _key.fullmatch(key) or key in keys or not label or len(label) > 100:
            raise HTTPException(400, "字段标识需唯一且使用英文字母、数字或下划线")
        if data_type not in {"integer", "decimal", "text", "boolean"} or len(unit) > 40:
            raise HTTPException(400, f"字段 {key} 的类型或单位无效")
        if not isinstance(raw.get("computed"), bool):
            raise HTTPException(400, f"字段 {key} 缺少输入/计算属性")
        if raw["computed"] and data_type not in {"integer", "decimal"}:
            raise HTTPException(400, f"计算字段 {key} 必须是数值类型")
        group = str(raw.get("group") or "project").strip()
        if not group or len(group) > 50:
            raise HTTPException(400, f"字段 {key} 的分组无效")
        keys.add(key)
        fields.append({"key": key, "label": label, "data_type": data_type,
                       "unit": unit, "group": group, "computed": raw["computed"],
                       "input_format": "percentage" if raw.get("input_format") == "percentage" else "number",
                       "caliber": str(raw.get("caliber") or "")[:240],
                       "period": str(raw.get("period") or "")[:80], "source_ref": None})
    by_key = {field["key"]: field for field in fields}
    normalized_rules = []
    ids: set[str] = set()
    targets: set[str] = set()
    for raw in rules:
        rule_id = str(raw.get("id", "")).strip()
        name = str(raw.get("name", "")).strip()
        target = str(raw.get("target_key", "")).strip()
        expression = str(raw.get("expression", "")).strip()
        if not _rule_id.fullmatch(rule_id) or rule_id in ids or not name or len(name) > 100:
            raise HTTPException(400, "规则标识需唯一，且名称不能为空")
        if target not in by_key or not by_key[target]["computed"] or target in targets:
            raise HTTPException(400, f"规则 {rule_id} 的目标字段无效或重复")
        if len(expression) > 300:
            raise HTTPException(400, f"规则 {rule_id} 的表达式过长")
        try:
            _, deps = parse_expression(expression)
        except RuleError as exc:
            raise HTTPException(400, f"规则 {rule_id}：{exc}") from exc
        if any(key not in by_key for key in deps) or any(by_key[key]["data_type"] == "text" for key in deps):
            raise HTTPException(400, f"规则 {rule_id} 引用了不存在或文本类型字段")
        ids.add(rule_id); targets.add(target)
        normalized_rules.append({"id": rule_id, "name": name, "target_key": target,
                                 "expression": expression, "deps": deps,
                                 "version": hashlib.sha256(json.dumps([target, expression, deps],
                                          ensure_ascii=False).encode()).hexdigest()[:16],
                                 "source_ref": None})
    try:
        sort_rules([SimpleNamespace(**rule) for rule in normalized_rules], keys)
    except RuleError as exc:
        raise HTTPException(400, str(exc)) from exc
    normalized_sections = []
    section_ids: set[str] = set()
    for raw in sections:
        section_id = str(raw.get("id", "")).strip()
        title = str(raw.get("title", "")).strip()
        result_keys = raw.get("result_keys")
        if not _section_id.fullmatch(section_id) or section_id in section_ids or not title or len(title) > 100:
            raise HTTPException(400, "章节标识需唯一，且标题不能为空")
        if (not isinstance(result_keys, list) or not result_keys
                or any(not isinstance(key, str) for key in result_keys)
                or len(set(result_keys)) != len(result_keys)
                or any(key not in by_key or by_key[key]["data_type"] not in {"integer", "decimal"}
                       for key in result_keys)):
            raise HTTPException(400, f"章节 {section_id} 的指标选择无效")
        evidence_keys = raw.get("evidence_keys", [])
        if (not isinstance(evidence_keys, list) or any(not isinstance(key, str) for key in evidence_keys)
                or len(set(evidence_keys)) != len(evidence_keys)
                or any(key not in by_key or by_key[key]["computed"] for key in evidence_keys)):
            raise HTTPException(400, f"章节 {section_id} 的证据字段无效")
        conditions = raw.get("conditions", [])
        if not isinstance(conditions, list) or len(conditions) > 12:
            raise HTTPException(400, f"章节 {section_id} 的条件数量无效")
        checked_conditions = []
        condition_ids: set[str] = set()
        for condition in conditions:
            if not isinstance(condition, dict):
                raise HTTPException(400, f"章节 {section_id} 的条件无效")
            condition_id = str(condition.get("id", "")).strip()
            expression = str(condition.get("expression", "")).strip()
            yes = str(condition.get("when_true", "")).strip()
            no = str(condition.get("when_false", "")).strip()
            if (not _rule_id.fullmatch(condition_id) or condition_id in condition_ids
                    or not yes or not no or yes == no or len(yes) > 240 or len(no) > 240
                    or any(char.isdigit() for char in yes + no)):
                raise HTTPException(400, f"章节 {section_id} 的条件文字或标识无效；数值须由运行引用生成")
            try:
                tree, deps = parse_expression(expression)
            except RuleError as exc:
                raise HTTPException(400, f"章节条件 {condition_id}：{exc}") from exc
            if (not isinstance(tree.body, ast.Compare) or
                    any(key not in by_key or by_key[key]["data_type"] not in {"integer", "decimal"}
                        for key in deps)):
                raise HTTPException(400, f"章节条件 {condition_id} 必须比较已配置的数值字段")
            condition_ids.add(condition_id)
            checked_conditions.append({"id": condition_id, "expression": expression,
                                       "deps": deps, "when_true": yes, "when_false": no})
        forbidden_terms = raw.get("forbidden_terms", [])
        if (not isinstance(forbidden_terms, list) or len(forbidden_terms) > 20
                or any(not isinstance(term, str) or not term.strip() or len(term) > 40
                       for term in forbidden_terms)):
            raise HTTPException(400, f"章节 {section_id} 的禁用表述无效")
        section_ids.add(section_id)
        normalized_sections.append({"id": section_id, "title": title,
                                    "result_keys": result_keys, "evidence_keys": evidence_keys,
                                    "conditions": checked_conditions,
                                    "forbidden_terms": [term.strip() for term in forbidden_terms]})
    if complete and (not fields or not normalized_rules or not normalized_sections
                     or {field["key"] for field in fields if field["computed"]} != targets):
        raise HTTPException(400, "发布前须配置输入、每个计算字段的规则及至少一个章节")
    return fields, normalized_rules, normalized_sections


def _token(item: AnalysisConfig, expires: int) -> str:
    raw = f"{item.project_id}:{item.id}:{item.revision}:{item.checksum}:{expires}"
    return f"{expires}:{hmac.new(_secret, raw.encode(), hashlib.sha256).hexdigest()}"


@router.get("")
def config_list(project_id: str):
    with SessionLocal() as session:
        _project(session, project_id)
        return [_dict(item) for item in session.scalars(select(AnalysisConfig).where(
            AnalysisConfig.project_id == project_id).order_by(AnalysisConfig.version.desc())).all()]


@router.post("", status_code=201)
def config_create(project_id: str, body: ConfigCreate):
    if body.from_config_id and body.from_scenario_id:
        raise HTTPException(400, "只能选择一种配置来源")
    with SessionLocal.begin() as session:
        _project(session, project_id)
        existing = session.scalar(select(AnalysisConfig).where(
            AnalysisConfig.project_id == project_id, AnalysisConfig.status == "DRAFT"))
        if existing:
            raise HTTPException(409, "请先完成当前配置草稿")
        if body.from_config_id:
            previous = _config(session, project_id, body.from_config_id)
            if previous.status != "PUBLISHED":
                raise HTTPException(409, "只能从已发布版本建立新草稿")
            definitions, rules, sections = (deepcopy(previous.definitions),
                                            deepcopy(previous.rules), deepcopy(previous.sections))
        elif body.from_scenario_id:
            previous = _scenario(session, project_id, body.from_scenario_id)
            definitions = [{**{key: value for key, value in field.items() if key != "source_ref"},
                            "source_ref": None} for field in previous.definitions]
            rules = [{**{key: value for key, value in rule.items() if key != "source_ref"},
                      "source_ref": None} for rule in previous.rules]
            sections = []
        else:
            definitions, rules, sections = [], [], []
        version = (session.scalar(select(func.max(AnalysisConfig.version)).where(
            AnalysisConfig.project_id == project_id)) or 0) + 1
        name = body.name.strip()
        item = AnalysisConfig(project_id=project_id, version=version, name=name,
                              definitions=definitions, rules=rules, sections=sections,
                              checksum=_digest(name, definitions, rules, sections))
        session.add(item); session.flush()
        return _dict(item)


@router.get("/{config_id}")
def config_get(project_id: str, config_id: str):
    with SessionLocal() as session:
        return _dict(_config(session, project_id, config_id))


@router.put("/{config_id}")
def config_update(project_id: str, config_id: str, body: ConfigUpdate):
    fields, rules, sections = _normalize(body.definitions, body.rules, body.sections)
    with SessionLocal.begin() as session:
        item = _config(session, project_id, config_id, lock=True)
        if item.status != "DRAFT":
            raise HTTPException(409, "已发布配置不可修改，请建立新版本")
        if item.revision != body.revision:
            raise HTTPException(409, "配置草稿已变化，请刷新")
        name = body.name.strip()
        checksum = _digest(name, fields, rules, sections)
        if checksum != item.checksum:
            item.name, item.definitions, item.rules, item.sections = name, fields, rules, sections
            item.checksum, item.revision = checksum, item.revision + 1
        session.flush()
        return _dict(item)


@router.post("/{config_id}/test")
def config_test(project_id: str, config_id: str, body: ConfigTest):
    with SessionLocal() as session:
        item = _config(session, project_id, config_id)
        if item.status != "DRAFT" or item.revision != body.revision:
            raise HTTPException(409, "配置版本已变化，请刷新后再试算")
        fields, rules, sections = _normalize(item.definitions, item.rules, item.sections, complete=True)
        inputs = {}
        expected = {field["key"] for field in fields if not field["computed"]}
        if set(body.sample_inputs) != expected:
            raise HTTPException(400, "试算输入须与配置字段一致")
        for field in fields:
            if field["computed"]:
                continue
            try:
                value = _canonical(body.sample_inputs[field["key"]], field["data_type"])
            except RuleError as exc:
                raise HTTPException(400, f"{field['label']}：{exc}") from exc
            inputs[field["key"]] = {"value": value, "origin": "scenario_assumption" if value is not None else "missing",
                                     "source_ref": None, "review_status": "unverified"}
        trial = SimpleNamespace(id=f"config-test:{item.id}", revision=item.revision,
                                blueprint_version=f"config:{item.id}", corpus_id=None,
                                corpus_version=None, definitions=fields, rules=rules)
        snapshot = _calculate(trial, inputs)
        snapshot["condition_results"] = condition_results(snapshot, sections)
        if snapshot["status"] != "COMPUTED" or any(
                snapshot["results"][key]["value"] is None
                for section in sections for key in section["result_keys"]) or any(
                result["outcome"] is None for result in snapshot["condition_results"].values()):
            return {"status": "UNEVALUABLE", "snapshot": snapshot, "test_token": None}
        expires = int(time.time()) + 600
        return {"status": "COMPUTED", "snapshot": snapshot,
                "test_token": _token(item, expires), "expires_at": expires}


@router.post("/{config_id}/publish")
def config_publish(project_id: str, config_id: str, body: ConfigPublish):
    with SessionLocal.begin() as session:
        item = _config(session, project_id, config_id, lock=True)
        if item.status != "DRAFT" or item.revision != body.revision:
            raise HTTPException(409, "配置版本已变化，请重新试算")
        _normalize(item.definitions, item.rules, item.sections, complete=True)
        try:
            expires = int(body.test_token.split(":", 1)[0])
        except (ValueError, IndexError) as exc:
            raise HTTPException(409, "试算凭证无效") from exc
        if expires < time.time() or not hmac.compare_digest(body.test_token, _token(item, expires)):
            raise HTTPException(409, "试算凭证已失效，请重新试算")
        item.status = "PUBLISHED"
        item.published_at = utcnow()
        session.flush()
        return _dict(item)
