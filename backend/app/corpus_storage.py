"""Frozen corpus tables. These tables are separate from writable project facts."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Integer, LargeBinary, String, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from .db import Base, utcnow


class CorpusArchive(Base):
    __tablename__ = "corpus_archives"

    id: Mapped[str] = mapped_column(String(160), primary_key=True)
    version: Mapped[str] = mapped_column(String(60), nullable=False)
    manifest: Mapped[dict] = mapped_column(JSONB, nullable=False)
    integrity: Mapped[dict] = mapped_column(JSONB, nullable=False)
    artifact_count: Mapped[int] = mapped_column(Integer, nullable=False)
    category_count: Mapped[int] = mapped_column(Integer, nullable=False)
    imported_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)


class CorpusCategoryRow(Base):
    __tablename__ = "corpus_categories"

    corpus_id: Mapped[str] = mapped_column(ForeignKey("corpus_archives.id", ondelete="CASCADE"), primary_key=True)
    number: Mapped[int] = mapped_column(Integer, primary_key=True)
    payload: Mapped[dict] = mapped_column(JSONB, nullable=False)
    context: Mapped[dict | None] = mapped_column(JSONB(none_as_null=True), nullable=True)
    record_count: Mapped[int] = mapped_column(Integer, nullable=False)


class CorpusArtifact(Base):
    __tablename__ = "corpus_artifacts"
    __table_args__ = (UniqueConstraint("corpus_id", "artifact_id", name="uq_corpus_artifact_id"),)

    corpus_id: Mapped[str] = mapped_column(ForeignKey("corpus_archives.id", ondelete="CASCADE"), primary_key=True)
    path: Mapped[str] = mapped_column(String(320), primary_key=True)
    artifact_id: Mapped[str] = mapped_column(String(28), nullable=False)
    category_number: Mapped[int | None] = mapped_column(Integer, nullable=True, index=True)
    sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    byte_size: Mapped[int] = mapped_column(Integer, nullable=False)
    media_type: Mapped[str] = mapped_column(String(100), nullable=False)
    purpose: Mapped[str] = mapped_column(Text, nullable=False, default="")
    writing_use: Mapped[str] = mapped_column(Text, nullable=False, default="")
    boundary: Mapped[str] = mapped_column(Text, nullable=False, default="")
    purpose_source: Mapped[str] = mapped_column(Text, nullable=False, default="")
    content: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    parsed: Mapped[dict | list | None] = mapped_column(JSONB(none_as_null=True), nullable=True)


class CorpusRecord(Base):
    __tablename__ = "corpus_records"

    corpus_id: Mapped[str] = mapped_column(ForeignKey("corpus_archives.id", ondelete="CASCADE"), primary_key=True)
    category_number: Mapped[int] = mapped_column(Integer, primary_key=True)
    record_id: Mapped[str] = mapped_column(String(180), primary_key=True)
    ordinal: Mapped[int] = mapped_column(Integer, nullable=False)
    semantic_id: Mapped[str | None] = mapped_column(String(160), nullable=True, index=True)
    artifact_path: Mapped[str] = mapped_column(String(320), nullable=False)
    artifact_id: Mapped[str] = mapped_column(String(28), nullable=False, index=True)
    location: Mapped[dict | list | None] = mapped_column(JSONB(none_as_null=True), nullable=True)
    payload: Mapped[dict | list | str | int | float | None] = mapped_column(JSONB, nullable=False)


class CorpusGraphNode(Base):
    __tablename__ = "corpus_graph_nodes"

    corpus_id: Mapped[str] = mapped_column(ForeignKey("corpus_archives.id", ondelete="CASCADE"), primary_key=True)
    id: Mapped[str] = mapped_column(String(160), primary_key=True)
    label: Mapped[str] = mapped_column(Text, nullable=False)
    kind: Mapped[str] = mapped_column(String(40), nullable=False, index=True)
    source_path: Mapped[str] = mapped_column(String(320), nullable=False)


class CorpusGraphEdge(Base):
    __tablename__ = "corpus_graph_edges"

    corpus_id: Mapped[str] = mapped_column(ForeignKey("corpus_archives.id", ondelete="CASCADE"), primary_key=True)
    id: Mapped[str] = mapped_column(String(240), primary_key=True)
    source: Mapped[str] = mapped_column(String(160), nullable=False, index=True)
    target: Mapped[str] = mapped_column(String(160), nullable=False, index=True)
    type: Mapped[str] = mapped_column(String(100), nullable=False, index=True)
    origin: Mapped[str] = mapped_column(String(320), nullable=False)
    derivation: Mapped[str] = mapped_column(String(16), nullable=False)


class CorpusSourceAsset(Base):
    __tablename__ = "corpus_source_assets"

    corpus_id: Mapped[str] = mapped_column(ForeignKey("corpus_archives.id", ondelete="CASCADE"), primary_key=True)
    name: Mapped[str] = mapped_column(String(240), primary_key=True)
    sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    content: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    purpose: Mapped[str] = mapped_column(Text, nullable=False)
