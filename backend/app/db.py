"""项目事实的 PostgreSQL 权威存储。"""

from __future__ import annotations

import os
from datetime import datetime, timezone
from uuid import uuid4

from sqlalchemy import DateTime, ForeignKey, Integer, JSON, String, Text, UniqueConstraint, create_engine, text
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship, sessionmaker


DATABASE_URL = os.environ.get(
    "DATABASE_URL",
    "postgresql+psycopg://report:local_development_only@127.0.0.1:55432/report_platform",
)
engine = create_engine(DATABASE_URL, pool_pre_ping=True)
SessionLocal = sessionmaker(bind=engine, expire_on_commit=False)


def utcnow() -> datetime:
    """返回带时区的当前时间。

    Returns:
        UTC 时间。
    """
    return datetime.now(timezone.utc)


class Base(DeclarativeBase):
    """SQLAlchemy 声明基类。"""


class Project(Base):
    """项目及其单调递增的事实水位。"""

    __tablename__ = "projects"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    name: Mapped[str] = mapped_column(String(160), nullable=False)
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)
    facts: Mapped[list[ProjectFact]] = relationship(back_populates="project", cascade="all, delete-orphan")
    rules: Mapped[list[RuleRecord]] = relationship(back_populates="project", cascade="all, delete-orphan")
    corpus: Mapped[ProjectCorpus | None] = relationship(back_populates="project", uselist=False, cascade="all, delete-orphan")
    writing_bindings: Mapped[list[WritingBinding]] = relationship(back_populates="project", cascade="all, delete-orphan")
    documents: Mapped[list[SourceDocument]] = relationship(back_populates="project", cascade="all, delete-orphan")
    reports: Mapped[list[ReportDraft]] = relationship(back_populates="project", cascade="all, delete-orphan")


class SourceDocument(Base):
    """Project-owned uploaded original and its locatable text segments."""

    __tablename__ = "source_documents"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), index=True)
    filename: Mapped[str] = mapped_column(String(240), nullable=False)
    file_kind: Mapped[str] = mapped_column(String(12), nullable=False)
    sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    storage_name: Mapped[str] = mapped_column(String(80), nullable=False)
    pages: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    segments: Mapped[list[dict]] = mapped_column(JSON, nullable=False, default=list)
    status: Mapped[str] = mapped_column(String(24), nullable=False, default="READY")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    project: Mapped[Project] = relationship(back_populates="documents")
    candidates: Mapped[list[ExtractionCandidate]] = relationship(back_populates="document", cascade="all, delete-orphan")


class ExtractionCandidate(Base):
    """Untrusted model suggestion, isolated from authoritative project facts."""

    __tablename__ = "extraction_candidates"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    document_id: Mapped[str] = mapped_column(ForeignKey("source_documents.id", ondelete="CASCADE"), index=True)
    extraction_run_id: Mapped[str | None] = mapped_column(ForeignKey("extraction_runs.id", ondelete="SET NULL"), nullable=True, index=True)
    extraction_origin: Mapped[str] = mapped_column(String(20), nullable=False, default="UNKNOWN")
    label: Mapped[str] = mapped_column(String(160), nullable=False)
    value_text: Mapped[str] = mapped_column(Text, nullable=False)
    unit: Mapped[str] = mapped_column(String(80), nullable=False, default="")
    data_type: Mapped[str] = mapped_column(String(24), nullable=False, default="text")
    source_ref: Mapped[str] = mapped_column(String(80), nullable=False)
    source_valid: Mapped[bool] = mapped_column(nullable=False, default=False)
    review_status: Mapped[str] = mapped_column(String(24), nullable=False, default="PENDING")
    fact_key: Mapped[str | None] = mapped_column(String(100), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    document: Mapped[SourceDocument] = relationship(back_populates="candidates")


class ExtractionRun(Base):
    """An auditable model call for a located page; it contains no API key or source text."""

    __tablename__ = "extraction_runs"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), index=True)
    document_id: Mapped[str] = mapped_column(ForeignKey("source_documents.id", ondelete="CASCADE"), index=True)
    page: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False)
    model_call: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    input_refs: Mapped[list[str]] = mapped_column(JSON, nullable=False, default=list)
    input_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    returned_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    accepted_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    error: Mapped[str] = mapped_column(String(240), nullable=False, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class ReportDraft(Base):
    """Versioned Plate AST and user-acknowledged source fact snapshot."""

    __tablename__ = "report_drafts"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), index=True)
    title: Mapped[str] = mapped_column(String(160), nullable=False)
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    content: Mapped[list[dict]] = mapped_column(JSON, nullable=False, default=list)
    bound_facts: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    reviewed_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    analysis_run_id: Mapped[str | None] = mapped_column(String(36), nullable=True, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)
    project: Mapped[Project] = relationship(back_populates="reports")
    versions: Mapped[list[ReportVersion]] = relationship(back_populates="report", cascade="all, delete-orphan")


