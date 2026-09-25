"""An immutable run drives a reviewed scenario report without rewriting corpus data."""

import os
from uuid import uuid4

os.environ["DATABASE_URL"] = "postgresql+psycopg://report:local_development_only@127.0.0.1:55432/report_platform_test"

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.db import AnalysisRun, Base, Project, ProjectFact, ReportDraft, SessionLocal, WritingCommitEvent, engine
from app.main import BUILTIN_PROJECT_ID, app


@pytest.fixture(autouse=True)
def isolated_database():
    assert engine.url.database == "report_platform_test"
    writable = [table for table in Base.metadata.sorted_tables if not table.name.startswith("corpus_")]
    Base.metadata.drop_all(engine, tables=writable)
    Base.metadata.create_all(engine, tables=writable)
    yield
    Base.metadata.drop_all(engine, tables=writable)


@pytest.fixture
def client():
    with TestClient(app) as value:
        yield value


def create_history(client):
    base = f"/api/projects/{BUILTIN_PROJECT_ID}"
    response = client.post(base + "/analysis/scenarios", json={"name": "基准方案", "source": "historical"})
    assert response.status_code == 201, response.text
    return base, response.json()


def update(client, base, scenario, changes):
    response = client.put(base + f"/analysis/scenarios/{scenario['id']}",
                          json={"base_revision": scenario["revision"], "changes": changes})
    assert response.status_code == 200, response.text
    return response.json()


def run(client, base, scenario, request_key=None):
    response = client.post(base + f"/analysis/scenarios/{scenario['id']}/runs", json={
        "scenario_revision": scenario["revision"], "request_key": request_key or str(uuid4())})
    assert response.status_code == 201, response.text
    return response.json()


def test_historical_copy_preview_cancel_and_decimal_chain(client):
    base, scenario = create_history(client)
    assert scenario["inputs"]["N017"]["value"] == "180000"
    before = client.get(base + "/analysis/scenarios").json()
    proposed = client.post(base + f"/analysis/scenarios/{scenario['id']}/preview", json={
        "base_revision": 1, "changes": {"N017": "300000"}})
    assert proposed.status_code == 200, proposed.text
    assert proposed.json()["run"]["results"]["N035"]["value"] == "254016"
    assert client.get(base + "/analysis/scenarios").json() == before
    scenario = update(client, base, scenario, {"N017": "300000"})
    first = run(client, base, scenario, "same-request-0001")
    assert first["snapshot"]["results"]["N034"]["value"] == "254016"
    assert first["snapshot"]["results"]["demand_gap"]["value"] == "45984"
    assert first["snapshot"]["condition"] == "需求高于产能"
    assert run(client, base, scenario, "same-request-0001")["id"] == first["id"]
    scenario = update(client, base, scenario, {"N026": "250"})
    second = run(client, base, scenario)
    assert second["snapshot"]["results"]["N034"]["value"] == "211680"
    assert second["snapshot"]["results"]["N035"]["value"] == "211680"
    assert second["snapshot"]["results"]["demand_gap"]["value"] == "88320"
    assert client.get(base + f"/analysis/runs/{first['id']}").json()["snapshot"]["results"]["N034"]["value"] == "254016"
    assert client.post(base + "/analysis/runs/compare", json=[first["id"], second["id"]]).status_code == 200
    with SessionLocal() as session:
        project = session.get(Project, BUILTIN_PROJECT_ID)
        assert project is not None and len(project.facts) == 0


def test_zero_missing_supplier_clear_and_invalid_units(client):
    base, scenario = create_history(client)
    scenario = update(client, base, scenario, {"N017": "0"})
    zero = run(client, base, scenario)
    assert zero["snapshot"]["results"]["N035"]["value"] == "0"
    scenario = update(client, base, scenario, {"N017": None})
    missing = run(client, base, scenario)
    assert missing["status"] == "UNEVALUABLE" and missing["snapshot"]["results"]["N035"]["value"] is None
    assert client.put(base + f"/analysis/scenarios/{scenario['id']}", json={
        "base_revision": scenario["revision"], "changes": {"N030": "90"}}).status_code == 400
    scenario = update(client, base, scenario, {"supplier": "新供应商甲", "N017": "300000"})
    assert scenario["inputs"]["supplier_quote"]["value"] is None
    revised = run(client, base, scenario)
    assert revised["snapshot"]["results"]["supplier"]["value"] == "新供应商甲"
    assert client.post(base + f"/analysis/scenarios/{scenario['id']}/runs", json={
        "scenario_revision": scenario["revision"]-1, "request_key": "old-version-0001"}).status_code == 409


