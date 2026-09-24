"""首版 API：项目事实事务与内置语料只读浏览。"""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import re
import secrets
import time
from contextlib import asynccontextmanager
from datetime import date
from decimal import Decimal, InvalidOperation
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from urllib.parse import quote
from uuid import uuid4

from fastapi import FastAPI, File, HTTPException, Query, UploadFile
from fastapi.responses import FileResponse, Response
from pydantic import BaseModel, Field
from sqlalchemy import delete, select

from .corpus import CorpusRepository
from .db import ExtractionCandidate, ExtractionRun, FactEvidenceBinding, FactRevision, Project, ProjectCorpus, ProjectFact, ReportDraft, ReportVersion, RuleRecord, SessionLocal, SourceDocument, WritingBinding, WritingCommitEvent, WritingReference, init_db, utcnow
from .document_pipeline import MAX_FILE_BYTES, STORAGE, model_candidates, parse_original, sha256, source_supports, table_segments
from .model_settings import is_configured, parse_document_page, resolve_model, router as model_router
from .report_pipeline import change_impact, content_hash, export_bundle, gate, model_section, numeric_tokens, plain, report_fact_impacts, validate_content
from .rules import EvalValue, RuleError, evaluate, parse_expression, sort_rules, unit_dimension
from .writing import SLOTS, SLOT_BY_ID, render_cases, source_cases
from .writing_support import GUIDED_SECTIONS, accepted_reference, build_candidate, prepare_model_input, package as writing_package, sections as writing_sections
from .writing_model import ModelInputChanged
from .writing_materials import router as material_router
from .corpus_storage import CorpusArchive, CorpusRecord


@asynccontextmanager
async def lifespan(_app: FastAPI):
    """启动时检查数据库结构和内置语料。

    Args:
        _app: FastAPI 应用。

    Yields:
        应用运行时段。
    """
    init_db()
    corpus.categories()
    ensure_builtin_project()
    ensure_report_snapshots()
    yield


app = FastAPI(title="通用专业报告平台", version="0.1.0", lifespan=lifespan)
app.include_router(model_router)
app.include_router(material_router)
corpus = CorpusRepository()
preview_secret = secrets.token_bytes(32)
BUILTIN_PROJECT_ID = "92b09c9d-801c-5fcb-90e6-67bbcd5179a6"
BUILTIN_PROJECT_NAME = "澄岳精密 · 电池托盘焊接与检测技改项目"


def ensure_builtin_project() -> None:
    """在 PostgreSQL 中幂等登记语料所属项目，不复制或改写冻结语料。"""
    with SessionLocal.begin() as session:
        project = session.get(Project, BUILTIN_PROJECT_ID)
        if project is None:
            project = Project(id=BUILTIN_PROJECT_ID, name=BUILTIN_PROJECT_NAME)
            session.add(project)
        if session.get(ProjectCorpus, BUILTIN_PROJECT_ID) is None:
            session.add(ProjectCorpus(project_id=BUILTIN_PROJECT_ID, corpus_id=corpus.manifest["corpus_id"]))


def ensure_report_snapshots() -> None:
    """Backfill the current snapshot for drafts created before version history existed."""
    with SessionLocal.begin() as session:
        for item in session.scalars(select(ReportDraft)).all():
            if session.get(ReportVersion, (item.id, item.version)) is None:
                session.add(ReportVersion(report_id=item.id, version=item.version, content=item.content,
                                          bound_facts=item.bound_facts, reviewed_hash=item.reviewed_hash))


def project_dict(project: Project) -> dict:
    return {"id": project.id, "name": project.name, "version": project.version,
            "created_at": project.created_at.isoformat(), "has_corpus": project.corpus is not None,
            "is_builtin": project.id == BUILTIN_PROJECT_ID}


def get_corpus_project(project_id: str) -> None:
    with SessionLocal() as session:
        project = get_project(session, project_id)
        if project.corpus is None or project.corpus.corpus_id != corpus.manifest["corpus_id"]:
            fail("该项目没有关联的语料", 404)


def require_writable_project(project: Project) -> None:
    if project.corpus is not None:
        fail("内置语料项目为只读；请新建项目录入事实", 403)


class ProjectCreate(BaseModel):
    name: str = Field(min_length=1, max_length=160)


class FactCreate(BaseModel):
    key: str = Field(pattern=r"^[A-Za-z][A-Za-z0-9_]{0,99}$")
    label: str = Field(min_length=1, max_length=160)
    data_type: str
    unit: str = ""
    caliber: str = ""
    as_of: str = ""
    source: str = ""


class RuleCreate(BaseModel):
    name: str = Field(min_length=1, max_length=160)
    target_key: str
    expression: str = Field(min_length=1, max_length=1000)


class FactChange(BaseModel):
    fact_key: str
    value: str | int | bool | None = None
    source: str = ""
    caliber: str | None = None
    as_of: str | None = None
    reason: str = "人工修改"


class CommitRequest(FactChange):
    base_version: int
    preview_token: str


class BindingUpdate(BaseModel):
    bindings: dict[str, str]


class CandidateDecision(BaseModel):
    label: str = Field(min_length=1, max_length=160)
    value: str = Field(min_length=1)
    unit: str = Field(default="", max_length=80)
    data_type: str
    source_ref: str
    support_refs: list[str] = Field(default_factory=list, max_length=2)
    key: str | None = Field(default=None, pattern=r"^[A-Za-z][A-Za-z0-9_]{0,99}$")
    caliber: str = ""


class ReportCreate(BaseModel):
    title: str = Field(min_length=1, max_length=160)


class ReportSave(BaseModel):
    content: list[dict]
    base_version: int
    preview_token: str | None = None


class SectionDraft(BaseModel):
    title: str = Field(min_length=1, max_length=160)
    fact_keys: list[str] = Field(min_length=1, max_length=12)


class SectionCommit(SectionDraft):
    paragraphs: list[dict] = Field(min_length=1, max_length=3)
    base_version: int = Field(ge=0)
    project_version: int = Field(ge=0)
    preview_token: str


class WritingReferenceSelect(BaseModel):
    corpus_id: str
    corpus_version: str
    target_report_type: str
    accepted: bool


class WritingPackDraft(BaseModel):
    section_id: str = Field(min_length=1, max_length=80)
    approved_item_ids: list[str] = Field(default_factory=list, max_length=80)
    mode: str = "guided"
    expected_input_sha256: str | None = None


class WritingPackCommit(WritingPackDraft):
    model_input: dict | None = None
    input_sha256: str | None = None
    model_call: dict = Field(default_factory=dict)
    model_usage: list[dict] = Field(default_factory=list)
    paragraphs: list[dict] = Field(min_length=2, max_length=8)
    issues: list[dict] = Field(default_factory=list)
    used_items: list[str] = Field(default_factory=list)
    rejected_items: list[dict] = Field(default_factory=list)
    base_version: int = Field(ge=0)
    project_version: int = Field(ge=0)
    corpus_id: str
    corpus_version: str
    expires_at: int
    preview_token: str


class EvidenceBindRequest(BaseModel):
    document_id: str
    source_refs: list[str] = Field(min_length=1, max_length=3)


def fail(message: str, status: int = 400) -> None:
    """统一返回用户可读的业务错误。

    Args:
        message: 错误说明。
        status: HTTP 状态码。

    Raises:
        HTTPException: 总是抛出。
    """
    raise HTTPException(status_code=status, detail=message)


def get_project(session, project_id: str, lock: bool = False) -> Project:
    """读取指定项目，可选行锁以保护版本提交。

    Args:
        session: SQLAlchemy 会话。
        project_id: 项目 ID。
        lock: 是否锁定项目行。

    Returns:
        项目记录。

    Raises:
        HTTPException: 项目不存在。
    """
    statement = select(Project).where(Project.id == project_id)
    if lock:
        statement = statement.with_for_update()
    project = session.scalar(statement)
    if not project:
        fail("项目不存在", 404)
    return project


def fact_dict(fact: ProjectFact) -> dict:
    """将项目事实转换为前端契约。

    Args:
        fact: 数据库事实行。

    Returns:
        不含历史语料值的项目事实对象。
    """
    return {
        "id": fact.id,
        "key": fact.key,
        "label": fact.label,
        "data_type": fact.data_type,
        "value": fact.value_text,
        "status": fact.value_status,
        "unit": fact.unit,
        "caliber": fact.caliber,
        "as_of": fact.as_of,
        "source": fact.source,
        "revision": fact.revision,
        "updated_at": fact.updated_at.isoformat() if fact.updated_at else None,
    }


def canonical_value(value: Any, data_type: str) -> str | None:
    """校验用户输入并将权威数值存为十进制字符串。

    Args:
        value: 前端提供的值；None 表示未定义。
        data_type: 事实声明类型。

    Returns:
        规范化字符串或 None。

    Raises:
        RuleError: 输入不符合类型。
    """
    if value is None or value == "":
        return None
    if data_type in ("decimal", "integer"):
        if isinstance(value, bool):
            raise RuleError("布尔值不能作为数值事实")
        try:
            number = Decimal(str(value))
        except InvalidOperation as exc:
            raise RuleError("请输入有效数值") from exc
        if not number.is_finite():
            raise RuleError("数值必须是有限值")
        if data_type == "integer" and number != number.to_integral_value():
            raise RuleError("该事实只能输入整数")
        return format(number, "f")
    if data_type == "boolean":
        if value is True or str(value).lower() == "true":
            return "true"
        if value is False or str(value).lower() == "false":
            return "false"
        raise RuleError("布尔事实只能输入 true 或 false")
    if data_type == "date":
        try:
            return date.fromisoformat(str(value)).isoformat()
        except ValueError as exc:
            raise RuleError("日期格式应为 YYYY-MM-DD") from exc
    if data_type in ("text", "enum"):
        return str(value)
    raise RuleError("不支持的事实类型")


def _for_eval(fact: dict) -> EvalValue:
    if fact["data_type"] == "boolean":
        return EvalValue(fact["value"] == "true")
    if fact["data_type"] not in ("decimal", "integer"):
        raise RuleError(f"事实 {fact['key']} 不是可计算类型")
    return EvalValue(Decimal(fact["value"]), unit_dimension(fact["unit"]), fact["unit"] or "")


def simulate(facts: list[ProjectFact], rules: list[RuleRecord], change: FactChange | None = None) -> tuple[dict[str, dict], list[dict]]:
    """在内存快照上前向计算，不修改 ORM 行。

    Args:
        facts: 当前项目事实。
        rules: 当前项目规则。
        change: 可选的输入事实提议。

    Returns:
        提议事实快照和逐规则计算解释。

    Raises:
        RuleError: 类型、单位、循环或算术错误。
    """
    state = {fact.key: fact_dict(fact) for fact in facts}
    ordered = sort_rules(rules, set(state))
    targets = {rule.target_key for rule in rules}
    if change:
        if change.fact_key not in state:
            raise RuleError("事实不存在")
        if change.fact_key in targets:
            raise RuleError("计算结果只能通过输入事实和规则更新")
        current = state[change.fact_key]
        current["value"] = canonical_value(change.value, current["data_type"])
        current["status"] = "UNDEFINED" if current["value"] is None else "PROVIDED"
        current["source"] = change.source.strip()
        if change.caliber is not None:
            current["caliber"] = change.caliber.strip()
        if change.as_of is not None:
            current["as_of"] = change.as_of.strip()
    trace = []
    for rule in ordered:
        missing = [key for key in rule.deps if state[key]["value"] is None or state[key]["status"] in ("UNDEFINED", "UNEVALUABLE")]
        target = state[rule.target_key]
        if missing:
            target["value"] = None
            target["status"] = "UNEVALUABLE"
            trace.append({"rule_id": rule.id, "name": rule.name, "target": rule.target_key, "expression": rule.expression, "deps": rule.deps, "status": "UNEVALUABLE", "missing": missing, "result": None})
            continue
        inputs = {key: _for_eval(state[key]) for key in rule.deps}
        result = evaluate(rule.expression, inputs)
        if target["data_type"] == "boolean":
            if not isinstance(result.value, bool):
                raise RuleError(f"规则 {rule.name} 的目标应为布尔值")
            value = "true" if result.value else "false"
        elif target["data_type"] in ("decimal", "integer"):
            if isinstance(result.value, bool):
                raise RuleError(f"规则 {rule.name} 的目标应为数值")
            expected = unit_dimension(target["unit"])
            if result.dimension != expected and not (result.value == 0 and not result.dimension):
                raise RuleError(f"规则 {rule.name} 的结果单位与目标事实不匹配")
            if result.unit_tag and target["unit"] and result.unit_tag != target["unit"]:
                raise RuleError(f"规则 {rule.name} 的结果单位与目标事实不一致；请显式换算")
            value = canonical_value(result.value, target["data_type"])
        else:
            raise RuleError("规则目标只能是数值或布尔事实")
        target["value"] = value
        target["status"] = "COMPUTED"
        trace.append({"rule_id": rule.id, "name": rule.name, "target": rule.target_key, "expression": rule.expression, "deps": rule.deps, "status": "COMPUTED", "missing": [], "result": value, "inputs": {key: state[key]["value"] for key in rule.deps}})
    return state, trace


