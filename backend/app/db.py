"""项目事实的 PostgreSQL 权威存储。"""

from __future__ import annotations

import os
from datetime import datetime, timezone
from uuid import uuid4

from sqlalchemy import DateTime, ForeignKey, Integer, JSON, LargeBinary, String, Text, UniqueConstraint, create_engine, select, text
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
    parse_revision: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    status: Mapped[str] = mapped_column(String(24), nullable=False, default="READY")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    project: Mapped[Project] = relationship(back_populates="documents")
    candidates: Mapped[list[ExtractionCandidate]] = relationship(back_populates="document", cascade="all, delete-orphan")


class SourceParseRevision(Base):
    """Immutable parser output for one unchanged original byte stream."""

    __tablename__ = "source_parse_revisions"
    __table_args__ = (UniqueConstraint("document_id", "revision", name="uq_source_parse_revision"),)
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    document_id: Mapped[str] = mapped_column(ForeignKey("source_documents.id", ondelete="CASCADE"), nullable=False, index=True)
    document_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    revision: Mapped[int] = mapped_column(Integer, nullable=False)
    segments: Mapped[list[dict]] = mapped_column(JSON, nullable=False)
    status: Mapped[str] = mapped_column(String(24), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


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
    report_type: Mapped[str] = mapped_column(String(60), nullable=False, default="custom")
    template_version: Mapped[str] = mapped_column(String(40), nullable=False, default="legacy")
    template_snapshot: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
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


class ReportExport(Base):
    """Immutable, downloadable delivery bytes for one checked report watermark."""

    __tablename__ = "report_exports"
    __table_args__ = (UniqueConstraint("report_id", "report_version", "level", "watermark_sha256",
                                       name="uq_report_export_watermark"),)
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), nullable=False, index=True)
    report_id: Mapped[str] = mapped_column(ForeignKey("report_drafts.id", ondelete="CASCADE"), nullable=False, index=True)
    report_version: Mapped[int] = mapped_column(Integer, nullable=False)
    level: Mapped[str] = mapped_column(String(12), nullable=False)
    analysis_run_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    watermark_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    archive_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    filename: Mapped[str] = mapped_column(String(180), nullable=False)
    archive: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


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


class ProjectEvidence(Base):
    """Immutable, project-owned excerpt at a particular original and parse revision."""

    __tablename__ = "project_evidence"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), nullable=False, index=True)
    document_id: Mapped[str] = mapped_column(ForeignKey("source_documents.id", ondelete="RESTRICT"), nullable=False, index=True)
    document_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    parse_revision_id: Mapped[str | None] = mapped_column(ForeignKey("source_parse_revisions.id", ondelete="RESTRICT"), nullable=True)
    source_refs: Mapped[list[str]] = mapped_column(JSON, nullable=False)
    excerpt: Mapped[str] = mapped_column(Text, nullable=False)
    statement: Mapped[str] = mapped_column(Text, nullable=False)
    label: Mapped[str] = mapped_column(String(160), nullable=False)
    subject: Mapped[str] = mapped_column(String(160), nullable=False, default="")
    asserted_at: Mapped[str] = mapped_column(String(40), nullable=False, default="")
    source_type: Mapped[str] = mapped_column(String(20), nullable=False, default="original")
    fact_key: Mapped[str | None] = mapped_column(String(100), nullable=True, index=True)
    fact_revision: Mapped[int | None] = mapped_column(Integer, nullable=True)
    supersedes_id: Mapped[str | None] = mapped_column(ForeignKey("project_evidence.id", ondelete="RESTRICT"), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class ProjectIssue(Base):
    """A project-owned claim, unresolved contradiction, or scoped information gap."""

    __tablename__ = "project_issues"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), nullable=False, index=True)
    kind: Mapped[str] = mapped_column(String(20), nullable=False)
    title: Mapped[str] = mapped_column(String(160), nullable=False)
    statement: Mapped[str] = mapped_column(Text, nullable=False)
    section_ids: Mapped[list[str]] = mapped_column(JSON, nullable=False, default=list)
    support_evidence_ids: Mapped[list[str]] = mapped_column(JSON, nullable=False, default=list)
    opposing_evidence_ids: Mapped[list[str]] = mapped_column(JSON, nullable=False, default=list)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="OPEN")
    decision: Mapped[str] = mapped_column(Text, nullable=False, default="")
    resolution_evidence_id: Mapped[str | None] = mapped_column(ForeignKey("project_evidence.id", ondelete="RESTRICT"), nullable=True)
    revision: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)


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
    revision: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    project: Mapped[Project] = relationship(back_populates="rules")