class ReportVersion(Base):
    """Immutable saved report content; review status is updated for that version."""

    __tablename__ = "report_versions"
    report_id: Mapped[str] = mapped_column(ForeignKey("report_drafts.id", ondelete="CASCADE"), primary_key=True)
    version: Mapped[int] = mapped_column(Integer, primary_key=True)
    content: Mapped[list[dict]] = mapped_column(JSON, nullable=False)
    bound_facts: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    reviewed_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    analysis_run_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    report: Mapped[ReportDraft] = relationship(back_populates="versions")


class ProjectCorpus(Base):
    """把随应用交付的只读语料明确绑定到一个项目。"""

    __tablename__ = "project_corpora"
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), primary_key=True)
    corpus_id: Mapped[str] = mapped_column(String(160), nullable=False, unique=True)
    project: Mapped[Project] = relationship(back_populates="corpus")


class WritingBinding(Base):
    """经项目操作者确认的历史槽位到项目事实 key 的映射；不存历史值。"""

    __tablename__ = "writing_bindings"
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), primary_key=True)
    slot_id: Mapped[str] = mapped_column(String(80), primary_key=True)
    fact_key: Mapped[str] = mapped_column(String(100), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)
    project: Mapped[Project] = relationship(back_populates="writing_bindings")


class WritingReference(Base):
    """Explicit, version-pinned permission to consult one immutable corpus."""

    __tablename__ = "writing_references"
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), primary_key=True)
    corpus_id: Mapped[str] = mapped_column(String(160), nullable=False)
    corpus_version: Mapped[str] = mapped_column(String(60), nullable=False)
    target_report_type: Mapped[str] = mapped_column(String(60), nullable=False)
    accepted_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class WritingCommitEvent(Base):
    """Auditable provenance for a confirmed writing candidate, independent of Plate text."""

    __tablename__ = "writing_commit_events"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), nullable=False, index=True)
    report_id: Mapped[str] = mapped_column(ForeignKey("report_drafts.id", ondelete="CASCADE"), nullable=False, index=True)
    report_version: Mapped[int] = mapped_column(Integer, nullable=False)
    section_id: Mapped[str] = mapped_column(String(80), nullable=False)
    corpus_id: Mapped[str] = mapped_column(String(160), nullable=False)
    corpus_version: Mapped[str] = mapped_column(String(60), nullable=False)
    mode: Mapped[str] = mapped_column(String(20), nullable=False)
    block_ids: Mapped[list[str]] = mapped_column(JSON, nullable=False)
    source_refs: Mapped[list[dict]] = mapped_column(JSON, nullable=False)
    issues: Mapped[list[dict]] = mapped_column(JSON, nullable=False)
    approved_item_ids: Mapped[list[str]] = mapped_column(JSON, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    model_audit: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)


class FactEvidenceBinding(Base):
    """A reviewed, exact-value link from one project fact revision to its own original."""

    __tablename__ = "fact_evidence_bindings"
    __table_args__ = (UniqueConstraint("project_id", "fact_key", "fact_revision", name="uq_fact_evidence_revision"),)
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), nullable=False, index=True)
    fact_key: Mapped[str] = mapped_column(String(100), nullable=False)
    fact_revision: Mapped[int] = mapped_column(Integer, nullable=False)
    document_id: Mapped[str] = mapped_column(ForeignKey("source_documents.id", ondelete="CASCADE"), nullable=False)
    source_refs: Mapped[list[str]] = mapped_column(JSON, nullable=False)
    value_text: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class ProjectFact(Base):
    """本项目事实；历史语料不写入此表。"""

    __tablename__ = "project_facts"
    __table_args__ = (UniqueConstraint("project_id", "key", name="uq_project_fact_key"),)
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), index=True)
    key: Mapped[str] = mapped_column(String(100), nullable=False)
    label: Mapped[str] = mapped_column(String(160), nullable=False)
    data_type: Mapped[str] = mapped_column(String(24), nullable=False)
    value_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    value_status: Mapped[str] = mapped_column(String(24), nullable=False, default="UNDEFINED")
    unit: Mapped[str] = mapped_column(String(80), nullable=False, default="")
    caliber: Mapped[str] = mapped_column(Text, nullable=False, default="")
    as_of: Mapped[str] = mapped_column(String(40), nullable=False, default="")
    source: Mapped[str] = mapped_column(Text, nullable=False, default="")
    revision: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)
    project: Mapped[Project] = relationship(back_populates="facts")