def test_report_candidate_editor_review_and_export(client):
    base, scenario = create_history(client)
    scenario = update(client, base, scenario, {"N017": "300000"})
    first = run(client, base, scenario)
    created = client.post(base + "/reports", json={"title": "产能分析报告"})
    assert created.status_code == 201, created.text
    report_id = created.json()["id"]
    selected = client.post(base + f"/analysis/reports/{report_id}/select", json={
        "run_id": first["id"], "base_version": 0})
    assert selected.status_code == 200, selected.text
    preview = client.post(base + f"/analysis/reports/{report_id}/draft/preview", json={
        "run_id": first["id"], "section_id": "S4", "mode": "computed"})
    assert preview.status_code == 200, preview.text
    assert client.get(base + f"/reports/{report_id}").json()["version"] == 1
    payload = {key: value for key, value in preview.json().items() if key != "issues"}
    committed = client.post(base + f"/analysis/reports/{report_id}/draft/commit", json=payload)
    assert committed.status_code == 200, committed.text
    assert client.post(base + f"/analysis/reports/{report_id}/draft/commit", json=payload).status_code == 409
    report = committed.json()["report"]
    assert report["version"] == 2 and report["analysis_run_id"] == first["id"]
    assert any(block.get("type") == "table" for block in report["content"])
    assert not any(issue["severity"] == "block" and issue["code"] != "UNREVIEWED" for issue in report["issues"])
    reviewed = client.post(base + f"/reports/{report_id}/review")
    assert reviewed.status_code == 200, reviewed.text
    export = client.get(base + f"/reports/{report_id}/export?level=scenario")
    assert export.status_code == 200 and export.content.startswith(b"PK"), export.text[:200]
    assert client.get(base + f"/reports/{report_id}/export?level=formal").status_code == 409
    scenario = update(client, base, scenario, {"N026": "250"})
    second = run(client, base, scenario)
    impact = client.get(base + f"/analysis/reports/{report_id}/impact/{second['id']}")
    assert impact.status_code == 200 and any(row["after"] == "211680" for row in impact.json()["impacts"])
    chosen = client.post(base + f"/analysis/reports/{report_id}/select", json={
        "run_id": second["id"], "base_version": report["version"]})
    assert chosen.status_code == 200
    stale = client.get(base + f"/reports/{report_id}").json()
    assert any(issue["code"] == "ANALYSIS_RUN_STALE" for issue in stale["issues"])
    assert client.get(base + f"/reports/{report_id}/export?level=scenario").status_code == 409
    newer = client.post(base + f"/analysis/reports/{report_id}/draft/preview", json={
        "run_id": second["id"], "section_id": "S4", "mode": "computed"})
    assert newer.status_code == 200
    replacement = {key: value for key, value in newer.json().items() if key != "issues"}
    replacement["replace_section"] = True
    assert client.post(base + f"/analysis/reports/{report_id}/draft/commit", json=replacement).status_code == 200


def test_non_feasibility_project_does_not_inherit_history(client):
    created = client.post("/api/projects", json={"name": "独立审计报告"})
    assert created.status_code == 201
    base = f"/api/projects/{created.json()['id']}"
    scenario = client.post(base + "/analysis/scenarios", json={"name": "当前资料", "source": "project"})
    assert scenario.status_code == 201
    assert scenario.json()["definitions"] == [] and scenario.json()["inputs"] == {}
    assert client.post(base + "/analysis/scenarios", json={"name": "历史包", "source": "historical"}).status_code == 409
    with SessionLocal() as session:
        assert session.scalars(select(ProjectFact).where(ProjectFact.project_id == created.json()["id"])).all() == []


def test_builtin_report_can_be_created_before_its_first_scenario(client):
    created = client.post(f"/api/projects/{BUILTIN_PROJECT_ID}/reports", json={
        "title": "先建报告再录入方案"})
    assert created.status_code == 201, created.text
    assert created.json()["analysis_run_id"] is None


def test_old_article_is_blocked_after_selecting_a_new_run(client):
    base, scenario = create_history(client)
    chosen = run(client, base, scenario)
    created = client.post(base + "/reports", json={"title": "历史文章"}).json()
    report_id = created["id"]
    old_content = [{"type": "h1", "id": "heading", "children": [{"text": "历史文章"}]},
                   {"type": "p", "id": "old-paragraph", "section_id": "S4", "origin": "model",
                    "children": [{"text": "历史判断仍待按新方案核对。"}]}]
    preview = client.post(base + f"/reports/{report_id}/preview", json={
        "base_version": 0, "content": old_content}).json()
    saved = client.put(base + f"/reports/{report_id}", json={
        "base_version": 0, "content": old_content,
        "preview_token": preview["preview_token"]})
    assert saved.status_code == 200, saved.text
    with SessionLocal.begin() as session:
        session.add(WritingCommitEvent(project_id=BUILTIN_PROJECT_ID, report_id=report_id,
            report_version=1, section_id="S4", corpus_id=scenario["corpus_id"],
            corpus_version=scenario["corpus_version"], mode="corpus_article",
            block_ids=["old-paragraph"], source_refs=[], issues=[], approved_item_ids=[],
            model_audit={}))
    selected = client.post(base + f"/analysis/reports/{report_id}/select", json={
        "run_id": chosen["id"], "base_version": 1})
    assert selected.status_code == 200, selected.text
    assert any(issue["code"] == "LEGACY_ARTICLE_STALE" for issue in
               client.get(base + f"/reports/{report_id}").json()["issues"])
    assert client.post(base + f"/reports/{report_id}/review").status_code == 400


