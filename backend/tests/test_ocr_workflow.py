"""扫描页 OCR 只产生待核对片段，事实仍须人工确认。"""

import os
from pathlib import Path

os.environ["DATABASE_URL"] = "postgresql+psycopg://report:local_development_only@127.0.0.1:55432/report_platform_test"

import pytest
from fastapi.testclient import TestClient

from app.db import Base, engine
from app.main import app


FIXTURES = Path(__file__).resolve().parents[2] / "test-fixtures" / "public-reports"


@pytest.fixture(autouse=True)
def isolated_database():
    assert engine.url.database == "report_platform_test", "Refusing to reset a non-test database"
    writable_tables = [table for table in Base.metadata.sorted_tables if not table.name.startswith("corpus_")]
    Base.metadata.drop_all(engine, tables=writable_tables)
    Base.metadata.create_all(engine, tables=writable_tables)
    yield
    Base.metadata.drop_all(engine, tables=writable_tables)


def test_scanned_page_ocr_is_located_idempotent_and_reviewed(monkeypatch, tmp_path):
    import app.main as main_module

    monkeypatch.setattr(main_module, "STORAGE", tmp_path)
    calls = []

    def fake_ocr(_data: bytes, page: int) -> dict:
        calls.append(page)
        return {"text": "专项审计调查发现涉及 12 家金融机构。", "provider": "mineru",
                "model": "test-ocr", "metadata": {"page": page}}

    monkeypatch.setattr(main_module, "parse_document_page", fake_ocr)
    with TestClient(app) as client:
        project = client.post("/api/projects", json={"name": "OCR 验证项目"}).json()["id"]
        other = client.post("/api/projects", json={"name": "隔离项目"}).json()["id"]
        data = (FIXTURES / "03_taizhou_audit.pdf").read_bytes()
        uploaded = client.post(f"/api/projects/{project}/documents", files={"file": ("audit.pdf", data, "application/pdf")})
        assert uploaded.status_code == 201, uploaded.text
        document_id = uploaded.json()["id"]
        detail_url = f"/api/projects/{project}/documents/{document_id}"
        ocr_url = f"{detail_url}/ocr?page=2"
        assert client.get(f"{detail_url}?page=2").json()["segments"] == []
        assert client.post(f"/api/projects/{other}/documents/{document_id}/ocr?page=2").status_code == 404
        before = client.get(f"/api/projects/{project}/facts").json()["facts"]
        recognized = client.post(ocr_url)
        assert recognized.status_code == 200, recognized.text
        assert recognized.json() == {"ref": "p2-ocr1", "created": True, "chars": 20,
                                     "review_status": "PENDING_REVIEW"}
        assert calls == [2]
        segment = client.get(f"{detail_url}?page=2").json()["segments"][0]
        assert segment["ref"] == "p2-ocr1" and segment["kind"] == "ocr"
        assert "第 2 页" in segment["locator"]
        assert client.get(f"{detail_url}/pages").json()["pages"][1]["extractable"] is True
        assert client.get(f"/api/projects/{project}/facts").json()["facts"] == before
        assert client.post(ocr_url).json()["created"] is False

        monkeypatch.setattr(main_module, "model_candidates", lambda _segments: [
            {"label": "金融机构数量", "value_text": "12", "unit": "家", "data_type": "integer",
             "source_ref": "p2-ocr1", "source_valid": True}])
        extracted = client.post(f"{detail_url}/extract?page=2")
        assert extracted.status_code == 200, extracted.text
        runs = client.get(f"{detail_url}/extraction-runs?page=2").json()
        assert len(runs) == 1 and runs[0]["status"] == "SUCCEEDED"
        assert runs[0]["input_refs"] == ["p2-ocr1"]
        assert runs[0]["returned_count"] == runs[0]["accepted_count"] == 1
        assert client.get(f"/api/projects/{other}/documents/{document_id}/extraction-runs?page=2").status_code == 404
        candidate = client.get(f"{detail_url}?page=2").json()["candidates"][0]
        assert candidate["review_status"] == "PENDING"
        assert candidate["extraction_run_id"] == runs[0]["id"]
        assert candidate["extraction_origin"] == "MODEL"
        assert runs[0]["candidate_ids"] == [candidate["id"]]
        repeated = client.post(f"{detail_url}/extract?page=2")
        assert repeated.status_code == 200 and repeated.json()["created"] == 0
        newer = client.get(f"{detail_url}/extraction-runs?page=2").json()[0]
        assert newer["candidate_ids"] == []
        assert client.get(f"{detail_url}?page=2").json()["candidates"][0]["extraction_run_id"] == runs[0]["id"]
        assert client.get(f"/api/projects/{project}/facts").json()["facts"] == before
        approved = client.post(f"{detail_url}/candidates/{candidate['id']}/approve", json={
            "label": "金融机构数量", "value": "12", "unit": "家", "data_type": "integer",
            "source_ref": "p2-ocr1", "key": "financial_institutions"})
        assert approved.status_code == 200, approved.text
        source = client.get(f"/api/projects/{project}/facts/financial_institutions/source").json()
        assert source["source_ref"] == "p2-ocr1" and source["page"] == 2
        from app.report_pipeline import source_display
        fact = next(item for item in client.get(f"/api/projects/{project}/facts").json()["facts"]
                    if item["key"] == "financial_institutions")
        assert source_display(fact["source"]) == "audit.pdf，第 2 页，OCR 识别片段 1"


