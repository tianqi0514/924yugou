"""Frozen corpus import and database-only runtime checks against PostgreSQL."""

from __future__ import annotations

import hashlib
import os
import shutil
from pathlib import Path

os.environ["DATABASE_URL"] = "postgresql+psycopg://report:local_development_only@127.0.0.1:55432/report_platform_test"

import pytest
from sqlalchemy import func, select

from app import corpus as corpus_module
from app import corpus_import
from app.corpus import CorpusRepository
from app.corpus_storage import CorpusArchive, CorpusArtifact, CorpusGraphEdge, CorpusGraphNode, CorpusRecord
from app.db import Base, SessionLocal, engine


@pytest.fixture
def empty_test_database():
    assert engine.url.database == "report_platform_test", "Refusing to reset a non-test database"
    Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)
    yield
    Base.metadata.drop_all(engine)


def test_import_is_complete_repeatable_and_database_only(empty_test_database, monkeypatch):
    first = corpus_import.import_builtin_corpus()
    assert first == {
        "corpus_id": "cy_tray_20260918", "version": "v2", "categories": 30, "artifacts": 117,
        "records": 3462, "nodes": 728, "raw_edges": 1666, "derived_edges": 566, "imported": True,
    }
    assert corpus_import.import_builtin_corpus()["imported"] is False
    with SessionLocal() as session:
        assert session.scalar(select(func.count()).select_from(CorpusArtifact)) == 117
        assert session.scalar(select(func.count(func.distinct(CorpusArtifact.artifact_id)))) == 117
        artifacts = session.scalars(select(CorpusArtifact)).all()
        assert all(item.purpose and item.boundary and item.purpose_source for item in artifacts)
        assert len({item.path for item in artifacts}) == 117
        assert all(item.writing_use for item in artifacts if item.category_number is not None)
        assert session.scalar(select(func.count()).select_from(CorpusRecord)) == 3462
        assert session.scalar(select(func.count()).select_from(CorpusGraphNode)) == 728
        assert session.scalar(select(func.count()).select_from(CorpusGraphEdge).where(CorpusGraphEdge.derivation == "raw")) == 1666
        assert session.scalar(select(func.count()).select_from(CorpusGraphEdge).where(CorpusGraphEdge.derivation == "derived")) == 566

    inaccessible = Path("/archive-deliberately-unavailable")
    monkeypatch.setattr(corpus_module, "ROOT", inaccessible)
    monkeypatch.setattr(corpus_module, "EXPLAIN", inaccessible)
    monkeypatch.setattr(corpus_import, "ROOT", inaccessible)
    monkeypatch.setattr(corpus_import, "EXPLAIN", inaccessible)
    repository = CorpusRepository()
    assert repository.summary()["integrity"] == {"total_files": 117, "listed_hashes": 116, "valid": True, "errors": []}
    assert repository.summary()["runtime_storage_deployed"] is True
    assert sum(group["count"] for group in repository.summary()["groups"]) == 30
    assert len(repository.categories()) == 30
    category = repository.categories()[4]
    assert category["category_id"] == "CAT-05"
    assert category["artifacts"][0]["artifact_id"].startswith("ART-")
    assert category["artifacts"][0]["purpose"]
    assert category["artifacts"][0]["writing_use"]
    assert category["artifacts"][0]["boundary"]
    assert category["artifacts"][0]["source"]
    all_assets = [artifact for category in repository.categories() for artifact in category["artifacts"]]
    all_assets.extend(repository.summary()["auxiliary_assets"])
    assert len(all_assets) == len({artifact["artifact_id"] for artifact in all_assets}) == 117
    manifest_artifact = repository.artifact(repository.categories()[0]["artifacts"][0]["artifact_id"])
    assert manifest_artifact["path"] == "manifest.yaml"
    assert manifest_artifact["purpose"] == repository.categories()[0]["artifacts"][0]["purpose"]
    for number in range(1, 31):
        category = repository.category(number, size=1)
        assert category["total"] > 0, f"第 {number} 类为空"
        assert len(category["records"]) == 1, f"第 {number} 类无法读取首条记录"
        assert category["records"][0]["_record_id"].startswith(f"{number:02d}:")
        assert category["records"][0]["_artifact_id"].startswith("ART-")
    assert [repository.category(number)["total"] for number in (1, 8, 12, 14, 19, 27)] == [30, 18, 339, 1666, 20, 49]
    assert repository.parsed("chunks/embeddings.parquet")["rows"] == 0
    assert len(repository.graph()[1]) == 2232
    original, media_type, name = repository.raw_bytes("source/original.docx")
    original_by_id = repository.raw_bytes(repository.categories()[4]["artifacts"][0]["artifact_id"])[0]
    assert original_by_id == original
    assert name == "original.docx" and media_type
    assert hashlib.sha256(original).hexdigest() == repository.parsed("manifest.yaml")["source_fingerprint"].removeprefix("sha256:")
    assert repository.raw_bytes("validate_package.py")[0].startswith(b"#!/") or repository.raw_bytes("validate_package.py")[0]


def test_rebuild_refreshes_database_projections_without_changing_source(empty_test_database):
    first = corpus_import.import_builtin_corpus()
    with SessionLocal.begin() as session:
        item = session.scalar(select(CorpusArtifact).where(CorpusArtifact.path == "manifest.yaml"))
        assert item is not None
        original_hash = item.sha256
        item.purpose = ""
    rebuilt = corpus_import.import_builtin_corpus()
    assert rebuilt["imported"] is False and rebuilt["rebuilt"] is True
    assert rebuilt["records"] == first["records"]
    assert corpus_import.import_builtin_corpus()["imported"] is False
    forced = corpus_import.import_builtin_corpus(rebuild=True)
    assert forced["imported"] is False and forced["rebuilt"] is True
    with SessionLocal() as session:
        item = session.scalar(select(CorpusArtifact).where(CorpusArtifact.path == "manifest.yaml"))
        assert item is not None
        assert item.sha256 == original_hash
        assert "语料包的总入口" in item.purpose
        assert session.scalar(select(func.count()).select_from(CorpusArtifact)) == 117
        assert session.scalar(select(func.count()).select_from(CorpusRecord)) == 3462


def test_bad_checksum_never_partially_imports(empty_test_database, tmp_path):
    copied = tmp_path / "corrupt-corpus"
    shutil.copytree(corpus_import.ROOT, copied)
    target = copied / "meta.yaml"
    target.write_bytes(target.read_bytes() + b"\ncorruption: true\n")
    with pytest.raises(ValueError, match="SHA256SUMS"):
        corpus_import.import_builtin_corpus(root=copied)
    with SessionLocal() as session:
        assert session.scalar(select(func.count()).select_from(CorpusArchive)) == 0
        assert session.scalar(select(func.count()).select_from(CorpusArtifact)) == 0