def test_claim_and_evidence_links_are_in_run_bound_candidate(client):
    base, scenario = create_history(client)
    chosen = run(client, base, scenario)
    created = client.post(base + "/reports", json={"title": "需求约束"}).json()
    report_id = created["id"]
    assert client.post(base + f"/analysis/reports/{report_id}/select", json={
        "run_id": chosen["id"], "base_version": 0}).status_code == 200
    preview = client.post(base + f"/analysis/reports/{report_id}/draft/preview", json={
        "run_id": chosen["id"], "section_id": "S4", "mode": "computed"})
    assert preview.status_code == 200, preview.text
    refs = {ref["semantic_id"] for block in preview.json()["content"]
            for ref in block.get("source_refs", [])}
    assert {"C0052", "C0027", "L002", "EV-01"} <= refs
    payload = {key: value for key, value in preview.json().items() if key != "issues"}
    committed = client.post(base + f"/analysis/reports/{report_id}/draft/commit", json=payload)
    assert committed.status_code == 200, committed.text
    for category, record_id, semantic_id in ((15, "15:000001", "L002"), (18, "18:000001", "EV-01")):
        detail = client.get(base + f"/corpus/records/{category}/{record_id}")
        assert detail.status_code == 200, detail.text
        assert detail.json()["source_ref"]["semantic_id"] == semantic_id


def test_rewriting_a_chapter_keeps_manually_edited_blocks(client):
    base, scenario = create_history(client)
    scenario = update(client, base, scenario, {"N017": "300000"})
    first = run(client, base, scenario)
    report = client.post(base + "/reports", json={"title": "人工修改保护"}).json()
    report_id = report["id"]
    selected = client.post(base + f"/analysis/reports/{report_id}/select", json={
        "run_id": first["id"], "base_version": 0})
    assert selected.status_code == 200, selected.text
    candidate = client.post(base + f"/analysis/reports/{report_id}/draft/preview", json={
        "run_id": first["id"], "section_id": "S4", "mode": "computed"}).json()
    accepted = client.post(base + f"/analysis/reports/{report_id}/draft/commit", json={
        **{key: value for key, value in candidate.items() if key not in {"issues", "preserved_blocks"}},
        "replace_section": False})
    assert accepted.status_code == 200, accepted.text
    saved = accepted.json()["report"]
    changed = [dict(block) for block in saved["content"]]
    edited = next(block for block in changed if block.get("section_id") == "S4"
                  and block.get("type") == "p" and block.get("analysis_refs"))
    edited["children"] = [{"text": edited["children"][0]["text"] + " 人工复核意见待补。"}]
    trial = client.post(base + f"/reports/{report_id}/preview", json={
        "base_version": saved["version"], "content": changed})
    assert trial.status_code == 200, trial.text
    manual = client.put(base + f"/reports/{report_id}", json={
        "base_version": saved["version"], "content": changed,
        "preview_token": trial.json()["preview_token"]})
    assert manual.status_code == 200, manual.text
    manual_version = manual.json()["report"]["version"]
    scenario = update(client, base, scenario, {"N026": "250"})
    second = run(client, base, scenario)
    assert client.post(base + f"/analysis/reports/{report_id}/select", json={
        "run_id": second["id"], "base_version": manual_version}).status_code == 200
    newer = client.post(base + f"/analysis/reports/{report_id}/draft/preview", json={
        "run_id": second["id"], "section_id": "S4", "mode": "computed"})
    assert newer.status_code == 200, newer.text
    assert [row["id"] for row in newer.json()["preserved_blocks"]] == [edited["id"]]
    replacement = client.post(base + f"/analysis/reports/{report_id}/draft/commit", json={
        **{key: value for key, value in newer.json().items() if key not in {"issues", "preserved_blocks"}},
        "replace_section": True})
    assert replacement.status_code == 200, replacement.text
    updated = replacement.json()["report"]
    assert next(block for block in updated["content"] if block.get("id") == edited["id"])["origin"] == "manual"
    assert any("人工复核意见待补" in str(block) for block in updated["content"])
    assert any("211680" in str(block) for block in updated["content"])
    assert any(issue["code"] == "ANALYSIS_RUN_STALE" for issue in updated["issues"])
    assert client.get(base + f"/reports/{report_id}/export?level=scenario").status_code == 409
    again = client.post(base + f"/analysis/reports/{report_id}/draft/preview", json={
        "run_id": second["id"], "section_id": "S4", "mode": "computed"})
    assert [row["id"] for row in again.json()["preserved_blocks"]] == [edited["id"]]