def differences(facts: list[ProjectFact], state: dict[str, dict]) -> list[dict]:
    """找出提议快照与持久化事实的差异。

    Args:
        facts: 数据库现值。
        state: 提议值。

    Returns:
        按事实 key 排序的变化清单。
    """
    changes = []
    for fact in facts:
        before, after = fact_dict(fact), state[fact.key]
        fields = ("value", "status", "source", "caliber", "as_of")
        if any(before[field] != after[field] for field in fields):
            changes.append({"key": fact.key, "label": fact.label, "unit": fact.unit, "before": {field: before[field] for field in fields}, "after": {field: after[field] for field in fields}})
    return sorted(changes, key=lambda item: item["key"])


def _signature(project_id: str, version: int, change: FactChange, expires: int) -> str:
    payload = json.dumps({"project_id": project_id, "version": version, "change": change.model_dump(), "expires": expires}, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    return hmac.new(preview_secret, payload.encode("utf-8"), hashlib.sha256).hexdigest()


@app.get("/api/health")
def health():
    return {"status": "ok", "service": "report-platform"}


@app.get("/api/model/status")
def model_status():
    configured = is_configured("extraction")
    return {"configured": configured,
            "model": resolve_model("extraction").model if configured else "",
            "ocr_configured": is_configured("ocr")}


@app.get("/api/projects/{project_id}/corpus/summary")
def corpus_summary(project_id: str):
    get_corpus_project(project_id)
    return {**corpus.summary(), "project_id": project_id}


@app.get("/api/projects/{project_id}/corpus/categories")
def corpus_categories(project_id: str):
    get_corpus_project(project_id)
    return [{**item, "project_id": project_id} for item in corpus.categories()]


@app.get("/api/projects/{project_id}/corpus/categories/{number}")
def corpus_category(project_id: str, number: int, page: int = Query(1, ge=1), size: int = Query(30, ge=1, le=100), search: str = "", node_group: str = "", status: str = "", owner: str = "", rule_id: str = "", date_from: str = "", date_to: str = "", blocking: str = ""):
    get_corpus_project(project_id)
    try:
        return {**corpus.category(number, page, size, search, node_group, status, owner, rule_id, date_from, date_to, blocking), "project_id": project_id}
    except ValueError as exc:
        fail(str(exc), 404)


@app.get("/api/projects/{project_id}/corpus/artifacts/{artifact_id}")
def corpus_artifact(project_id: str, artifact_id: str):
    get_corpus_project(project_id)
    try:
        return {**corpus.artifact(artifact_id), "project_id": project_id}
    except ValueError as exc:
        fail(str(exc), 404)


@app.get("/api/projects/{project_id}/corpus/graph")
def corpus_graph(project_id: str, focus: str = "N034", depth: int = Query(1, ge=1, le=2), edge_types: str = "", node_kinds: str = "", direction: str = "both"):
    get_corpus_project(project_id)
    try:
        return {**corpus.graph_slice(focus, depth, set(filter(None, edge_types.split(","))) or None, set(filter(None, node_kinds.split(","))) or None, direction), "project_id": project_id}
    except ValueError as exc:
        fail(str(exc), 404)


@app.get("/api/projects/{project_id}/corpus/graph/search")
def corpus_graph_search(project_id: str, q: str = ""):
    get_corpus_project(project_id)
    return corpus.search_entities(q)


@app.get("/api/projects/{project_id}/corpus/entities/{entity_id}")
def corpus_entity(project_id: str, entity_id: str):
    get_corpus_project(project_id)
    try:
        return {**corpus.entity(entity_id), "project_id": project_id}
    except ValueError as exc:
        fail(str(exc), 404)


@app.get("/api/projects/{project_id}/corpus/raw")
def corpus_raw(project_id: str, path: str):
    get_corpus_project(project_id)
    try:
        content, media_type, filename = corpus.raw_bytes(path)
    except ValueError as exc:
        fail(str(exc), 404)
    return Response(content=content, media_type=media_type,
                    headers={"Content-Disposition": f"inline; filename*=UTF-8''{quote(filename)}"})


@app.get("/api/projects")
def projects_list():
    with SessionLocal() as session:
        projects = session.scalars(select(Project).order_by(Project.created_at.desc())).all()
        return [project_dict(item) for item in projects]


@app.post("/api/projects", status_code=201)
def projects_create(body: ProjectCreate):
    with SessionLocal.begin() as session:
        item = Project(name=body.name.strip())
        session.add(item)
        session.flush()
        return project_dict(item)


@app.get("/api/projects/{project_id}")
def project_get(project_id: str):
    with SessionLocal() as session:
        item = get_project(session, project_id)
        return project_dict(item)


def _document(session, project_id: str, document_id: str) -> SourceDocument:
    item = session.scalar(select(SourceDocument).where(SourceDocument.id == document_id, SourceDocument.project_id == project_id))
    if item is None:
        fail("项目文件不存在", 404)
    return item


def _document_dict(item: SourceDocument) -> dict:
    return {"id": item.id, "filename": item.filename, "file_kind": item.file_kind,
            "sha256": item.sha256, "pages": item.pages, "segments": len(item.segments),
            "status": item.status, "created_at": item.created_at.isoformat()}


@app.get("/api/projects/{project_id}/documents")
def documents_list(project_id: str):
    with SessionLocal() as session:
        get_project(session, project_id)
        items = session.scalars(select(SourceDocument).where(SourceDocument.project_id == project_id).order_by(SourceDocument.created_at.desc())).all()
        return [_document_dict(item) for item in items]


@app.post("/api/projects/{project_id}/documents", status_code=201)
async def documents_upload(project_id: str, file: UploadFile = File(...)):
    with SessionLocal() as session:
        require_writable_project(get_project(session, project_id))
    filename = Path(file.filename or "").name
    kind = Path(filename).suffix.lower().removeprefix(".")
    if kind not in ("pdf", "docx") or not filename:
        fail("只支持 PDF 或 DOCX 文件")
    data = await file.read(MAX_FILE_BYTES + 1)
    if not data or len(data) > MAX_FILE_BYTES:
        fail("文件为空或超过 30 MB")
    try:
        pages, segments, status = parse_original(data, kind)
    except Exception as exc:
        fail(f"文件解析失败：{str(exc)[:120]}")
    if not segments and status != "OCR_REQUIRED":
        fail("文件没有可解析文字")
    storage_name = f"{uuid4().hex}.{kind}"
    with SessionLocal.begin() as session:
        project = get_project(session, project_id)
        require_writable_project(project)
        existing = session.scalar(select(SourceDocument).where(SourceDocument.project_id == project_id, SourceDocument.sha256 == sha256(data)))
        if existing:
            fail("同一项目已上传该文件", 409)
        STORAGE.mkdir(parents=True, exist_ok=True)
        (STORAGE / storage_name).write_bytes(data)
        item = SourceDocument(project_id=project_id, filename=filename[:240], file_kind=kind,
                              sha256=sha256(data), storage_name=storage_name, pages=pages,
                              segments=segments, status=status)
        session.add(item)
        session.flush()
        return _document_dict(item)


@app.get("/api/projects/{project_id}/documents/{document_id}")
def document_detail(project_id: str, document_id: str, page: int = Query(1, ge=1)):
    with SessionLocal() as session:
        item = _document(session, project_id, document_id)
        if page > item.pages:
            fail("页码超出文件范围", 404)
        segments = [part for part in item.segments if part["page"] == page]
        refs = {part["ref"]: part for part in segments}
        candidates = session.scalars(select(ExtractionCandidate).where(ExtractionCandidate.document_id == document_id, ExtractionCandidate.source_ref.in_(refs)).order_by(ExtractionCandidate.created_at, ExtractionCandidate.id)).all() if refs else []
        run_ids = {candidate.extraction_run_id for candidate in candidates if candidate.extraction_run_id}
        runs = {run.id: run for run in session.scalars(select(ExtractionRun).where(ExtractionRun.id.in_(run_ids))).all()} if run_ids else {}
        approved_keys = {candidate.fact_key for candidate in candidates if candidate.fact_key}
        approved = {fact.key: fact for fact in session.scalars(select(ProjectFact).where(ProjectFact.project_id == project_id, ProjectFact.key.in_(approved_keys))).all()} if approved_keys else {}
        return {"document": _document_dict(item), "page": page,
                "segments": segments,
                "candidates": [{"id": c.id, "label": c.label, "value": c.value_text, "unit": c.unit,
                                "data_type": c.data_type, "source_ref": c.source_ref,
                                "excerpt": refs[c.source_ref]["text"] if c.source_ref in refs else "",
                                "source_valid": source_supports(c.value_text, refs[c.source_ref]["text"]) if c.source_ref in refs else False,
                                "review_status": c.review_status,
                                "extraction_run_id": c.extraction_run_id,
                                "extraction_origin": c.extraction_origin,
                                "extraction_model": runs[c.extraction_run_id].model_call.get("model") if c.extraction_run_id in runs else None,
                                "extraction_model_version": runs[c.extraction_run_id].model_call.get("model_version") if c.extraction_run_id in runs else None,
                                "fact_key": c.fact_key,
                                "approved_fact": fact_dict(approved[c.fact_key]) if c.fact_key in approved else None}
                               for c in candidates]}


@app.get("/api/projects/{project_id}/documents/{document_id}/pages")
def document_pages(project_id: str, document_id: str):
    """One compact page map for jump navigation and bounded multi-page extraction."""
    with SessionLocal() as session:
        item = _document(session, project_id, document_id)
        pages = [{"page": number, "segment_count": 0, "char_count": 0,
                  "candidate_count": 0, "pending_count": 0, "invalid_count": 0}
                 for number in range(1, item.pages + 1)]
        refs = {}
        for segment in item.segments:
            row = pages[segment["page"] - 1]
            row["segment_count"] += 1
            row["char_count"] += len(segment["text"])
            refs[segment["ref"]] = segment
        for candidate in session.scalars(select(ExtractionCandidate).where(ExtractionCandidate.document_id == document_id)).all():
            segment = refs.get(candidate.source_ref)
            if segment is None:
                continue
            row = pages[segment["page"] - 1]
            row["candidate_count"] += 1
            row["pending_count"] += candidate.review_status == "PENDING"
            row["invalid_count"] += candidate.review_status == "PENDING" and not source_supports(candidate.value_text, segment["text"])
        for row in pages:
            row["extractable"] = bool(row["segment_count"] and row["char_count"] <= 12000)
            row["reason"] = "" if row["extractable"] else ("无可读取文字，可能需要 OCR" if not row["segment_count"] else "本页文字超过 12000 字")
        return {"document_id": document_id, "pages": pages}


@app.get("/api/projects/{project_id}/documents/{document_id}/original")
def document_original(project_id: str, document_id: str):
    with SessionLocal() as session:
        item = _document(session, project_id, document_id)
        path = STORAGE / item.storage_name
        if not path.is_file():
            fail("原件文件缺失", 404)
        return FileResponse(path, filename=item.filename)


@app.post("/api/projects/{project_id}/documents/{document_id}/ocr")
def document_ocr(project_id: str, document_id: str, page: int = Query(1, ge=1)):
    """识别没有文字层的 PDF 页；结果仍是待核对的来源片段。

    Args:
        project_id: 项目标识。
        document_id: 项目文件标识。
        page: 从 1 开始的 PDF 物理页码。

    Returns:
        新增的 OCR 片段位置和字符数。
    """
    with SessionLocal() as session:
        project = get_project(session, project_id)
        require_writable_project(project)
        item = _document(session, project_id, document_id)
        if item.file_kind != "pdf" or page > item.pages:
            fail("只能识别 PDF 的有效页码")
        current = [part for part in item.segments if part["page"] == page]
        if any(part.get("kind") != "ocr" for part in current):
            fail("该页已有可读取文字，请直接抽取候选")
        source_path = STORAGE / item.storage_name
        if not source_path.is_file():
            fail("原件文件缺失", 404)
    try:
        result = parse_document_page(source_path.read_bytes(), page)
    except ValueError as exc:
        fail(str(exc), 503)
    content = result["text"].strip()
    if len(content) > 12000:
        fail("OCR 正文超过单页抽取上限，请人工拆分后处理")
    ref = f"p{page}-ocr1"
    segment = {"ref": ref, "page": page, "text": content, "kind": "ocr",
               "locator": f"第 {page} 页 · OCR 待核对", "provider": result["provider"],
               "model": result["model"], "metadata": result["metadata"]}
    with SessionLocal.begin() as session:
        item = session.scalar(select(SourceDocument).where(
            SourceDocument.id == document_id, SourceDocument.project_id == project_id).with_for_update())
        if item is None:
            fail("项目文件不存在", 404)
        current = [part for part in item.segments if part["page"] == page]
        if any(part.get("kind") != "ocr" for part in current):
            fail("该页已有可读取文字，请直接抽取候选", 409)
        old = next((part for part in current if part["ref"] == ref), None)
        if old:
            if old["text"] != content:
                fail("本页已有 OCR 结果；请先核对已保存的来源位置", 409)
            return {"ref": ref, "created": False, "chars": len(content), "review_status": "PENDING_REVIEW"}
        item.segments = [*item.segments, segment]
        return {"ref": ref, "created": True, "chars": len(content), "review_status": "PENDING_REVIEW"}


@app.post("/api/projects/{project_id}/documents/{document_id}/extract")
def document_extract(project_id: str, document_id: str, page: int = Query(1, ge=1)):
    with SessionLocal() as session:
        project = get_project(session, project_id)
        require_writable_project(project)
        item = _document(session, project_id, document_id)
        if page > item.pages:
            fail("页码超出文件范围", 404)
        source_path = STORAGE / item.storage_name
        kind = item.file_kind
    additions = table_segments(source_path.read_bytes(), page) if kind == "pdf" and source_path.is_file() else []
    with SessionLocal() as session:
        item = session.scalar(select(SourceDocument).where(SourceDocument.id == document_id, SourceDocument.project_id == project_id))
        if item is None:
            fail("项目文件不存在", 404)
        original_segments = item.segments
        prepared_segments = original_segments
        current = [part for part in item.segments if part["page"] == page]
        table_by_ref = {part["ref"]: part for part in additions}
        existing_refs = {part["ref"] for part in current}
        new_rows = [part for part in additions if part["ref"] not in existing_refs]
        # 表格行是原件的另一种定位视图；保留旧片段 ref，避免已有候选与事实回链失效。
        if sum(len(part["text"]) for part in current + new_rows) <= 12000 and additions:
            candidate_refs = set(session.scalars(select(ExtractionCandidate.source_ref).where(
                ExtractionCandidate.document_id == document_id)).all())
            for saved_source in session.scalars(select(ProjectFact.source).where(
                ProjectFact.project_id == project_id, ProjectFact.source.like(f"document:{document_id}#%"))).all():
                match = re.match(r"^document:[a-f0-9-]+#([^ ]+)", saved_source)
                if match:
                    candidate_refs.update(match[1].split("+"))
            refreshed_segments = []
            for part in item.segments:
                if part["page"] == page and part.get("kind") == "table_row":
                    if part["ref"] in table_by_ref:
                        refreshed_segments.append(table_by_ref[part["ref"]])
                    elif part["ref"] in candidate_refs:
                        refreshed_segments.append(part)
                    continue
                refreshed_segments.append(part)
            refreshed_segments += new_rows
            prepared_segments = refreshed_segments
            current = [part for part in refreshed_segments if part["page"] == page]
        segments = current
        if not segments:
            fail("该页没有可抽取文字；扫描页需要 OCR")
        if sum(len(part["text"]) for part in segments) > 12000:
            fail("该页文字过长，暂不能一次抽取")
    table_proposals = [{**fact, "source_ref": segment["ref"]} for segment in segments
                       if segment.get("kind") == "table_row" for fact in segment.get("facts", [])]
    input_refs = [part["ref"] for part in segments]
    input_sha256 = hashlib.sha256(json.dumps(
        [{"ref": part["ref"], "text": part["text"]} for part in segments],
        ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()
    try:
        selected_model = resolve_model("extraction")
        attempted_model_call = {"model_id": selected_model.id, "model": selected_model.model,
                                "model_version": None, "source": selected_model.source,
                                "protocol": selected_model.protocol, "temperature": 0, "max_tokens": 3000,
                                "task": "extraction", "input_kind": "located_page_segments"}
    except ValueError:
        attempted_model_call = {"task": "extraction", "model": "unconfigured",
                                "input_kind": "located_page_segments"}
    try:
        model_proposals = model_candidates(segments)
    except ValueError as exc:
        # A failed model request is an audit event, but it never creates candidates or facts.
        with SessionLocal.begin() as session:
            session.add(ExtractionRun(project_id=project_id, document_id=document_id, page=page,
                                      status="FAILED", model_call=attempted_model_call,
                                      input_refs=input_refs, input_sha256=input_sha256, error=str(exc)[:240]))
        fail(str(exc), 503)
    model_call = dict(getattr(model_proposals, "model_call", {}) or attempted_model_call)
    model_call["task"] = "extraction"
    model_call.setdefault("input_kind", "located_page_segments")
    with SessionLocal.begin() as session:
        project = get_project(session, project_id)
        require_writable_project(project)
        item = session.scalar(select(SourceDocument).where(SourceDocument.id == document_id, SourceDocument.project_id == project_id).with_for_update())
        if item is None:
            fail("项目文件不存在", 404)
        if item.segments != original_segments:
            fail("抽取期间原文片段已变化，请重新抽取", 409)
        if prepared_segments != original_segments:
            item.segments = prepared_segments
        old_candidates = session.scalars(select(ExtractionCandidate).where(ExtractionCandidate.document_id == document_id)).all()
        existing = {(c.label, c.value_text, c.source_ref): c for c in old_candidates}
        refs = {part["ref"]: part for part in item.segments if part["page"] == page}
        def identity(label: str, value: str, unit: str) -> tuple[str, str, str]:
            normalized_unit = unit.replace("平方米", "㎡").replace("m2", "㎡")
            if normalized_unit == "月":
                normalized_unit = "个月"
            return (re.sub(r"\s+", "", label), value.replace(",", ""), normalized_unit)
        seen = {identity(c.label, c.value_text, c.unit) for c in old_candidates if c.source_ref in refs}
        created = 0
        refreshed = 0
        run_id = str(uuid4())
        run = ExtractionRun(id=run_id, project_id=project_id, document_id=document_id, page=page,
                            status="SUCCEEDED", model_call=model_call,
                            input_refs=input_refs, input_sha256=input_sha256,
                            returned_count=len(model_proposals), accepted_count=0)
        session.add(run)
        session.flush()
        for origin, proposal in [("TABLE", proposal) for proposal in table_proposals] + [("MODEL", proposal) for proposal in model_proposals]:
            signature = (proposal["label"], proposal["value_text"], proposal["source_ref"])
            if proposal["source_ref"] not in refs:
                continue
            proposal["source_valid"] = source_supports(proposal["value_text"], refs[proposal["source_ref"]]["text"])
            if signature in existing:
                old = existing[signature]
                if old is not None and old.review_status == "PENDING" and old.source_valid != proposal["source_valid"]:
                    old.source_valid = proposal["source_valid"]
                    refreshed += 1
            elif identity(proposal["label"], proposal["value_text"], proposal["unit"]) in seen:
                continue
            else:
                session.add(ExtractionCandidate(document_id=document_id, extraction_run_id=run_id,
                                                extraction_origin=origin, **proposal))
                existing[signature] = None
                seen.add(identity(proposal["label"], proposal["value_text"], proposal["unit"]))
                created += 1
        run.accepted_count = created
        return {"created": created, "refreshed": refreshed, "model_returned": len(model_proposals),
                "table_returned": len(table_proposals), "page": page}


@app.get("/api/projects/{project_id}/documents/{document_id}/extraction-runs")
def document_extraction_runs(project_id: str, document_id: str, page: int = Query(1, ge=1)):
    with SessionLocal() as session:
        get_project(session, project_id)
        item = _document(session, project_id, document_id)
        if page > item.pages:
            fail("页码超出文件范围", 404)
        rows = session.scalars(select(ExtractionRun).where(
            ExtractionRun.project_id == project_id, ExtractionRun.document_id == document_id,
            ExtractionRun.page == page).order_by(ExtractionRun.created_at.desc()).limit(20)).all()
        run_ids = [row.id for row in rows]
        candidates = session.scalars(select(ExtractionCandidate).where(
            ExtractionCandidate.extraction_run_id.in_(run_ids))).all() if run_ids else []
        candidate_ids: dict[str, list[str]] = {run_id: [] for run_id in run_ids}
        for candidate in candidates:
            candidate_ids[candidate.extraction_run_id].append(candidate.id)
        return [{"id": row.id, "status": row.status, "model_call": row.model_call,
                 "input_refs": row.input_refs, "input_sha256": row.input_sha256,
                 "returned_count": row.returned_count, "accepted_count": row.accepted_count,
                 "candidate_ids": candidate_ids[row.id],
                 "error": row.error, "created_at": row.created_at.isoformat()} for row in rows]


@app.post("/api/projects/{project_id}/documents/{document_id}/candidates/{candidate_id}/reject")
def candidate_reject(project_id: str, document_id: str, candidate_id: str):
    with SessionLocal.begin() as session:
        project = get_project(session, project_id)
        require_writable_project(project)
        _document(session, project_id, document_id)
        candidate = session.scalar(select(ExtractionCandidate).where(ExtractionCandidate.id == candidate_id, ExtractionCandidate.document_id == document_id))
        if candidate is None:
            fail("候选不存在", 404)
        if candidate.review_status != "PENDING":
            fail("该候选已处理", 409)
        candidate.review_status = "REJECTED"
        return {"status": "REJECTED"}


@app.post("/api/projects/{project_id}/documents/{document_id}/candidates/{candidate_id}/approve")
def candidate_approve(project_id: str, document_id: str, candidate_id: str, body: CandidateDecision):
    with SessionLocal.begin() as session:
        project = get_project(session, project_id, lock=True)
        require_writable_project(project)
        item = _document(session, project_id, document_id)
        candidate = session.scalar(select(ExtractionCandidate).where(ExtractionCandidate.id == candidate_id, ExtractionCandidate.document_id == document_id).with_for_update())
        if candidate is None:
            fail("候选不存在", 404)
        if candidate.review_status != "PENDING":
            fail("该候选已处理", 409)
        by_ref = {part["ref"]: part for part in item.segments}
        segment = by_ref.get(body.source_ref)
        original = by_ref.get(candidate.source_ref)
        refs = [body.source_ref, *body.support_refs]
        if len(refs) != len(set(refs)) or any(ref not in by_ref for ref in refs):
            fail("补充来源位置不存在或重复")
        if original is None or any(by_ref[ref]["page"] != original["page"] for ref in refs):
            fail("候选的引用位置必须在同一页")
        if segment is None or not source_supports(body.value, segment["text"]):
            fail("引用的原文位置不能直接支持该值，请修改候选或核对原件")
        try:
            value = canonical_value(body.value, body.data_type)
        except RuleError as exc:
            fail(str(exc))
        if value is None:
            fail("不能将缺值候选确认为有效事实")
        key = body.key or f"f_{candidate.id.replace('-', '')[:16]}"
        if session.scalar(select(ProjectFact).where(ProjectFact.project_id == project_id, ProjectFact.key == key)):
            fail("事实 key 已存在，请选择其他 key", 409)
        source = f"document:{item.id}#{'+'.join(refs)} · {item.filename}"
        fact = ProjectFact(project_id=project_id, key=key, label=body.label.strip(), data_type=body.data_type,
                           value_text=value, value_status="PROVIDED", unit=body.unit.strip(),
                           caliber=body.caliber.strip(), source=source, revision=1)
        session.add(fact)
        project.version += 1
        session.add(FactRevision(project_id=project_id, fact_key=key, project_version=project.version,
                                 before={"value": None, "status": "UNDEFINED"},
                                 after={"value": value, "status": "PROVIDED", "source": source}, reason="审核文件抽取候选"))
        candidate.review_status = "APPROVED"
        candidate.fact_key = key
        session.flush()
        return {"fact": fact_dict(fact), "project_version": project.version}


def _report(session, project_id: str, report_id: str, lock: bool = False) -> ReportDraft:
    statement = select(ReportDraft).where(ReportDraft.id == report_id, ReportDraft.project_id == project_id)
    if lock:
        statement = statement.with_for_update()
    item = session.scalar(statement)
    if item is None:
        fail("报告不存在", 404)
    return item


def _report_signature(project_id: str, report_id: str, version: int, content: list[dict], expires: int) -> str:
    payload = f"{project_id}:{report_id}:{version}:{content_hash(content)}:{expires}"
    return hmac.new(preview_secret, payload.encode(), hashlib.sha256).hexdigest()


def _section_signature(project_id: str, report_id: str, body: SectionCommit, expires: int) -> str:
    payload = json.dumps({"project_id": project_id, "report_id": report_id,
                          "title": body.title, "fact_keys": body.fact_keys,
                          "paragraphs": body.paragraphs, "base_version": body.base_version,
                          "project_version": body.project_version, "expires": expires},
                         ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hmac.new(preview_secret, payload.encode(), hashlib.sha256).hexdigest()


def _writing_signature(project_id: str, report_id: str, data: dict) -> str:
    signed = {key: value for key, value in data.items() if key not in {"preview_token", "source_refs"}}
    payload = json.dumps({"project_id": project_id, "report_id": report_id, **signed},
                         ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hmac.new(preview_secret, payload.encode("utf-8"), hashlib.sha256).hexdigest()


def _current_facts(session, project_id: str) -> dict[str, ProjectFact]:
    return {fact.key: fact for fact in session.scalars(select(ProjectFact).where(ProjectFact.project_id == project_id)).all()}


def _validate_writing_refs(session, project_id: str, content: list[dict]) -> None:
    refs = [ref for block in content for ref in block.get("source_refs", [])]
    if not refs and not any(block.get("project_rule_refs") for block in content):
        return
    try:
        _, archive = accepted_reference(session, project_id)
    except ValueError as exc:
        fail(str(exc), 409)
    checked: set[tuple[int, str]] = set()
    for ref in refs:
        if ref["corpus_id"] != archive.id or ref["corpus_version"] != archive.version:
            fail("正文引用的语料版本与当前项目选择不一致", 409)
        match = re.fullmatch(r"CAT-(\d{2})", ref["category_id"])
        if not match:
            fail("正文引用的语料类别无效", 409)
        number = int(match[1])
        identity = (number, ref["record_id"])
        if identity in checked:
            continue
        row = session.get(CorpusRecord, (archive.id, number, ref["record_id"]))
        if row is None or row.artifact_id != ref["artifact_id"] or (
            "semantic_id" in ref and (row.semantic_id or "") != ref["semantic_id"]
        ):
            fail("正文来源记录不存在或身份不一致", 409)
        checked.add(identity)
    for block in content:
        for rule_ref in block.get("project_rule_refs", []):
            rule = session.get(RuleRecord, rule_ref["rule_id"])
            if (rule is None or rule.project_id != project_id or rule.target_key != rule_ref["target_key"]
                    or rule.expression != rule_ref["expression"] or list(rule.deps) != rule_ref["deps"]):
                fail("正文引用的当前项目规则不存在或身份不一致", 409)


def _writing_report_issues(session, item: ReportDraft) -> list[dict]:
    """Recheck committed source links and unresolved limitations at review/export time."""
    events = session.scalars(select(WritingCommitEvent).where(
        WritingCommitEvent.report_id == item.id,
        WritingCommitEvent.project_id == item.project_id,
    ).order_by(WritingCommitEvent.report_version.desc())).all()
    latest: dict[str, tuple[WritingCommitEvent, dict]] = {}
    for event in events:
        for binding in event.source_refs:
            latest.setdefault(binding["block_id"], (event, binding))
    issues: list[dict] = []
    present_ids = {block.get("id") for block in item.content}
    for position, block in enumerate(item.content, 1):
        block_id = block.get("id")
        committed = latest.get(block_id)
        if committed:
            event, binding = committed
            if (block.get("source_refs", []) != binding["refs"] or block.get("section_id") != binding["section_id"]
                    or block.get("project_rule_refs", []) != binding.get("project_rule_refs", [])):
                issues.append({"code": "SOURCE_LINK_CHANGED", "severity": "block", "message": f"第 {position} 段的语料来源标记已变化，请重新核对", "position": position})
            if binding["refs"] and event.corpus_id != binding["refs"][0]["corpus_id"]:
                issues.append({"code": "SOURCE_VERSION_CHANGED", "severity": "block", "message": "语料版本标记不一致", "position": position})
        elif block.get("source_refs"):
            issues.append({"code": "SOURCE_LINK_UNCONFIRMED", "severity": "block", "message": f"第 {position} 段的语料来源未经写作包确认", "position": position})
        for rule_ref in block.get("project_rule_refs", []):
            rule = session.get(RuleRecord, rule_ref["rule_id"])
            if (rule is None or rule.project_id != item.project_id or rule.expression != rule_ref["expression"]
                    or rule.target_key != rule_ref["target_key"] or list(rule.deps) != rule_ref["deps"]):
                issues.append({"code": "PROJECT_RULE_CHANGED", "severity": "block",
                               "message": f"第 {position} 段的项目计算规则无法核对", "position": position})
                continue
            keys = [entry["fact_key"] for entry in rule_ref["input_fact_revisions"]]
            if keys != rule.deps:
                issues.append({"code": "PROJECT_RULE_INPUT_CHANGED", "severity": "block",
                               "message": f"第 {position} 段的规则输入列表不一致", "position": position})
            target_fact = session.scalar(select(ProjectFact).where(ProjectFact.project_id == item.project_id,
                                                                   ProjectFact.key == rule.target_key))
            if target_fact is None or target_fact.revision != rule_ref["target_fact_revision"]:
                issues.append({"code": "PROJECT_RULE_RESULT_CHANGED", "severity": "block",
                               "message": f"第 {position} 段的计算结果修订已变化", "position": position})
            for entry in rule_ref["input_fact_revisions"]:
                fact = session.scalar(select(ProjectFact).where(ProjectFact.project_id == item.project_id,
                                                                ProjectFact.key == entry["fact_key"]))
                if fact is None or fact.revision != entry["revision"] or fact.value_text != entry["value"]:
                    issues.append({"code": "PROJECT_RULE_INPUT_CHANGED", "severity": "block",
                                   "message": f"第 {position} 段计算输入 {entry['fact_key']} 已变化", "position": position})
    for event in events:
        if not any(block_id in present_ids for block_id in event.block_ids):
            continue  # A legitimately deleted candidate no longer constrains this report.
        for problem in event.issues:
            if problem.get("code") == "POWER_CONFLICT_UNRESOLVED":
                risk_blocks = {binding["block_id"] for binding in event.source_refs
                               if any(ref.get("semantic_id") == "X001" for ref in binding["refs"])}
                if risk_blocks & present_ids:
                    issues.append({"code": problem["code"], "severity": "block", "message": problem["message"],
                                   "section_id": event.section_id, "report_version": event.report_version})
            elif problem.get("severity") == "review":
                issues.append({**problem, "section_id": event.section_id, "report_version": event.report_version})
    return issues


def _reviewed_fact_binding(session, project_id: str, fact: ProjectFact):
    """One authoritative resolution for fact gate, drawer and export citation."""
    if fact.value_status != "PROVIDED" or fact.value_text is None:
        return None
    binding = session.scalar(select(FactEvidenceBinding).where(
        FactEvidenceBinding.project_id == project_id,
        FactEvidenceBinding.fact_key == fact.key,
        FactEvidenceBinding.fact_revision == fact.revision))
    if binding is None or binding.value_text != fact.value_text:
        return None
    document = session.scalar(select(SourceDocument).where(SourceDocument.id == binding.document_id,
                                                          SourceDocument.project_id == project_id))
    if document is None:
        return None
    by_ref = {part.get("ref"): part for part in document.segments}
    if not binding.source_refs or any(ref not in by_ref for ref in binding.source_refs):
        return None
    segments = [by_ref[ref] for ref in binding.source_refs]
    if not any(source_supports(fact.value_text, str(part.get("text", ""))) for part in segments):
        return None
    return binding, document, segments


def _fact_has_project_evidence(session, project_id: str, fact: ProjectFact, checked: set[str]) -> bool:
    if fact.key in checked:
        return False
    checked.add(fact.key)
    if fact.value_text is None:
        return False
    if fact.value_status == "COMPUTED":
        rule = session.scalar(select(RuleRecord).where(RuleRecord.project_id == project_id,
                                                      RuleRecord.target_key == fact.key))
        if rule is None:
            return False
        dependencies = [session.scalar(select(ProjectFact).where(ProjectFact.project_id == project_id,
                                                                 ProjectFact.key == key)) for key in rule.deps]
        return all(dependency is not None and _fact_has_project_evidence(session, project_id, dependency, set(checked))
                   for dependency in dependencies)
    return _reviewed_fact_binding(session, project_id, fact) is not None


def _project_evidence_issues(session, item: ReportDraft) -> list[dict]:
    """Formal writing requires project-owned original evidence, not an arbitrary source string."""
    issues: list[dict] = []
    seen: set[str] = set()
    for position, block in enumerate(item.content, 1):
        for key in block.get("fact_keys", []):
            if key in seen:
                continue
            seen.add(key)
            fact = session.scalar(select(ProjectFact).where(ProjectFact.project_id == item.project_id,
                                                            ProjectFact.key == key))
            if fact is None or not _fact_has_project_evidence(session, item.project_id, fact, set()):
                issues.append({"code": "PROJECT_EVIDENCE_UNVERIFIED", "severity": "block",
                               "message": f"事实 {key} 缺少与当前修订对应、可从本项目原件核对的证据；仅能作为预审稿",
                               "fact_key": key, "position": position})
    return issues


def _report_dict(item: ReportDraft, facts: dict[str, ProjectFact], session=None) -> dict:
    used = {key for node in item.content for key in node.get("fact_keys", [])}
    writing_issues = [*_writing_report_issues(session, item), *_project_evidence_issues(session, item)] if session is not None else []
    return {"id": item.id, "project_id": item.project_id, "title": item.title, "version": item.version,
            "content": item.content, "bound_facts": item.bound_facts,
            "reviewed": item.reviewed_hash == content_hash(item.content),
            "issues": [*gate(item.content, item.reviewed_hash, item.bound_facts, facts), *writing_issues],
            "fact_impacts": report_fact_impacts(item.content, item.bound_facts, facts),
            "facts": [fact_dict(facts[key]) for key in sorted(used) if key in facts],
            "updated_at": item.updated_at.isoformat()}


@app.get("/api/projects/{project_id}/reports")
def reports_list(project_id: str):
    with SessionLocal() as session:
        get_project(session, project_id)
        items = session.scalars(select(ReportDraft).where(ReportDraft.project_id == project_id).order_by(ReportDraft.updated_at.desc())).all()
        return [{"id": item.id, "title": item.title, "version": item.version,
                 "updated_at": item.updated_at.isoformat()} for item in items]


@app.post("/api/projects/{project_id}/reports", status_code=201)
def report_create(project_id: str, body: ReportCreate):
    with SessionLocal.begin() as session:
        project = get_project(session, project_id)
        require_writable_project(project)
        item = ReportDraft(project_id=project_id, title=body.title.strip(),
                           content=[{"type": "h1", "children": [{"text": body.title.strip()}]},
                                    {"type": "p", "children": [{"text": ""}]}], bound_facts={})
        session.add(item)
        session.flush()
        session.add(ReportVersion(report_id=item.id, version=0, content=item.content, bound_facts={}))
        return _report_dict(item, _current_facts(session, project_id), session)


@app.get("/api/projects/{project_id}/reports/{report_id}")
def report_get(project_id: str, report_id: str):
    with SessionLocal() as session:
        item = _report(session, project_id, report_id)
        return _report_dict(item, _current_facts(session, project_id), session)


@app.get("/api/projects/{project_id}/reports/{report_id}/versions")
def report_versions(project_id: str, report_id: str):
    with SessionLocal() as session:
        _report(session, project_id, report_id)
        versions = session.scalars(select(ReportVersion).where(ReportVersion.report_id == report_id).order_by(ReportVersion.version.desc())).all()
        return [{"version": version.version, "reviewed": version.reviewed_hash == content_hash(version.content),
                 "created_at": version.created_at.isoformat()} for version in versions]


@app.get("/api/projects/{project_id}/reports/{report_id}/compare")
def report_compare(project_id: str, report_id: str, base: int = Query(..., ge=0)):
    with SessionLocal() as session:
        item = _report(session, project_id, report_id)
        earlier = session.get(ReportVersion, (report_id, base))
        if earlier is None:
            fail("所选历史版本不存在", 404)
        return {"base_version": base, "current_version": item.version,
                "changes": change_impact(earlier.content, item.content),
                "base_content": earlier.content}


@app.post("/api/projects/{project_id}/reports/{report_id}/preview")
def report_preview(project_id: str, report_id: str, body: ReportSave):
    try:
        validate_content(body.content)
    except ValueError as exc:
        fail(str(exc))
    with SessionLocal() as session:
        item = _report(session, project_id, report_id)
        if item.version != body.base_version:
            fail("报告已有新版本，请刷新后重试", 409)
        facts = _current_facts(session, project_id)
        _validate_writing_refs(session, project_id, body.content)
        used = {key for node in body.content for key in node.get("fact_keys", [])}
        if any(key not in facts for key in used):
            fail("正文引用了当前项目不存在的事实")
        expires = int(time.time()) + 600
        return {"base_version": item.version, "expires_at": expires,
                "preview_token": f"{expires}.{_report_signature(project_id, report_id, item.version, body.content, expires)}",
                "changes": change_impact(item.content, body.content),
                "fact_keys_affected": sorted({key for change in change_impact(item.content, body.content) for key in change["fact_keys"]})}


def _current_fact_snapshots_for_saved_content(session, item: ReportDraft, content: list[dict],
                                              facts: dict[str, ProjectFact]) -> dict[str, dict]:
    """Refresh only facts whose every visible use and provenance is current.

    A text-only edit may replace a Plate fact token, so a token is not required.
    All paragraphs citing the key must instead pass the same numeric checks as
    review. Existing corpus and project-rule links are checked against their
    committed block identities and current rule input revisions.
    """
    used = {key for block in content for key in block.get("fact_keys", [])}
    trial_bound = {**item.bound_facts, **{
        key: {"value": facts[key].value_text, "revision": facts[key].revision}
        for key in used if key in facts and facts[key].value_text is not None
    }}
    trial_issues = gate(content, None, trial_bound, facts)
    trial_item = SimpleNamespace(id=item.id, project_id=item.project_id, content=content)
    provenance_issues = _writing_report_issues(session, trial_item)
    refresh: dict[str, dict] = {}
    for key in used:
        fact = facts.get(key)
        if fact is None or fact.value_text is None or fact.value_status not in {"PROVIDED", "COMPUTED"}:
            continue
        positions = {index for index, block in enumerate(content, 1) if key in block.get("fact_keys", [])}
        if any(issue.get("severity") == "block" and (
            issue.get("fact_key") == key or issue.get("position") in positions
        ) for issue in (*trial_issues, *provenance_issues)):
            continue
        previous = item.bound_facts.get(key, {}).get("value")
        matching = True
        for position in positions:
            block = content[position - 1]
            visible = plain(block)
            if fact.data_type in {"integer", "decimal"}:
                if previous is not None and previous != fact.value_text:
                    try:
                        if any(Decimal(token) == Decimal(previous) for token in numeric_tokens(visible)):
                            matching = False
                    except InvalidOperation:
                        matching = False
            elif fact.value_text not in visible:
                matching = False
            elif previous and previous != fact.value_text and previous in visible.replace(fact.value_text, ""):
                matching = False
            if not matching:
                break
        if matching:
            refresh[key] = {"value": fact.value_text, "revision": fact.revision}
    return refresh


@app.put("/api/projects/{project_id}/reports/{report_id}")
def report_save(project_id: str, report_id: str, body: ReportSave):
    try:
        validate_content(body.content)
        expires_text, signature = (body.preview_token or "").split(".", 1)
        expires = int(expires_text)
    except (ValueError, AttributeError) as exc:
        fail(f"保存前需要有效预览：{str(exc)[:80]}", 409)
    if expires < time.time() or not hmac.compare_digest(signature, _report_signature(project_id, report_id, body.base_version, body.content, expires)):
        fail("预览已过期或正文发生变化，请重新预览", 409)
    with SessionLocal.begin() as session:
        project = get_project(session, project_id)
        require_writable_project(project)
        item = _report(session, project_id, report_id, lock=True)
        if item.version != body.base_version:
            fail("报告已有新版本，请刷新后重试", 409)
        facts = _current_facts(session, project_id)
        _validate_writing_refs(session, project_id, body.content)
        used = {key for node in body.content for key in node.get("fact_keys", [])}
        if any(key not in facts for key in used):
            fail("正文引用了当前项目不存在的事实")
        impacts = change_impact(item.content, body.content)
        refresh = _current_fact_snapshots_for_saved_content(session, item, body.content, facts)
        bound = {**item.bound_facts, **refresh}
        if impacts or bound != item.bound_facts:
            item.bound_facts = bound
            item.content = body.content
            item.reviewed_hash = None
            item.version += 1
            session.add(ReportVersion(report_id=item.id, version=item.version, content=item.content,
                                      bound_facts=item.bound_facts, reviewed_hash=None))
        return {"report": _report_dict(item, facts, session), "changes": impacts}


@app.post("/api/projects/{project_id}/reports/{report_id}/generate/preview")
def report_generate_preview(project_id: str, report_id: str, body: SectionDraft):
    if len(set(body.fact_keys)) != len(body.fact_keys):
        fail("请勿重复选择事实")
    title = body.title.strip()
    if not title:
        fail("请输入章节标题")
    with SessionLocal() as session:
        project = get_project(session, project_id)
        require_writable_project(project)
        item = _report(session, project_id, report_id)
        facts = _current_facts(session, project_id)
        selected = []
        for key in body.fact_keys:
            fact = facts.get(key)
            if fact is None or fact.value_text is None or fact.value_status not in ("PROVIDED", "COMPUTED"):
                fail(f"事实 {key} 未定义或不可用于起草")
            selected.append({"key": key, "label": fact.label, "value": fact.value_text,
                             "unit": fact.unit, "source": fact.source})
        base_version, project_version = item.version, project.version
    try:
        paragraphs = model_section(title, selected)
        validate_content([{"type": "h2", "children": [{"text": title}]}, *paragraphs])
        if any(paragraph.get("type") != "p" or paragraph.get("origin") != "model" or
               not paragraph.get("fact_keys") or not set(paragraph["fact_keys"]).issubset(body.fact_keys)
               for paragraph in paragraphs):
            raise ValueError("模型候选引用结构不正确")
    except ValueError as exc:
        fail(f"{exc}；现有报告没有改变", 503)
    with SessionLocal() as session:
        project = get_project(session, project_id)
        item = _report(session, project_id, report_id)
        if project.version != project_version or item.version != base_version:
            fail("生成期间项目事实或报告已变化，请重新生成候选", 409)
        try:
            validate_content([*item.content, {"type": "h2", "children": [{"text": title}]}, *paragraphs])
        except ValueError as exc:
            fail(str(exc))
    expires = int(time.time()) + 600
    unsigned = SectionCommit(title=title, fact_keys=body.fact_keys, paragraphs=paragraphs,
                             base_version=base_version, project_version=project_version, preview_token="")
    return {"title": title, "fact_keys": body.fact_keys, "paragraphs": paragraphs,
            "base_version": base_version, "project_version": project_version,
            "expires_at": expires,
            "preview_token": f"{expires}.{_section_signature(project_id, report_id, unsigned, expires)}"}


@app.post("/api/projects/{project_id}/reports/{report_id}/generate/commit")
def report_generate_commit(project_id: str, report_id: str, body: SectionCommit):
    try:
        expires_text, signature = body.preview_token.split(".", 1)
        expires = int(expires_text)
    except (ValueError, AttributeError):
        fail("生成候选预览凭证无效", 409)
    if expires < time.time() or not hmac.compare_digest(signature, _section_signature(project_id, report_id, body, expires)):
        fail("生成候选已变化或预览已过期，请重新生成", 409)
    if len(set(body.fact_keys)) != len(body.fact_keys):
        fail("请勿重复选择事实")
    title = body.title.strip()
    if title != body.title or any(paragraph.get("type") != "p" or paragraph.get("origin") != "model" or
                                  not paragraph.get("fact_keys") or not set(paragraph["fact_keys"]).issubset(body.fact_keys)
                                  for paragraph in body.paragraphs):
        fail("生成候选结构不正确")
    with SessionLocal.begin() as session:
        project = get_project(session, project_id, lock=True)
        require_writable_project(project)
        item = _report(session, project_id, report_id, lock=True)
        if project.version != body.project_version or item.version != body.base_version:
            fail("项目事实或报告已变化，请重新生成候选", 409)
        facts = _current_facts(session, project_id)
        for key in body.fact_keys:
            fact = facts.get(key)
            if fact is None or fact.value_text is None or fact.value_status not in ("PROVIDED", "COMPUTED"):
                fail(f"事实 {key} 已变化，请重新生成候选", 409)
        content = [*item.content, {"type": "h2", "children": [{"text": title}]}, *body.paragraphs]
        try:
            validate_content(content)
        except ValueError as exc:
            fail(str(exc))
        bound = dict(item.bound_facts)
        for key in body.fact_keys:
            fact = facts[key]
            bound[key] = {"value": fact.value_text, "revision": fact.revision}
        item.content, item.bound_facts, item.reviewed_hash = content, bound, None
        item.version += 1
        session.add(ReportVersion(report_id=item.id, version=item.version, content=content, bound_facts=bound))
        session.flush()
        return _report_dict(item, facts, session)


@app.post("/api/projects/{project_id}/reports/{report_id}/review")
def report_review(project_id: str, report_id: str):
    with SessionLocal.begin() as session:
        project = get_project(session, project_id)
        require_writable_project(project)
        item = _report(session, project_id, report_id, lock=True)
        facts = _current_facts(session, project_id)
        used = {key for node in item.content for key in node.get("fact_keys", [])}
        for key in used:
            fact = facts.get(key)
            if fact is None or fact.value_text is None or fact.value_status not in ("PROVIDED", "COMPUTED"):
                fail(f"事实 {key} 尚不可用于正式报告")
        if not used or not any(plain(node).strip() for node in item.content):
            fail("报告缺少正文或事实引用")
        proposed = {key: {"value": facts[key].value_text, "revision": facts[key].revision} for key in used}
        _validate_writing_refs(session, project_id, item.content)
        blockers = [issue for issue in [*gate(item.content, content_hash(item.content), proposed, facts),
                                       *_writing_report_issues(session, item),
                                       *_project_evidence_issues(session, item)] if issue["severity"] == "block"]
        if blockers:
            fail("请先修正正文：" + "；".join(issue["message"] for issue in blockers))
        item.bound_facts = proposed
        item.reviewed_hash = content_hash(item.content)
        snapshot = session.get(ReportVersion, (item.id, item.version))
        if snapshot is not None:
            snapshot.reviewed_hash = item.reviewed_hash
            snapshot.bound_facts = item.bound_facts
        return _report_dict(item, facts, session)


@app.get("/api/projects/{project_id}/reports/{report_id}/export")
def report_export(project_id: str, report_id: str, level: str = Query("formal", pattern="^(formal|preview)$")):
    with SessionLocal() as session:
        item = _report(session, project_id, report_id)
        if level == "preview" and not any(block.get("type") not in {"h1", "h2", "h3"} and plain(block).strip()
                                           for block in item.content):
            fail("报告尚无可预览的正文", 409)
        facts = _current_facts(session, project_id)
        _validate_writing_refs(session, project_id, item.content)
        issues = [*gate(item.content, item.reviewed_hash, item.bound_facts, facts),
                  *_writing_report_issues(session, item), *_project_evidence_issues(session, item)]
        always_block = {"SOURCE_LINK_CHANGED", "SOURCE_LINK_UNCONFIRMED", "SOURCE_VERSION_CHANGED",
                        "PROJECT_RULE_CHANGED", "PROJECT_RULE_INPUT_CHANGED", "PROJECT_RULE_RESULT_CHANGED",
                        "FACT_REF_DISPLAY_MISMATCH", "UNSUPPORTED_NUMBER", "FACT_TEXT_STALE",
                        "FACT_CHANGED", "FACT_MISSING", "UNCITED_NUMBER", "EMPTY"}
        blockers = [issue for issue in issues if issue["severity"] == "block" and
                    (level == "formal" or issue["code"] in always_block)]
        if blockers:
            fail("导出受阻：" + "；".join(issue["message"] for issue in blockers), 409)
        used = {key for node in item.content for key in node.get("fact_keys", [])}
        evidence_audit = []
        for key in sorted(used):
            fact = facts[key]
            binding = session.scalar(select(FactEvidenceBinding).where(
                FactEvidenceBinding.project_id == project_id,
                FactEvidenceBinding.fact_key == key,
                FactEvidenceBinding.fact_revision == fact.revision))
            document = session.get(SourceDocument, binding.document_id) if binding else None
            resolved = _reviewed_fact_binding(session, project_id, fact)
            computed = fact.value_status == "COMPUTED"
            reviewed = _fact_has_project_evidence(session, project_id, fact, set())
            rule = session.scalar(select(RuleRecord).where(
                RuleRecord.project_id == project_id, RuleRecord.target_key == key)) if computed else None
            input_fact_refs = []
            for dependency_key in (rule.deps if rule else []):
                dependency = facts.get(dependency_key)
                if dependency is None:
                    input_fact_refs.append({"fact_key": dependency_key, "status": "missing"})
                    continue
                input_binding = session.scalar(select(FactEvidenceBinding).where(
                    FactEvidenceBinding.project_id == project_id,
                    FactEvidenceBinding.fact_key == dependency_key,
                    FactEvidenceBinding.fact_revision == dependency.revision))
                input_document = session.get(SourceDocument, input_binding.document_id) if input_binding else None
                input_resolved = _reviewed_fact_binding(session, project_id, dependency)
                input_reviewed = _fact_has_project_evidence(session, project_id, dependency, set())
                input_fact_refs.append({
                    "fact_key": dependency_key, "fact_revision": dependency.revision,
                    "status": ("derived_from_reviewed_inputs" if dependency.value_status == "COMPUTED" else
                               "source_locator_reviewed") if input_reviewed else "unverified",
                    "document_id": input_binding.document_id if input_binding else None,
                    "document_filename": input_document.filename if input_document else None,
                    "document_sha256": input_document.sha256 if input_document else None,
                    "source_refs": input_binding.source_refs if input_binding else [],
                    "location_labels": [part.get("locator") or part["ref"] for part in input_resolved[2]]
                    if input_resolved else [],
                    "bound_at": input_binding.created_at.isoformat() if input_binding else None,
                })
            evidence_audit.append({"fact_key": key, "fact_revision": fact.revision,
                                   "status": ("derived_from_reviewed_inputs" if reviewed else "derived_inputs_unverified")
                                             if computed else "source_locator_reviewed" if reviewed else "unverified",
                                   "document_id": binding.document_id if binding else None,
                                   "document_filename": document.filename if document else None,
                                   "document_sha256": document.sha256 if document else None,
                                   "source_refs": binding.source_refs if binding else [],
                                   "location_labels": [part.get("locator") or part["ref"] for part in resolved[2]]
                                   if resolved else [],
                                   "bound_at": binding.created_at.isoformat() if binding else None,
                                   "project_rule": {"rule_id": rule.id, "expression": rule.expression,
                                                    "target_key": rule.target_key, "deps": rule.deps} if rule else None,
                                   "input_fact_refs": input_fact_refs})
        audit = {"report_id": item.id, "project_id": project_id, "report_version": item.version,
                 "content_sha256": content_hash(item.content),
                 "delivery_status": "preview_only" if level == "preview" else "source_locator_reviewed",
                 "delivery_note": "预审稿；项目证据或冲突仍待核，不构成正式结论" if level == "preview" else
                                  "项目原文位置由操作者核对；不代表原件真实性经独立认证",
                 "source_refs": [{"block_id": block.get("id"), "section_id": block.get("section_id"),
                                  "refs": block.get("source_refs", []),
                                  "project_rule_refs": block.get("project_rule_refs", [])}
                                 for block in item.content if block.get("source_refs")],
                 "writing_issues": issues,
                 "writing_model_audits": _model_audits(session, project_id, report_id),
                 "evidence_bindings": evidence_audit,
                 "facts": [{"key": key, "label": facts[key].label, "value": facts[key].value_text, "unit": facts[key].unit,
                            "revision": facts[key].revision, "source": facts[key].source} for key in sorted(used)]}
        data = export_bundle(item.title, item.content, audit,
                             preview_label="预审稿 · 来源待核 · 不构成正式结论" if level == "preview" else None)
        return Response(content=data, media_type="application/zip",
                        headers={"Content-Disposition": f'attachment; filename="report-{item.id[:8]}-v{item.version}{"-preview" if level == "preview" else ""}.zip"'})


@app.get("/api/projects/{project_id}/facts")
def facts_list(project_id: str):
    with SessionLocal() as session:
        project = get_project(session, project_id)
        facts = session.scalars(select(ProjectFact).where(ProjectFact.project_id == project_id).order_by(ProjectFact.created_at, ProjectFact.key)).all()
        return {"project_version": project.version, "facts": [fact_dict(fact) for fact in facts]}


@app.get("/api/projects/{project_id}/facts/{fact_key}/source")
def fact_source(project_id: str, fact_key: str):
    """Resolve one cited fact to its stored excerpt and original document locator."""
    with SessionLocal() as session:
        get_project(session, project_id)
        fact = session.scalar(select(ProjectFact).where(ProjectFact.project_id == project_id, ProjectFact.key == fact_key))
        if fact is None:
            fail("项目事实不存在", 404)
        reviewed = _reviewed_fact_binding(session, project_id, fact)
        if reviewed:
            binding, document, segments = reviewed
            return _document_fact_source(project_id, fact, document, binding.source_refs, segments,
                                         "SOURCE_LOCATOR_REVIEWED", "本项目原件，原文位置由操作者核对")
        if fact.value_status == "COMPUTED":
            rule = session.scalar(select(RuleRecord).where(RuleRecord.project_id == project_id,
                                                          RuleRecord.target_key == fact_key))
            if rule is not None:
                inputs = []
                for key in rule.deps:
                    dependency = session.scalar(select(ProjectFact).where(ProjectFact.project_id == project_id,
                                                                         ProjectFact.key == key))
                    supported = dependency is not None and _fact_has_project_evidence(
                        session, project_id, dependency, set())
                    inputs.append({"key": key, "label": dependency.label if dependency else key,
                                   "value": dependency.value_text if dependency else None,
                                   "unit": dependency.unit if dependency else "",
                                   "revision": dependency.revision if dependency else None,
                                   "review_status": "SOURCE_LOCATOR_REVIEWED" if supported else "UNVERIFIED",
                                   "source_url": f"/api/projects/{project_id}/facts/{quote(key, safe='')}/source"})
                complete = _fact_has_project_evidence(session, project_id, fact, set())
                return {"kind": "rule", "fact": fact_dict(fact),
                        "description": "由本项目规则计算；可逐项核对上游事实",
                        "review_status": "DERIVED_FROM_REVIEWED_INPUTS" if complete else "DERIVED_INPUTS_UNVERIFIED",
                        "rule": {"id": rule.id, "name": rule.name, "target_key": rule.target_key,
                                 "expression": rule.expression, "deps": rule.deps},
                        "input_facts": inputs, "excerpt": None, "original_url": None}
        source = fact.source or ""
        match = re.match(r"^document:([a-f0-9-]+)#([^ ]+)(?: · .*)?$", source)
        if not match:
            return {"kind": "manual", "fact": fact_dict(fact), "description": source or "尚未标注来源",
                    "review_status": "UNVERIFIED", "excerpt": None, "original_url": None}
        document = session.scalar(select(SourceDocument).where(SourceDocument.id == match[1], SourceDocument.project_id == project_id))
        if document is None:
            return {"kind": "missing", "fact": fact_dict(fact), "description": "来源文件已不可用",
                    "review_status": "UNVERIFIED", "excerpt": None, "original_url": None}
        refs = match[2].split("+")
        by_ref = {item["ref"]: item for item in document.segments}
        segments = [by_ref.get(ref) for ref in refs]
        if any(segment is None for segment in segments):
            return {"kind": "missing", "fact": fact_dict(fact), "description": "来源位置已不可用",
                    "filename": document.filename, "review_status": "UNVERIFIED",
                    "excerpt": None, "original_url": None}
        return _document_fact_source(project_id, fact, document, refs, segments, "UNVERIFIED", source)


def _document_fact_source(project_id: str, fact: ProjectFact, document: SourceDocument,
                          refs: list[str], segments: list[dict], review_status: str,
                          description: str) -> dict:
    page = segments[0].get("page")
    url = f"/api/projects/{project_id}/documents/{document.id}/original"
    if document.file_kind == "pdf" and page:
        url += f"#page={page}"
    return {"kind": "document", "fact": fact_dict(fact), "description": description,
            "review_status": review_status, "document_id": document.id,
            "document_sha256": document.sha256, "original_source": fact.source or "",
            "filename": document.filename, "source_ref": segments[0]["ref"], "source_refs": refs, "page": page,
            "locator": "；".join(segment.get("locator") or (f"第 {page} 页 · {segment['ref']}" if page else segment["ref"])
                                for segment in segments),
            "excerpt": "\n\n".join(f"【{segment['ref']}】{segment['text']}" for segment in segments),
            "excerpts": [{"ref": segment["ref"], "text": segment["text"]} for segment in segments],
            "original_url": url}


@app.get("/api/projects/{project_id}/facts/{fact_key}/evidence")
def fact_evidence_get(project_id: str, fact_key: str):
    with SessionLocal() as session:
        get_project(session, project_id)
        fact = session.scalar(select(ProjectFact).where(ProjectFact.project_id == project_id,
                                                       ProjectFact.key == fact_key))
        if fact is None:
            fail("项目事实不存在", 404)
        binding = session.scalar(select(FactEvidenceBinding).where(
            FactEvidenceBinding.project_id == project_id,
            FactEvidenceBinding.fact_key == fact_key,
            FactEvidenceBinding.fact_revision == fact.revision))
        return {"fact_key": fact_key, "fact_revision": fact.revision,
                "status": "SOURCE_LOCATOR_REVIEWED" if _reviewed_fact_binding(session, project_id, fact) else "UNVERIFIED",
                "document_id": binding.document_id if binding else None,
                "source_refs": binding.source_refs if binding else []}


@app.post("/api/projects/{project_id}/facts/{fact_key}/evidence/bind")
def fact_evidence_bind(project_id: str, fact_key: str, body: EvidenceBindRequest):
    with SessionLocal.begin() as session:
        project = get_project(session, project_id, lock=True)
        require_writable_project(project)
        fact = session.scalar(select(ProjectFact).where(ProjectFact.project_id == project_id,
                                                        ProjectFact.key == fact_key).with_for_update())
        if fact is None:
            fail("项目事实不存在", 404)
        if fact.value_status != "PROVIDED" or fact.value_text is None:
            fail("仅原始有效事实可绑定原文；计算事实通过规则依赖回链", 409)
        document = session.scalar(select(SourceDocument).where(
            SourceDocument.id == body.document_id, SourceDocument.project_id == project_id))
        if document is None:
            fail("当前项目原件不存在", 404)
        if len(set(body.source_refs)) != len(body.source_refs):
            fail("原文位置不能重复")
        by_ref = {str(part.get("ref")): part for part in document.segments}
        if any(ref not in by_ref for ref in body.source_refs):
            fail("原文位置不存在或不属于当前项目", 409)
        selected = [by_ref[ref] for ref in body.source_refs]
        if not any(source_supports(fact.value_text, str(segment.get("text", ""))) for segment in selected):
            fail("所选原文位置不能直接支持当前事实值", 409)
        binding = session.scalar(select(FactEvidenceBinding).where(
            FactEvidenceBinding.project_id == project_id,
            FactEvidenceBinding.fact_key == fact_key,
            FactEvidenceBinding.fact_revision == fact.revision).with_for_update())
        if binding is None:
            binding = FactEvidenceBinding(project_id=project_id, fact_key=fact_key,
                                          fact_revision=fact.revision, document_id=document.id,
                                          source_refs=list(body.source_refs), value_text=fact.value_text)
            session.add(binding)
        elif binding.document_id != document.id or binding.source_refs != body.source_refs or binding.value_text != fact.value_text:
            binding.document_id, binding.source_refs, binding.value_text = document.id, list(body.source_refs), fact.value_text
            binding.created_at = utcnow()
        else:
            return {"fact_key": fact_key, "fact_revision": fact.revision, "status": "SOURCE_LOCATOR_REVIEWED",
                    "document_id": document.id, "source_refs": body.source_refs,
                    "project_version": project.version}
        project.version += 1
        for report in session.scalars(select(ReportDraft).where(ReportDraft.project_id == project_id)).all():
            if any(fact_key in block.get("fact_keys", []) for block in report.content):
                report.reviewed_hash = None
                snapshot = session.get(ReportVersion, (report.id, report.version))
                if snapshot is not None:
                    snapshot.reviewed_hash = None
        session.flush()
        return {"fact_key": fact_key, "fact_revision": fact.revision, "status": "SOURCE_LOCATOR_REVIEWED",
                "document_id": document.id, "source_refs": body.source_refs,
                "project_version": project.version}


@app.post("/api/projects/{project_id}/facts", status_code=201)
def facts_create(project_id: str, body: FactCreate):
    if body.data_type not in ("decimal", "integer", "boolean", "date", "text", "enum"):
        fail("不支持的事实类型")
    with SessionLocal.begin() as session:
        project = get_project(session, project_id, lock=True)
        require_writable_project(project)
        if session.scalar(select(ProjectFact).where(ProjectFact.project_id == project_id, ProjectFact.key == body.key)):
            fail("该事实 key 已存在", 409)
        fact = ProjectFact(project_id=project_id, **body.model_dump())
        session.add(fact)
        project.version += 1
        session.flush()
        return fact_dict(fact)


@app.get("/api/projects/{project_id}/rules")
def rules_list(project_id: str):
    with SessionLocal() as session:
        get_project(session, project_id)
        rules = session.scalars(select(RuleRecord).where(RuleRecord.project_id == project_id).order_by(RuleRecord.created_at)).all()
        facts = session.scalars(select(ProjectFact).where(ProjectFact.project_id == project_id)).all()
        try:
            _, trace = simulate(facts, rules)
        except RuleError as exc:
            trace = [{"status": "ERROR", "reason": str(exc)}]
        return {"rules": [{"id": rule.id, "name": rule.name, "target_key": rule.target_key, "expression": rule.expression, "deps": rule.deps} for rule in rules], "trace": trace}


@app.post("/api/projects/{project_id}/rules", status_code=201)
def rules_create(project_id: str, body: RuleCreate):
    try:
        _, deps = parse_expression(body.expression)
    except RuleError as exc:
        fail(str(exc))
    with SessionLocal.begin() as session:
        project = get_project(session, project_id, lock=True)
        require_writable_project(project)
        facts = session.scalars(select(ProjectFact).where(ProjectFact.project_id == project_id)).all()
        rules = session.scalars(select(RuleRecord).where(RuleRecord.project_id == project_id)).all()
        if body.target_key not in {fact.key for fact in facts}:
            fail("规则目标事实不存在")
        if any(rule.target_key == body.target_key for rule in rules):
            fail("目标事实已有规则", 409)
        rule = RuleRecord(project_id=project_id, name=body.name.strip(), target_key=body.target_key, expression=body.expression.strip(), deps=deps)
        try:
            state, _ = simulate(facts, [*rules, rule])
        except RuleError as exc:
            fail(str(exc))
        project.version += 1
        session.add(rule)
        for fact in facts:
            new = state[fact.key]
            if fact.value_text != new["value"] or fact.value_status != new["status"]:
                before = {"value": fact.value_text, "status": fact.value_status}
                fact.value_text, fact.value_status = new["value"], new["status"]
                fact.revision += 1
                session.add(FactRevision(project_id=project_id, fact_key=fact.key, project_version=project.version, before=before, after={"value": fact.value_text, "status": fact.value_status}, reason="新增规则后重算"))
        session.flush()
        return {"id": rule.id, "name": rule.name, "target_key": rule.target_key, "expression": rule.expression, "deps": rule.deps}


@app.post("/api/projects/{project_id}/changes/preview")
def changes_preview(project_id: str, body: FactChange):
    with SessionLocal() as session:
        project = get_project(session, project_id)
        require_writable_project(project)
        facts = session.scalars(select(ProjectFact).where(ProjectFact.project_id == project_id)).all()
        rules = session.scalars(select(RuleRecord).where(RuleRecord.project_id == project_id)).all()
        try:
            state, trace = simulate(facts, rules, body)
        except RuleError as exc:
            fail(str(exc))
        expires = int(time.time()) + 600
        changes = differences(facts, state)
        changed = {item["key"]: item for item in changes}
        report_impacts = []
        if changed:
            reports = session.scalars(select(ReportDraft).where(ReportDraft.project_id == project_id)).all()
            for report in reports:
                for position, node in enumerate(report.content, 1):
                    for key in node.get("fact_keys", []):
                        if key in changed:
                            report_impacts.append({"report_id": report.id, "report_title": report.title,
                                                   "report_version": report.version, "position": position,
                                                   "text": plain(node), "fact_key": key,
                                                   "before": changed[key]["before"], "after": changed[key]["after"]})
        return {"base_version": project.version, "expires_at": expires, "preview_token": f"{expires}.{_signature(project_id, project.version, body, expires)}", "changes": changes, "trace": trace, "report_impacts": report_impacts}


@app.post("/api/projects/{project_id}/changes/commit")
def changes_commit(project_id: str, body: CommitRequest):
    change = FactChange(**body.model_dump(exclude={"base_version", "preview_token"}))
    # 有效期作为预览票据的一部分由客户端带回，不从不可信值推断有效性。
    # 票据格式为 expires.signature；避免服务器保留预览状态或落库。
    try:
        expires_text, signature = body.preview_token.split(".", 1)
        expires = int(expires_text)
    except (ValueError, AttributeError):
        fail("预览凭证无效", 409)
    if expires < time.time() or not hmac.compare_digest(signature, _signature(project_id, body.base_version, change, expires)):
        fail("预览凭证无效或已过期", 409)
    with SessionLocal.begin() as session:
        project = get_project(session, project_id, lock=True)
        require_writable_project(project)
        if project.version != body.base_version:
            fail("项目已有新版本，请重新预览", 409)
        facts = session.scalars(select(ProjectFact).where(ProjectFact.project_id == project_id)).all()
        rules = session.scalars(select(RuleRecord).where(RuleRecord.project_id == project_id)).all()
        try:
            state, trace = simulate(facts, rules, change)
        except RuleError as exc:
            fail(str(exc))
        changes = differences(facts, state)
        if not changes:
            return {"project_version": project.version, "changes": [], "trace": trace}
        project.version += 1
        by_key = {fact.key: fact for fact in facts}
        for item in changes:
            fact = by_key[item["key"]]
            after = item["after"]
            fact.value_text = after["value"]
            fact.value_status = after["status"]
            fact.source = after["source"]
            fact.caliber = after["caliber"]
            fact.as_of = after["as_of"]
            fact.revision += 1
            session.add(FactRevision(project_id=project_id, fact_key=fact.key, project_version=project.version, before=item["before"], after=after, reason=change.reason))
        return {"project_version": project.version, "changes": changes, "trace": trace}


@app.get("/api/projects/{project_id}/revisions")
def revisions_list(project_id: str, fact_key: str = ""):
    with SessionLocal() as session:
        get_project(session, project_id)
        statement = select(FactRevision).where(FactRevision.project_id == project_id)
        if fact_key:
            statement = statement.where(FactRevision.fact_key == fact_key)
        entries = session.scalars(statement.order_by(FactRevision.created_at.desc()).limit(200)).all()
        return [{"id": item.id, "fact_key": item.fact_key, "project_version": item.project_version, "before": item.before, "after": item.after, "reason": item.reason, "created_at": item.created_at.isoformat()} for item in entries]


def _writing_rows(session, project_id: str):
    facts = session.scalars(select(ProjectFact).where(ProjectFact.project_id == project_id).order_by(ProjectFact.key)).all()
    rules = session.scalars(select(RuleRecord).where(RuleRecord.project_id == project_id)).all()
    bindings = {item.slot_id: item.fact_key for item in session.scalars(select(WritingBinding).where(WritingBinding.project_id == project_id)).all()}
    return facts, rules, bindings


@app.get("/api/projects/{project_id}/writing/reference")
def writing_reference_get(project_id: str):
    with SessionLocal() as session:
        project = get_project(session, project_id)
        require_writable_project(project)
        archive = session.get(CorpusArchive, corpus.manifest["corpus_id"])
        if archive is None:
            fail("只读语料尚未入库", 503)
        selected = session.get(WritingReference, project_id)
        return {"selected": selected is not None, "project_id": project_id,
                "source_project_id": BUILTIN_PROJECT_ID, "corpus_id": archive.id,
                "corpus_version": archive.version,
                "target_report_type": selected.target_report_type if selected else None,
                "compatible": selected is not None and selected.target_report_type == "feasibility",
                "note": "选择仅允许只读参考，不表示历史事实或证据已经独立核实"}


@app.put("/api/projects/{project_id}/writing/reference")
def writing_reference_select(project_id: str, body: WritingReferenceSelect):
    if body.accepted is not True:
        fail("请明确确认只读参考语料的使用边界")
    if body.target_report_type not in {"feasibility", "accident_investigation", "other"}:
        fail("目标报告类型不受支持")
    if body.target_report_type != "feasibility":
        fail("澄岳精密语料仅适用于可研/建设类写作试验；其他类型不可套用", 409)
    with SessionLocal.begin() as session:
        project = get_project(session, project_id, lock=True)
        require_writable_project(project)
        archive = session.get(CorpusArchive, body.corpus_id)
        if archive is None or archive.version != body.corpus_version or archive.id != corpus.manifest["corpus_id"]:
            fail("所选语料及版本不存在或不受支持", 404)
        current = session.get(WritingReference, project_id)
        if current is not None and (current.corpus_id, current.corpus_version, current.target_report_type) != (
            body.corpus_id, body.corpus_version, body.target_report_type
        ):
            if session.scalar(select(WritingCommitEvent.id).where(WritingCommitEvent.project_id == project_id).limit(1)):
                fail("已有基于旧语料的正文，不能切换参考版本", 409)
            current.corpus_id, current.corpus_version, current.target_report_type = body.corpus_id, body.corpus_version, body.target_report_type
            project.version += 1
        elif current is None:
            session.add(WritingReference(project_id=project_id, corpus_id=body.corpus_id,
                                         corpus_version=body.corpus_version, target_report_type=body.target_report_type))
            project.version += 1
        return {"selected": True, "project_id": project_id,
                "source_project_id": BUILTIN_PROJECT_ID, "corpus_id": archive.id,
                "corpus_version": archive.version, "target_report_type": body.target_report_type,
                "compatible": True, "project_version": project.version}


@app.get("/api/projects/{project_id}/writing/sections")
def writing_section_list(project_id: str):
    with SessionLocal() as session:
        project = get_project(session, project_id)
        require_writable_project(project)
        try:
            _, archive = accepted_reference(session, project_id)
        except ValueError as exc:
            fail(str(exc), 409)
        return {"project_id": project_id, "corpus_id": archive.id, "corpus_version": archive.version,
                "sections": writing_sections(session, archive.id)}


@app.get("/api/projects/{project_id}/writing/packages/{section_id}")
def writing_package_get(project_id: str, section_id: str):
    with SessionLocal() as session:
        project = get_project(session, project_id)
        require_writable_project(project)
        try:
            result = writing_package(session, project_id, section_id)
        except ValueError as exc:
            fail(str(exc), 404 if str(exc) == "章节不存在" else 409)
        return {**result, "project_version": project.version}


@app.get("/api/projects/{project_id}/writing/sources/{category_number}/{record_id}")
def writing_source_get(project_id: str, category_number: int, record_id: str):
    with SessionLocal() as session:
        project = get_project(session, project_id)
        require_writable_project(project)
        try:
            _, archive = accepted_reference(session, project_id)
        except ValueError as exc:
            fail(str(exc), 409)
        record = session.get(CorpusRecord, (archive.id, category_number, record_id))
        if record is None:
            record = session.scalar(select(CorpusRecord).where(
                CorpusRecord.corpus_id == archive.id, CorpusRecord.category_number == category_number,
                CorpusRecord.semantic_id == record_id).order_by(CorpusRecord.ordinal))
        if record is None and category_number == 19:
            # Excerpt records have no semantic_id in this frozen corpus. Their
            # stable EX-* identifier lives in payload.link.id instead.
            record = next((row for row in session.scalars(select(CorpusRecord).where(
                CorpusRecord.corpus_id == archive.id, CorpusRecord.category_number == 19
            ).order_by(CorpusRecord.ordinal))
                           if isinstance(row.payload, dict)
                           and isinstance(row.payload.get("link"), dict)
                           and row.payload["link"].get("id") == record_id), None)
        if record is None:
            fail("语料记录不存在", 404)
        reference = {"corpus_id": archive.id, "corpus_version": archive.version,
                     "category_id": f"CAT-{category_number:02d}", "artifact_id": record.artifact_id,
                     "record_id": record.record_id, "semantic_id": record.semantic_id}
        return {"source_ref": reference, "payload": record.payload, "location": record.location,
                "summary": f"历史语料 {record.semantic_id or record.record_id}；仅供查看，不证明当前项目事实"}


@app.get("/api/projects/{project_id}/writing/impact")
def writing_source_impact(project_id: str, record_id: str):
    with SessionLocal() as session:
        project = get_project(session, project_id)
        require_writable_project(project)
        try:
            _, archive = accepted_reference(session, project_id)
        except ValueError as exc:
            fail(str(exc), 409)
        if not session.scalar(select(CorpusRecord.record_id).where(
            CorpusRecord.corpus_id == archive.id, CorpusRecord.record_id == record_id).limit(1)):
            fail("语料记录不存在", 404)
        impacts = []
        reports = session.scalars(select(ReportDraft).where(ReportDraft.project_id == project_id)).all()
        for report in reports:
            for position, block in enumerate(report.content, 1):
                if any(ref.get("record_id") == record_id for ref in block.get("source_refs", [])):
                    impacts.append({"report_id": report.id, "report_title": report.title,
                                    "report_version": report.version, "block_id": block.get("id"),
                                    "section_id": block.get("section_id"), "position": position,
                                    "text": plain(block)})
        return {"record_id": record_id, "impacts": impacts}


def _model_audits(session, project_id: str, report_id: str) -> list[dict]:
    rows = session.scalars(select(WritingCommitEvent).where(
        WritingCommitEvent.project_id == project_id, WritingCommitEvent.report_id == report_id,
        WritingCommitEvent.mode == "model").order_by(WritingCommitEvent.report_version)).all()
    return [{"event_id": row.id, "report_version": row.report_version, "section_id": row.section_id,
             "created_at": row.created_at.isoformat(), "model_audit": row.model_audit} for row in rows if row.model_audit]


@app.get("/api/projects/{project_id}/reports/{report_id}/writing/model-audits")
def report_model_audits(project_id: str, report_id: str):
    with SessionLocal() as session:
        _report(session, project_id, report_id)
        return {"audits": _model_audits(session, project_id, report_id)}


@app.post("/api/projects/{project_id}/reports/{report_id}/writing/model-input")
def report_model_input(project_id: str, report_id: str, body: WritingPackDraft):
    if body.mode != "model":
        fail("请选择模型起草", 409)
    with SessionLocal() as session:
        project = get_project(session, project_id)
        require_writable_project(project)
        report = _report(session, project_id, report_id)
        try:
            pack = writing_package(session, project_id, body.section_id)
            facts, rules, bindings = _writing_rows(session, project_id)
            state, _ = simulate(facts, rules)
            prepared, _ = prepare_model_input(session, pack, state, rules, bindings, body.approved_item_ids,
                                             {"project_version": project.version, "report_id": report.id,
                                              "report_version": report.version})
            return prepared
        except (ValueError, RuleError) as exc:
            fail(str(exc), 409)


@app.post("/api/projects/{project_id}/reports/{report_id}/writing/preview")
def report_writing_preview(project_id: str, report_id: str, body: WritingPackDraft):
    if body.mode not in {"guided", "model"}:
        fail("起草方式不受支持")
    with SessionLocal() as session:
        project = get_project(session, project_id)
        require_writable_project(project)
        report = _report(session, project_id, report_id)
        try:
            pack = writing_package(session, project_id, body.section_id)
            facts, rules, bindings = _writing_rows(session, project_id)
            state, _ = simulate(facts, rules)
            proposal = build_candidate(session, pack, state, rules, bindings,
                                       body.approved_item_ids, body.mode,
                                       model_context={"project_version": project.version, "report_id": report.id,
                                                      "report_version": report.version},
                                       expected_input_sha256=body.expected_input_sha256)
            validate_content([*report.content, *proposal["paragraphs"]])
            _validate_writing_refs(session, project_id, proposal["paragraphs"])
        except ModelInputChanged as exc:
            fail(str(exc), 409)
        except (ValueError, RuleError) as exc:
            fail(str(exc), 503 if body.mode == "model" else 409)
        result = {"section_id": body.section_id, "approved_item_ids": body.approved_item_ids,
                  "expected_input_sha256": body.expected_input_sha256,
                  "model_input": proposal.get("model_input"), "input_sha256": proposal.get("input_sha256"),
                  "model_call": proposal.get("model_call", {}), "model_usage": proposal.get("model_usage", []),
                  "mode": body.mode, "paragraphs": proposal["paragraphs"],
                  "issues": proposal["issues"], "used_items": proposal["used_items"],
                  "rejected_items": proposal["rejected_items"],
                  "source_refs": proposal["source_refs"],
                  "base_version": report.version, "project_version": project.version,
                  "corpus_id": pack["corpus_id"], "corpus_version": pack["corpus_version"],
                  "expires_at": int(time.time()) + 600}
        return {**result, "preview_token": _writing_signature(project_id, report_id, result)}


@app.post("/api/projects/{project_id}/reports/{report_id}/writing/commit")
def report_writing_commit(project_id: str, report_id: str, body: WritingPackCommit):
    data = body.model_dump()
    if body.expires_at < time.time() or not hmac.compare_digest(
        body.preview_token, _writing_signature(project_id, report_id, data)
    ):
        fail("写作候选已改变或过期，请重新预览", 409)
    with SessionLocal.begin() as session:
        project = get_project(session, project_id, lock=True)
        require_writable_project(project)
        report = _report(session, project_id, report_id, lock=True)
        if report.version != body.base_version or project.version != body.project_version:
            fail("项目事实或报告已变化，请重新生成候选", 409)
        try:
            pack = writing_package(session, project_id, body.section_id)
        except ValueError as exc:
            fail(str(exc), 409)
        if pack["corpus_id"] != body.corpus_id or pack["corpus_version"] != body.corpus_version:
            fail("参考语料版本已变化", 409)
        selectable = {entry["item_id"] for entry in pack["items"] if entry["decision"] == "selectable"}
        if not set(body.approved_item_ids).issubset(selectable):
            fail("写作包的可复用项已变化，请重新预览", 409)
        expected_refs = [ref for paragraph in body.paragraphs for ref in paragraph.get("source_refs", [])]
        if {ref["record_id"] for ref in expected_refs} != set(body.used_items):
            fail("候选来源与使用记录不一致", 409)
        if any(paragraph.get("section_id") != body.section_id or not paragraph.get("id")
               for paragraph in body.paragraphs):
            fail("候选章节或段落身份不正确", 409)
        if len({paragraph["id"] for paragraph in body.paragraphs}) != len(body.paragraphs):
            fail("候选段落 ID 重复", 409)
        content = [*report.content, *body.paragraphs]
        try:
            validate_content(content)
            _validate_writing_refs(session, project_id, content)
        except ValueError as exc:
            fail(str(exc))
        facts = _current_facts(session, project_id)
        used_facts = {key for paragraph in body.paragraphs for key in paragraph.get("fact_keys", [])}
        if any(key not in facts or facts[key].value_text is None or facts[key].value_status not in {"PROVIDED", "COMPUTED"}
               for key in used_facts):
            fail("候选项目事实已失效，请重新生成", 409)
        bound = dict(report.bound_facts)
        for key in used_facts:
            bound[key] = {"value": facts[key].value_text, "revision": facts[key].revision}
        report.content, report.bound_facts, report.reviewed_hash = content, bound, None
        report.version += 1
        session.add(ReportVersion(report_id=report.id, version=report.version, content=content, bound_facts=bound))
        event = WritingCommitEvent(
            project_id=project_id, report_id=report_id, report_version=report.version,
            section_id=body.section_id, corpus_id=body.corpus_id, corpus_version=body.corpus_version,
            mode=body.mode, block_ids=[paragraph["id"] for paragraph in body.paragraphs],
            source_refs=[{"block_id": paragraph["id"], "section_id": body.section_id,
                          "refs": paragraph.get("source_refs", []),
                          "project_rule_refs": paragraph.get("project_rule_refs", [])}
                         for paragraph in body.paragraphs],
            issues=body.issues, approved_item_ids=body.approved_item_ids,
            model_audit={"schema_version": "writing-model-audit/v1", "model_input": body.model_input,
                         "input_sha256": body.input_sha256, "model_call": body.model_call,
                         "model_usage": body.model_usage} if body.mode == "model" else {},
        )
        session.add(event)
        session.flush()
        return {"report": _report_dict(report, facts, session),
                "event_id": event.id, "used_items": body.used_items,
                "source_refs": event.source_refs, "issues": body.issues, "model_audit": event.model_audit}


@app.get("/api/projects/{project_id}/writing")
def writing_get(project_id: str):
    with SessionLocal() as session:
        project = get_project(session, project_id)
        require_writable_project(project)
        try:
            accepted_reference(session, project_id)
        except ValueError as exc:
            fail(str(exc), 409)
        facts, rules, bindings = _writing_rows(session, project_id)
        try:
            state, _ = simulate(facts, rules)
        except RuleError as exc:
            fail(str(exc), 409)
        sources = source_cases(corpus)
        return {"project_id": project_id, "project_version": project.version, "source_project_id": BUILTIN_PROJECT_ID,
                "source_corpus_id": corpus.manifest["corpus_id"], "slots": list(SLOTS), "bindings": bindings,
                "facts": [fact_dict(fact) for fact in facts], "rule_targets": [rule.target_key for rule in rules], "sources": sources,
                "draft": render_cases(state, rules, bindings, sources)}


@app.post("/api/projects/{project_id}/writing/setup")
def writing_setup(project_id: str):
    """仅给空项目创建验证字段和明确的 min 规则，不导入任何历史值或映射。"""
    with SessionLocal.begin() as session:
        project = get_project(session, project_id, lock=True)
        require_writable_project(project)
        try:
            accepted_reference(session, project_id)
        except ValueError as exc:
            fail(str(exc), 409)
        facts, rules, _ = _writing_rows(session, project_id)
        if facts or rules:
            fail("验证字段只可在空白项目中一次性创建；现有项目请手动建立字段并映射", 409)
        for slot in SLOTS:
            session.add(ProjectFact(project_id=project_id, key=slot["suggested_key"], label=slot["label"],
                                    data_type=slot["data_type"], unit=slot["unit"], caliber="本项目待确认", source=""))
        session.flush()
        target = session.scalar(select(ProjectFact).where(ProjectFact.project_id == project_id, ProjectFact.key == "planned_sales"))
        target.value_status = "UNEVALUABLE"
        session.add(RuleRecord(project_id=project_id, name="首年计划销售量", target_key="planned_sales",
                               expression="min(first_year_demand, qualified_capacity)",
                               deps=["first_year_demand", "qualified_capacity"]))
        project.version += 1
        return {"project_id": project_id, "project_version": project.version, "created_fact_count": len(SLOTS),
                "historical_values_copied": 0, "bindings_created": 0}


@app.put("/api/projects/{project_id}/writing/bindings")
def writing_bindings_update(project_id: str, body: BindingUpdate):
    with SessionLocal.begin() as session:
        project = get_project(session, project_id, lock=True)
        require_writable_project(project)
        try:
            accepted_reference(session, project_id)
        except ValueError as exc:
            fail(str(exc), 409)
        facts, _, current = _writing_rows(session, project_id)
        by_key = {item.key: item for item in facts}
        proposed = {slot.strip(): key.strip() for slot, key in body.bindings.items() if key.strip()}
        if any(slot not in SLOT_BY_ID for slot in proposed):
            fail("映射含未知语料槽位")
        if len(set(proposed.values())) != len(proposed):
            fail("不同槽位不能映射到同一个项目事实")
        for slot_id, key in proposed.items():
            fact = by_key.get(key)
            if fact is None:
                fail(f"项目事实 {key} 不存在")
            slot = SLOT_BY_ID[slot_id]
            expected_type = slot["data_type"]
            allowed_types = ("integer", "decimal") if expected_type in ("integer", "decimal") else ("text", "enum")
            if fact.data_type not in allowed_types or fact.unit != slot["unit"]:
                fail(f"{slot['label']} 的类型或单位与项目事实 {key} 不一致")
        if proposed != current:
            session.execute(delete(WritingBinding).where(WritingBinding.project_id == project_id))
            session.add_all(WritingBinding(project_id=project_id, slot_id=slot, fact_key=key) for slot, key in proposed.items())
            project.version += 1
        return {"project_id": project_id, "project_version": project.version, "bindings": proposed}


@app.post("/api/projects/{project_id}/writing/preview")
def writing_preview(project_id: str, body: FactChange):
    """预览事实变化对两处候选正文的影响；不写数据库。"""
    with SessionLocal() as session:
        project = get_project(session, project_id)
        require_writable_project(project)
        try:
            accepted_reference(session, project_id)
        except ValueError as exc:
            fail(str(exc), 409)
        facts, rules, bindings = _writing_rows(session, project_id)
        try:
            before, _ = simulate(facts, rules)
            after, trace = simulate(facts, rules, body)
        except RuleError as exc:
            fail(str(exc))
        sources = source_cases(corpus)
        previous_draft = render_cases(before, rules, bindings, sources)
        proposed_draft = render_cases(after, rules, bindings, sources)
        affected = []
        for old, new in zip(previous_draft["blocks"], proposed_draft["blocks"]):
            old_issues = [(item["code"], item.get("slot_id")) for item in previous_draft["issues"] if item["chunk_id"] == old["chunk_id"]]
            new_issues = [(item["code"], item.get("slot_id")) for item in proposed_draft["issues"] if item["chunk_id"] == new["chunk_id"]]
            if old["text"] != new["text"] or old["references"] != new["references"] or old_issues != new_issues:
                affected.append({"chunk_id": new["chunk_id"], "before": old["text"], "after": new["text"],
                                 "issues_changed": old_issues != new_issues})
        expires = int(time.time()) + 600
        return {"project_id": project_id, "base_version": project.version, "expires_at": expires,
                "preview_token": f"{expires}.{_signature(project_id, project.version, body, expires)}",
                "changes": differences(facts, after), "trace": trace, "before": previous_draft,
                "after": proposed_draft, "affected": affected}
