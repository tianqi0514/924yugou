"""NEXT-35: a paragraph proposal must not bypass fact preview and version checks."""

import os
from uuid import uuid4

os.environ["DATABASE_URL"] = "postgresql+psycopg://report:local_development_only@127.0.0.1:55432/report_platform_test"

import pytest
from fastapi.testclient import TestClient

from app.db import Base, ProjectFact, ReportDraft, ReportFactProposal, ReportVersion, SessionLocal, engine
from app.main import app


@pytest.fixture(autouse=True)
def isolated_database():
    assert engine.url.database == "report_platform_test", "Refusing to reset a non-test database"
    writable = [table for table in Base.metadata.sorted_tables if not table.name.startswith("corpus_")]
    Base.metadata.drop_all(engine, tables=writable)
    Base.metadata.create_all(engine, tables=writable)
    yield
    Base.metadata.drop_all(engine, tables=writable)


@pytest.fixture
def client():
    with TestClient(app) as value:
        yield value


def prepared(client):
    project = client.post("/api/projects", json={"name": "提案 QA"}).json()
    base = f"/api/projects/{project['id']}"
    created = client.post(base + "/facts", json={"key": "demand", "label": "需求量",
        "data_type": "integer", "unit": "套", "source": "原件 A"})
    assert created.status_code == 201, created.text
    request = {"fact_key": "demand", "value": "300000", "source": "原件 A", "reason": "录入"}
    preview = client.post(base + "/changes/preview", json=request).json()
    assert client.post(base + "/changes/commit", json={**request,
        "base_version": preview["base_version"], "preview_token": preview["preview_token"]}).status_code == 200
    block = {"id": "paragraph-1", "type": "p", "children": [{"text": "首年需求 300000 套。"}],
             "fact_keys": ["demand"]}
    report_id = str(uuid4())
    with SessionLocal.begin() as session:
        report = ReportDraft(id=report_id, project_id=project["id"], title="需求报告",
                             version=1, content=[block], bound_facts={"demand": {"value": "300000", "revision": 1}})
        session.add(report)
        session.add(ReportVersion(report_id=report_id, version=1, content=[block],
                                  bound_facts=report.bound_facts))
    return base, report_id


def proposal(client, base, report_id, value="200000"):
    return client.post(base + f"/reports/{report_id}/fact-proposals", json={
        "block_id": "paragraph-1", "fact_key": "demand", "proposed_value": value,
        "source": "", "reason": "正文修订"})


def test_proposal_reject_preview_commit_and_no_double_apply(client):
    base, report_id = prepared(client)
    first = proposal(client, base, report_id)
    assert first.status_code == 201, first.text
    assert proposal(client, base, report_id).status_code == 409
    assert client.get(base + "/facts").json()["facts"][0]["value"] == "300000"
    proposal_url = base + f"/reports/{report_id}/fact-proposals/{first.json()['id']}"
    preview = client.post(proposal_url + "/preview", json={})
    assert preview.status_code == 200, preview.text
    assert preview.json()["changes"][0]["after"]["value"] == "200000"
    assert preview.json()["report_impacts"][0]["position"] == 1
    assert client.get(base + "/facts").json()["facts"][0]["value"] == "300000"
    rejected = client.post(proposal_url + "/reject", json={})
    assert rejected.status_code == 200 and rejected.json()["status"] == "REJECTED"
    assert client.post(proposal_url + "/preview", json={}).status_code == 409
    assert client.get(base + "/facts").json()["facts"][0]["value"] == "300000"

    second = proposal(client, base, report_id).json()
    second_url = base + f"/reports/{report_id}/fact-proposals/{second['id']}"
    valid = client.post(second_url + "/preview", json={}).json()
    request = {"fact_key": "demand", "value": "200000", "source": "", "reason": "正文修订",
               "base_version": valid["base_version"], "preview_token": valid["preview_token"],
               "proposal_id": second["id"]}
    assert client.post(base + "/changes/commit", json={**request, "value": "100000"}).status_code == 409
    assert client.get(base + "/facts").json()["facts"][0]["value"] == "300000"
    applied = client.post(base + "/changes/commit", json=request)
    assert applied.status_code == 200, applied.text
    assert applied.json()["changes"][0]["after"]["value"] == "200000"
    assert client.post(base + "/changes/commit", json=request).status_code == 409
    with SessionLocal() as session:
        record = session.get(ReportFactProposal, second["id"])
        fact = session.query(ProjectFact).filter_by(project_id=record.project_id, key="demand").one()
        assert record.status == "APPLIED" and record.applied_project_version is not None
        assert fact.value_text == "200000" and fact.revision == 2
    report = client.get(base + f"/reports/{report_id}").json()
    assert report["reviewed"] is False
    assert any(row["code"] == "FACT_CHANGED" for row in report["issues"])


def test_proposal_rejects_cross_project_and_changed_report(client):
    base, report_id = prepared(client)
    other = client.post("/api/projects", json={"name": "其他项目"}).json()
    foreign = f"/api/projects/{other['id']}/reports/{report_id}/fact-proposals"
    assert client.get(foreign).status_code == 404
    assert client.post(foreign, json={"block_id": "paragraph-1", "fact_key": "demand",
        "proposed_value": "200000", "reason": "跨项目"}).status_code == 404
    created = proposal(client, base, report_id).json()
    with SessionLocal.begin() as session:
        report = session.get(ReportDraft, report_id)
        report.version += 1
    url = base + f"/reports/{report_id}/fact-proposals/{created['id']}"
    assert client.post(url + "/preview", json={}).status_code == 409
    assert client.get(base + "/facts").json()["facts"][0]["value"] == "300000"


def test_proposal_preserves_valid_zero_and_rejects_missing_value(client):
    base, report_id = prepared(client)
    assert client.post(base + f"/reports/{report_id}/fact-proposals", json={
        "block_id": "paragraph-1", "fact_key": "demand", "proposed_value": "",
        "reason": "清空"}).status_code == 422
    created = proposal(client, base, report_id, "0")
    assert created.status_code == 201, created.text
    row = created.json()
    ticket = client.post(base + f"/reports/{report_id}/fact-proposals/{row['id']}/preview", json={}).json()
    assert ticket["changes"][0]["after"]["value"] == "0"
    committed = client.post(base + "/changes/commit", json={
        "fact_key": "demand", "value": "0", "source": "", "reason": "正文修订",
        "base_version": ticket["base_version"], "preview_token": ticket["preview_token"],
        "proposal_id": row["id"]})
    assert committed.status_code == 200, committed.text
    assert client.get(base + "/facts").json()["facts"][0]["value"] == "0"