class RuleRevision(Base):
    """Immutable expression used by a project rule at one revision."""

    __tablename__ = "rule_revisions"
    rule_id: Mapped[str] = mapped_column(ForeignKey("rules.id", ondelete="CASCADE"), primary_key=True)
    revision: Mapped[int] = mapped_column(Integer, primary_key=True)
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), nullable=False, index=True)
    name: Mapped[str] = mapped_column(String(160), nullable=False)
    target_key: Mapped[str] = mapped_column(String(100), nullable=False)
    expression: Mapped[str] = mapped_column(Text, nullable=False)
    deps: Mapped[list[str]] = mapped_column(JSON, nullable=False)
    project_version: Mapped[int | None] = mapped_column(Integer, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


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


class ReportFactProposal(Base):
    """A report paragraph's proposed fact change; never changes the fact by itself."""

    __tablename__ = "report_fact_proposals"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), nullable=False, index=True)
    report_id: Mapped[str] = mapped_column(ForeignKey("report_drafts.id", ondelete="CASCADE"), nullable=False, index=True)
    report_version: Mapped[int] = mapped_column(Integer, nullable=False)
    block_id: Mapped[str] = mapped_column(String(100), nullable=False)
    block_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    fact_key: Mapped[str] = mapped_column(String(100), nullable=False)
    fact_revision: Mapped[int] = mapped_column(Integer, nullable=False)
    project_version: Mapped[int] = mapped_column(Integer, nullable=False)
    proposed_value: Mapped[str] = mapped_column(Text, nullable=False)
    proposed_source: Mapped[str] = mapped_column(Text, nullable=False, default="")
    reason: Mapped[str] = mapped_column(String(240), nullable=False)
    status: Mapped[str] = mapped_column(String(24), nullable=False, default="PENDING")
    applied_project_version: Mapped[int | None] = mapped_column(Integer, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


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


class AnalysisConfig(Base):
    """Project-owned writing and calculation contract; published rows are immutable."""

    __tablename__ = "analysis_configs"
    __table_args__ = (UniqueConstraint("project_id", "version", name="uq_analysis_config_version"),)
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), nullable=False, index=True)
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    revision: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="DRAFT")
    name: Mapped[str] = mapped_column(String(160), nullable=False)
    definitions: Mapped[list[dict]] = mapped_column(JSON, nullable=False, default=list)
    rules: Mapped[list[dict]] = mapped_column(JSON, nullable=False, default=list)
    sections: Mapped[list[dict]] = mapped_column(JSON, nullable=False, default=list)
    checksum: Mapped[str] = mapped_column(String(64), nullable=False, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


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
    run_id: Mapped[str | None] = mapped_column(ForeignKey("analysis_runs.id", ondelete="CASCADE"), nullable=True)
    report_version: Mapped[int] = mapped_column(Integer, nullable=False)
    action: Mapped[str] = mapped_column(String(30), nullable=False)
    section_id: Mapped[str] = mapped_column(String(80), nullable=False, default="")
    payload: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class AnalysisCandidate(Base):
    """A reviewable, server-persisted chapter candidate separate from report text."""

    __tablename__ = "analysis_candidates"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), nullable=False, index=True)
    report_id: Mapped[str] = mapped_column(ForeignKey("report_drafts.id", ondelete="CASCADE"), nullable=False, index=True)
    section_id: Mapped[str] = mapped_column(String(80), nullable=False)
    mode: Mapped[str] = mapped_column(String(20), nullable=False)
    run_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    config_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    evidence_ids: Mapped[list[str]] = mapped_column(JSON, nullable=False, default=list)
    base_version: Mapped[int] = mapped_column(Integer, nullable=False)
    content: Mapped[list[dict]] = mapped_column(JSON, nullable=False)
    model_audit: Mapped[dict] = mapped_column(JSON, nullable=False)
    issues: Mapped[list[dict]] = mapped_column(JSON, nullable=False, default=list)
    preserved_blocks: Mapped[list[dict]] = mapped_column(JSON, nullable=False, default=list)
    digest: Mapped[str] = mapped_column(String(64), nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="PENDING")
    accepted_block_ids: Mapped[list[str]] = mapped_column(JSON, nullable=False, default=list)
    adoption_request_sha256: Mapped[str | None] = mapped_column(String(64), nullable=True)
    accepted_report_version: Mapped[int | None] = mapped_column(Integer, nullable=True)
    expires_at: Mapped[int] = mapped_column(Integer, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class WorkTask(Base):
    """Durable local job record; the worker never edits report text directly."""

    __tablename__ = "work_tasks"
    __table_args__ = (UniqueConstraint("project_id", "kind", "request_key", name="uq_work_task_request"),)
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), nullable=False, index=True)
    report_id: Mapped[str] = mapped_column(ForeignKey("report_drafts.id", ondelete="CASCADE"), nullable=False, index=True)
    kind: Mapped[str] = mapped_column(String(24), nullable=False)
    request_key: Mapped[str] = mapped_column(String(80), nullable=False)
    input_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    payload: Mapped[dict] = mapped_column(JSON, nullable=False)
    result: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="PENDING")
    stage: Mapped[str] = mapped_column(String(24), nullable=False, default="QUEUED")
    attempt: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    generation: Mapped[str | None] = mapped_column(String(36), nullable=True)
    lease_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    cancel_requested: Mapped[bool] = mapped_column(nullable=False, default=False)
    error: Mapped[str | None] = mapped_column(String(500), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)