class RuleRecord(Base):
    """项目自身的确定性规则。"""

    __tablename__ = "rules"
    __table_args__ = (UniqueConstraint("project_id", "target_key", name="uq_project_rule_target"),)
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), index=True)
    name: Mapped[str] = mapped_column(String(160), nullable=False)
    target_key: Mapped[str] = mapped_column(String(100), nullable=False)
    expression: Mapped[str] = mapped_column(Text, nullable=False)
    deps: Mapped[list[str]] = mapped_column(JSON, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    project: Mapped[Project] = relationship(back_populates="rules")


class FactRevision(Base):
    """事实值变更的不可变记录。"""

    __tablename__ = "fact_revisions"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), index=True)
    fact_key: Mapped[str] = mapped_column(String(100), nullable=False, index=True)
    project_version: Mapped[int] = mapped_column(Integer, nullable=False)
    before: Mapped[dict] = mapped_column(JSON, nullable=False)
    after: Mapped[dict] = mapped_column(JSON, nullable=False)
    reason: Mapped[str] = mapped_column(String(240), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class AnalysisScenario(Base):
    """A project-owned, editable input set. Corpus values are copied only by explicit action."""

    __tablename__ = "analysis_scenarios"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), nullable=False, index=True)
    name: Mapped[str] = mapped_column(String(160), nullable=False)
    revision: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    blueprint_version: Mapped[str] = mapped_column(String(80), nullable=False)
    corpus_id: Mapped[str | None] = mapped_column(String(160), nullable=True)
    corpus_version: Mapped[str | None] = mapped_column(String(60), nullable=True)
    definitions: Mapped[list[dict]] = mapped_column(JSON, nullable=False)
    rules: Mapped[list[dict]] = mapped_column(JSON, nullable=False)
    inputs: Mapped[dict] = mapped_column(JSON, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)


class AnalysisRun(Base):
    """Immutable result and provenance for one scenario revision."""

    __tablename__ = "analysis_runs"
    __table_args__ = (UniqueConstraint("scenario_id", "request_key", name="uq_analysis_run_request"),)
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), nullable=False, index=True)
    scenario_id: Mapped[str] = mapped_column(ForeignKey("analysis_scenarios.id", ondelete="CASCADE"), nullable=False, index=True)
    scenario_revision: Mapped[int] = mapped_column(Integer, nullable=False)
    request_key: Mapped[str] = mapped_column(String(80), nullable=False)
    input_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    snapshot: Mapped[dict] = mapped_column(JSON, nullable=False)
    status: Mapped[str] = mapped_column(String(24), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class AnalysisWritingEvent(Base):
    """Accepted and declined analysis writing actions, separate from report versions."""

    __tablename__ = "analysis_writing_events"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), nullable=False, index=True)
    report_id: Mapped[str] = mapped_column(ForeignKey("report_drafts.id", ondelete="CASCADE"), nullable=False, index=True)
    run_id: Mapped[str] = mapped_column(ForeignKey("analysis_runs.id", ondelete="CASCADE"), nullable=False)
    report_version: Mapped[int] = mapped_column(Integer, nullable=False)
    action: Mapped[str] = mapped_column(String(30), nullable=False)
    section_id: Mapped[str] = mapped_column(String(80), nullable=False, default="")
    payload: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


def init_db() -> None:
    """为首版新数据库创建数据表。

    Raises:
        SQLAlchemyError: 数据库不可用或建表失败。
    """
    Base.metadata.create_all(engine)
    # Existing local installations predate candidate-level provenance. Old candidates
    # remain explicitly unattributed; new candidates always link to their run.
    with engine.begin() as connection:
        connection.execute(text("ALTER TABLE report_drafts ADD COLUMN IF NOT EXISTS analysis_run_id varchar(36)"))
        connection.execute(text("ALTER TABLE report_versions ADD COLUMN IF NOT EXISTS analysis_run_id varchar(36)"))
        connection.execute(text("CREATE INDEX IF NOT EXISTS ix_report_drafts_analysis_run_id ON report_drafts (analysis_run_id)"))
        connection.execute(text("ALTER TABLE writing_commit_events ADD COLUMN IF NOT EXISTS model_audit JSON NOT NULL DEFAULT '{}'::json"))
        connection.execute(text("ALTER TABLE writing_references ADD COLUMN IF NOT EXISTS target_report_type varchar(60) NOT NULL DEFAULT 'feasibility'"))
        connection.execute(text("ALTER TABLE extraction_candidates ADD COLUMN IF NOT EXISTS extraction_run_id varchar(36) REFERENCES extraction_runs(id) ON DELETE SET NULL"))
        connection.execute(text("ALTER TABLE extraction_candidates ADD COLUMN IF NOT EXISTS extraction_origin varchar(20) NOT NULL DEFAULT 'UNKNOWN'"))
        connection.execute(text("CREATE INDEX IF NOT EXISTS ix_extraction_candidates_extraction_run_id ON extraction_candidates (extraction_run_id)"))