def test_ocr_failure_leaves_document_unchanged(monkeypatch, tmp_path):
    import app.main as main_module

    monkeypatch.setattr(main_module, "STORAGE", tmp_path)
    monkeypatch.setattr(main_module, "parse_document_page", lambda _data, _page: (_ for _ in ()).throw(ValueError("OCR 服务不可用")))
    with TestClient(app) as client:
        project = client.post("/api/projects", json={"name": "OCR 失败验证"}).json()["id"]
        data = (FIXTURES / "03_taizhou_audit.pdf").read_bytes()
        document_id = client.post(f"/api/projects/{project}/documents", files={
            "file": ("audit.pdf", data, "application/pdf")}).json()["id"]
        url = f"/api/projects/{project}/documents/{document_id}"
        assert client.post(f"{url}/ocr?page=2").status_code == 503
        assert client.get(f"{url}?page=2").json()["segments"] == []
        assert client.get(f"/api/projects/{project}/facts").json()["facts"] == []


def test_model_failure_records_safe_run_without_candidates(monkeypatch, tmp_path):
    import app.main as main_module

    monkeypatch.setattr(main_module, "STORAGE", tmp_path)
    monkeypatch.setattr(main_module, "model_candidates", lambda _segments: (_ for _ in ()).throw(ValueError("模型服务不可用")))
    with TestClient(app) as client:
        project = client.post("/api/projects", json={"name": "失败抽取审计"}).json()["id"]
        data = (FIXTURES / "04_jinjiang_accident.pdf").read_bytes()
        document_id = client.post(f"/api/projects/{project}/documents", files={
            "file": ("accident.pdf", data, "application/pdf")}).json()["id"]
        url = f"/api/projects/{project}/documents/{document_id}"
        assert client.post(f"{url}/extract?page=3").status_code == 503
        assert client.get(f"{url}?page=3").json()["candidates"] == []
        assert client.get(f"/api/projects/{project}/facts").json()["facts"] == []
        runs = client.get(f"{url}/extraction-runs?page=3").json()
        assert len(runs) == 1 and runs[0]["status"] == "FAILED"
        assert runs[0]["error"] == "模型服务不可用"
        assert runs[0]["candidate_ids"] == []


def test_model_failure_does_not_half_commit_table_segments(monkeypatch, tmp_path):
    import pymupdf
    import app.main as main_module

    monkeypatch.setattr(main_module, "STORAGE", tmp_path)
    monkeypatch.setattr(main_module, "model_candidates", lambda _segments: (_ for _ in ()).throw(ValueError("模型服务不可用")))
    with pymupdf.open(FIXTURES / "01_beijing_feasibility.pdf") as source:
        document = pymupdf.open()
        document.insert_pdf(source, from_page=6, to_page=6)
        data = document.tobytes()
    with TestClient(app) as client:
        project = client.post("/api/projects", json={"name": "表格抽取原子性验证"}).json()["id"]
        document_id = client.post(f"/api/projects/{project}/documents", files={
            "file": ("table.pdf", data, "application/pdf")}).json()["id"]
        url = f"/api/projects/{project}/documents/{document_id}"
        before = client.get(f"{url}?page=1").json()["segments"]
        assert not any(part.get("kind") == "table_row" for part in before)
        assert client.post(f"{url}/extract?page=1").status_code == 503
        after = client.get(f"{url}?page=1").json()
        assert after["segments"] == before
        assert after["candidates"] == []
        assert client.get(f"{url}/extraction-runs?page=1").json()[0]["status"] == "FAILED"