def init_db() -> None:
    """Install or upgrade the local schema in one PostgreSQL DDL transaction.

    Raises:
        SQLAlchemyError: 数据库不可用或建表失败。
    """
    # PostgreSQL rolls CREATE TABLE/ALTER TABLE back together. Never write a
    # success marker before the last DDL statement has completed.
    # Existing local installations predate candidate-level provenance. Old candidates
    # remain explicitly unattributed; new candidates always link to their run.
    with engine.begin() as connection:
        Base.metadata.create_all(connection)
        connection.execute(text("CREATE TABLE IF NOT EXISTS schema_migrations (version varchar(64) PRIMARY KEY, applied_at timestamptz NOT NULL DEFAULT now())"))
        connection.execute(text("ALTER TABLE report_drafts ADD COLUMN IF NOT EXISTS analysis_run_id varchar(36)"))
        connection.execute(text("ALTER TABLE report_drafts ADD COLUMN IF NOT EXISTS report_type varchar(60) NOT NULL DEFAULT 'custom'"))
        connection.execute(text("ALTER TABLE report_drafts ADD COLUMN IF NOT EXISTS template_version varchar(40) NOT NULL DEFAULT 'legacy'"))
        connection.execute(text("ALTER TABLE report_drafts ADD COLUMN IF NOT EXISTS template_snapshot JSON NOT NULL DEFAULT '{}'::json"))
        connection.execute(text("ALTER TABLE source_documents ADD COLUMN IF NOT EXISTS parse_revision integer NOT NULL DEFAULT 1"))
        connection.execute(text("ALTER TABLE project_evidence ADD COLUMN IF NOT EXISTS parse_revision_id varchar(36)"))
        connection.execute(text("CREATE INDEX IF NOT EXISTS ix_project_evidence_parse_revision_id ON project_evidence (parse_revision_id)"))
        connection.execute(text("DO $$ BEGIN IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'fk_project_evidence_parse_revision') THEN ALTER TABLE project_evidence ADD CONSTRAINT fk_project_evidence_parse_revision FOREIGN KEY (parse_revision_id) REFERENCES source_parse_revisions(id) ON DELETE RESTRICT; END IF; END $$"))
        connection.execute(text("ALTER TABLE report_versions ADD COLUMN IF NOT EXISTS analysis_run_id varchar(36)"))
        connection.execute(text("CREATE INDEX IF NOT EXISTS ix_report_drafts_analysis_run_id ON report_drafts (analysis_run_id)"))
        connection.execute(text("ALTER TABLE writing_commit_events ADD COLUMN IF NOT EXISTS model_audit JSON NOT NULL DEFAULT '{}'::json"))
        connection.execute(text("ALTER TABLE writing_references ADD COLUMN IF NOT EXISTS target_report_type varchar(60) NOT NULL DEFAULT 'feasibility'"))
        connection.execute(text("ALTER TABLE extraction_candidates ADD COLUMN IF NOT EXISTS extraction_run_id varchar(36) REFERENCES extraction_runs(id) ON DELETE SET NULL"))
        connection.execute(text("ALTER TABLE extraction_candidates ADD COLUMN IF NOT EXISTS extraction_origin varchar(20) NOT NULL DEFAULT 'UNKNOWN'"))
        connection.execute(text("CREATE INDEX IF NOT EXISTS ix_extraction_candidates_extraction_run_id ON extraction_candidates (extraction_run_id)"))
        connection.execute(text("ALTER TABLE analysis_writing_events ALTER COLUMN run_id DROP NOT NULL"))
        connection.execute(text("ALTER TABLE analysis_candidates ADD COLUMN IF NOT EXISTS adoption_request_sha256 varchar(64)"))
        connection.execute(text("ALTER TABLE analysis_candidates ADD COLUMN IF NOT EXISTS accepted_report_version integer"))
        connection.execute(text("ALTER TABLE analysis_candidates ADD COLUMN IF NOT EXISTS issues JSON NOT NULL DEFAULT '[]'::json"))
        connection.execute(text("ALTER TABLE analysis_candidates ADD COLUMN IF NOT EXISTS preserved_blocks JSON NOT NULL DEFAULT '[]'::json"))
        connection.execute(text("ALTER TABLE analysis_candidates ADD COLUMN IF NOT EXISTS evidence_ids JSON NOT NULL DEFAULT '[]'::json"))
        connection.execute(text("ALTER TABLE report_fact_proposals ADD COLUMN IF NOT EXISTS proposed_source text NOT NULL DEFAULT ''"))
        connection.execute(text("ALTER TABLE work_tasks ADD COLUMN IF NOT EXISTS stage varchar(24) NOT NULL DEFAULT 'QUEUED'"))
        connection.execute(text("ALTER TABLE rules ADD COLUMN IF NOT EXISTS revision integer NOT NULL DEFAULT 1"))
        connection.execute(text("INSERT INTO rule_revisions(rule_id, revision, project_id, name, target_key, expression, deps, project_version, created_at) SELECT id, 1, project_id, name, target_key, expression, deps, NULL, created_at FROM rules ON CONFLICT (rule_id, revision) DO NOTHING"))
        connection.execute(text("UPDATE work_tasks SET stage = CASE status WHEN 'COMPLETED' THEN 'DONE' WHEN 'FAILED' THEN 'FAILED' WHEN 'CANCELLED' THEN 'CANCELLED' WHEN 'UNCERTAIN' THEN 'RETRY_REQUIRED' WHEN 'RUNNING' THEN CASE kind WHEN 'model_draft' THEN 'CALLING_MODEL' ELSE 'RENDERING' END ELSE stage END WHERE stage = 'QUEUED' AND status <> 'PENDING'"))
        connection.execute(text("INSERT INTO schema_migrations(version) VALUES ('20260927_next28_40') ON CONFLICT DO NOTHING"))
        connection.execute(text("INSERT INTO schema_migrations(version) VALUES ('20260927_next35_proposal') ON CONFLICT DO NOTHING"))
        connection.execute(text("INSERT INTO schema_migrations(version) VALUES ('20260927_fact_entry_ux') ON CONFLICT DO NOTHING"))
        connection.execute(text("INSERT INTO schema_migrations(version) VALUES ('20260927_rule_revision') ON CONFLICT DO NOTHING"))
    # Older parser output can be pinned from the current snapshot. Existing evidence
    # with no parse_revision_id remains legacy; do not claim its historical parse
    # revision has been reconstructed from later OCR output.
    with SessionLocal.begin() as session:
        for document in session.scalars(select(SourceDocument)).all():
            exists = session.scalar(select(SourceParseRevision.id).where(
                SourceParseRevision.document_id == document.id,
                SourceParseRevision.revision == document.parse_revision))
            if exists is None:
                session.add(SourceParseRevision(document_id=document.id,
                    document_sha256=document.sha256, revision=document.parse_revision,
                    segments=document.segments, status=document.status))
