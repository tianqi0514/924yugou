"""An immutable run drives a reviewed scenario report without rewriting corpus data."""

import os
import io
import json
import zipfile
from copy import deepcopy
from types import SimpleNamespace

import pymupdf
from uuid import uuid4

os.environ["DATABASE_URL"] = "postgresql+psycopg://report:local_development_only@127.0.0.1:55432/report_platform_test"

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from docx import Document

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


def test_zero_quote_remains_an_explicit_scenario_value(client):
    base, scenario = create_history(client)
    scenario = update(client, base, scenario, {"supplier": "新供应商甲", "supplier_quote": "0"})
    chosen = run(client, base, scenario)
    report = client.post(base + "/reports", json={"title": "报价边界"}).json()
    report_id = report["id"]
    assert client.post(base + f"/analysis/reports/{report_id}/select", json={
        "run_id": chosen["id"], "base_version": 0}).status_code == 200
    candidate = client.post(base + f"/analysis/reports/{report_id}/draft/preview", json={
        "run_id": chosen["id"], "section_id": "S7.1", "mode": "computed"})
    assert candidate.status_code == 200, candidate.text
    text = " ".join(str(block.get("children", "")) for block in candidate.json()["content"])
    assert "0元/条" in text and "报价待补" not in text


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
    assert {row["position"] for row in impact.json()["impacts"] if row["result_key"] == "N034"} == {3, 4}
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


def _accepted_section(client, base, report_id, run_id, section_id):
    preview = client.post(base + f"/analysis/reports/{report_id}/draft/preview", json={
        "run_id": run_id, "section_id": section_id, "mode": "computed"})
    assert preview.status_code == 200, preview.text
    committed = client.post(base + f"/analysis/reports/{report_id}/draft/commit", json={
        **{key: value for key, value in preview.json().items() if key not in {"issues", "preserved_blocks"}},
        "replace_section": False})
    assert committed.status_code == 200, committed.text
    return committed.json()["report"]


def test_local_refresh_updates_paragraph_then_table_without_touching_other_text(client):
    base, scenario = create_history(client)
    scenario = update(client, base, scenario, {"N017": "300000"})
    first = run(client, base, scenario)
    report = client.post(base + "/reports", json={"title": "局部更新产能"}).json()
    report_id = report["id"]
    assert client.post(base + f"/analysis/reports/{report_id}/select", json={
        "run_id": first["id"], "base_version": 0}).status_code == 200
    report = _accepted_section(client, base, report_id, first["id"], "S4")
    original_version = report["version"]
    original_content = report["content"]
    scenario = update(client, base, scenario, {"N026": "250"})
    second = run(client, base, scenario)
    assert client.post(base + f"/analysis/reports/{report_id}/select", json={
        "run_id": second["id"], "base_version": original_version}).status_code == 200
    path = base + f"/analysis/reports/{report_id}/refresh"
    preview = client.get(path + "/preview")
    assert preview.status_code == 200, preview.text
    actions = preview.json()["actions"]
    assert {item["kind"] for item in actions} == {"numbers", "table"}
    assert client.get(base + f"/reports/{report_id}").json()["content"] == original_content
    number = next(item for item in actions if item["kind"] == "numbers")
    table = next(item for item in actions if item["kind"] == "table")
    assert "254016" in number["before"] and "211680" in number["after"]
    first_change = client.post(path + "/commit", json={
        "run_id": second["id"], "base_version": preview.json()["report_version"],
        "operation_ids": [number["id"]]})
    assert first_change.status_code == 200, first_change.text
    partial = first_change.json()["report"]
    assert any(issue["code"] == "ANALYSIS_RUN_STALE" for issue in partial["issues"])
    assert "211680" in str(partial["content"][number["position"]-1])
    assert "254016" in str(partial["content"][table["position"]-1])
    assert client.post(path + "/commit", json={
        "run_id": second["id"], "base_version": preview.json()["report_version"],
        "operation_ids": [number["id"]]}).status_code == 409
    assert client.get(base + f"/reports/{report_id}/export?level=scenario").status_code == 409
    remaining = client.get(path + "/preview").json()
    assert {item["kind"] for item in remaining["actions"]} == {"table"}
    complete = client.post(path + "/commit", json={
        "run_id": second["id"], "base_version": remaining["report_version"],
        "operation_ids": [remaining["actions"][0]["id"]]})
    assert complete.status_code == 200, complete.text
    final = complete.json()["report"]
    assert not any(issue["code"] == "ANALYSIS_RUN_STALE" for issue in final["issues"])
    assert [block.get("id") for block in final["content"]] == [block.get("id") for block in original_content]
    assert client.post(base + f"/reports/{report_id}/review").status_code == 200
    delivery = client.get(base + f"/reports/{report_id}/export?level=scenario")
    assert delivery.status_code == 200, delivery.text[:200]
    with zipfile.ZipFile(io.BytesIO(delivery.content)) as archive:
        audit = json.loads(archive.read("audit.json"))
        assert second["id"] in json.dumps(audit, ensure_ascii=False)
        with zipfile.ZipFile(io.BytesIO(archive.read("report.docx"))) as docx:
            assert b"211680" in docx.read("word/document.xml")
    old = client.get(base + f"/reports/{report_id}/compare?base={original_version}").json()
    assert "254016" in str(old["base_content"])


def test_manual_sentence_and_table_require_reviewed_local_update(client):
    base, scenario = create_history(client)
    scenario = update(client, base, scenario, {"N017": "300000"})
    first = run(client, base, scenario)
    report = client.post(base + "/reports", json={"title": "人工编辑局部更新"}).json()
    report_id = report["id"]
    assert client.post(base + f"/analysis/reports/{report_id}/select", json={
        "run_id": first["id"], "base_version": 0}).status_code == 200
    report = _accepted_section(client, base, report_id, first["id"], "S4")
    edited = deepcopy(report["content"])
    paragraph = next(block for block in edited if block["type"] == "p" and block.get("analysis_refs"))
    table = next(block for block in edited if block["type"] == "table")
    paragraph["children"][0]["text"] += " 人工补充：交付节奏另行核实。"
    table["children"][0]["children"][0]["children"][0]["children"][0]["text"] += "（人工列名）"
    saved_preview = client.post(base + f"/reports/{report_id}/preview", json={
        "base_version": report["version"], "content": edited}).json()
    saved = client.put(base + f"/reports/{report_id}", json={
        "base_version": report["version"], "content": edited,
        "preview_token": saved_preview["preview_token"]})
    assert saved.status_code == 200, saved.text
    before_change = saved.json()["report"]
    scenario = update(client, base, scenario, {"N026": "250"})
    second = run(client, base, scenario)
    assert client.post(base + f"/analysis/reports/{report_id}/select", json={
        "run_id": second["id"], "base_version": before_change["version"]}).status_code == 200
    path = base + f"/analysis/reports/{report_id}/refresh"
    proposed = client.get(path + "/preview").json()
    manual = [action for action in proposed["actions"] if action["kind"] == "manual_review"]
    assert {action["block_id"] for action in manual} == {paragraph["id"], table["id"]}
    assert all(action["selectable"] and "211680" in action["after"] for action in manual)
    assert "人工补充：交付节奏另行核实" in next(
        action["after"] for action in manual if action["block_id"] == paragraph["id"])
    assert client.get(base + f"/reports/{report_id}/export?level=scenario").status_code == 409
    updated = client.post(path + "/commit", json={
        "run_id": second["id"], "base_version": proposed["report_version"],
        "operation_ids": [action["id"] for action in manual]})
    assert updated.status_code == 200, updated.text
    current = updated.json()["report"]
    assert not any(issue["code"] == "ANALYSIS_RUN_STALE" for issue in current["issues"])
    assert "人工补充：交付节奏另行核实" in str(current["content"])
    assert "人工列名" in str(current["content"])
    assert "254016" not in str(current["content"])
    assert [block.get("id") for block in current["content"]] == [block.get("id") for block in edited]
    assert client.post(base + f"/reports/{report_id}/review").status_code == 200
    delivery = client.get(base + f"/reports/{report_id}/export?level=scenario")
    assert delivery.status_code == 200, delivery.text[:200]
    with zipfile.ZipFile(io.BytesIO(delivery.content)) as archive:
        audit = json.loads(archive.read("audit.json"))
        assert second["id"] == audit["analysis_run_id"]
        assert "manual_review" in json.dumps(audit, ensure_ascii=False)
        with zipfile.ZipFile(io.BytesIO(archive.read("report.docx"))) as word:
            xml = word.read("word/document.xml")
            assert b"211680" in xml and b"254016" not in xml
        with pymupdf.open(stream=archive.read("report.pdf"), filetype="pdf") as pdf:
            pdf_text = "\n".join(page.get_text() for page in pdf)
            assert "211680" in pdf_text and "254016" not in pdf_text
            assert "交付节奏另行核实" in pdf_text and "人工列名" in pdf_text
    old = client.get(base + f"/reports/{report_id}/compare?base={before_change['version']}").json()
    assert "254016" in str(old["base_content"])


def test_manual_update_refuses_ambiguous_numeric_location():
    from app.analysis_refresh import UnsafeUpdate, _manual_update
    block = {"type": "p", "id": "edited", "section_id": "S4", "origin": "manual",
             "children": [{"text": "合格能力254016套，人工另记254016套。"}],
             "analysis_refs": [{"run_id": "old", "result_key": "capacity",
                                "value": "254016", "unit": "套"}]}
    run = SimpleNamespace(id="new", snapshot={"results": {
        "capacity": {"value": "211680", "unit": "套"}}, "condition": None})
    with pytest.raises(UnsafeUpdate, match="位置不唯一"):
        _manual_update(block, run)
    assert block["analysis_refs"][0]["run_id"] == "old"


def test_manual_condition_sentence_preserves_added_text_and_rebinds_dependencies():
    from app.analysis_refresh import _manual_update
    block = {"type": "p", "id": "condition-edited", "section_id": "budget", "origin": "manual",
             "children": [{"text": "本方案环保投资未超过预算上限。人工补充：用途另行核对。"}],
             "analysis_refs": [{"run_id": "old", "result_key": key, "value": value, "unit": "万元"}
                               for key, value in (("environmental_investment", "17"), ("budget_limit", "20"))]}
    condition = {"id": "within_budget", "when_true": "本方案环保投资未超过预算上限。",
                 "when_false": "本方案环保投资超过预算上限。"}
    run = SimpleNamespace(id="new", snapshot={
        "results": {key: {"value": value, "unit": "万元"}
                    for key, value in (("environmental_investment", "17"), ("budget_limit", "15"))},
        "configuration": {"sections": [{"id": "budget", "conditions": [condition]}]},
        "condition_results": {"budget:within_budget": {
            "text": condition["when_false"], "deps": ["environmental_investment", "budget_limit"]}},
    })
    changed = _manual_update(block, run)
    assert changed["children"] == [{"text": "本方案环保投资超过预算上限。人工补充：用途另行核对。"}]
    assert [ref["value"] for ref in changed["analysis_refs"]] == ["17", "15"]
    assert all(ref["run_id"] == "new" for ref in changed["analysis_refs"])
    assert "未超过" in block["children"][0]["text"]


def test_local_refresh_keeps_condition_as_a_separate_blocking_choice(client):
    base, scenario = create_history(client)
    scenario = update(client, base, scenario, {"N017": "300000"})
    first = run(client, base, scenario)
    report = client.post(base + "/reports", json={"title": "条件句变化"}).json()
    report_id = report["id"]
    assert client.post(base + f"/analysis/reports/{report_id}/select", json={
        "run_id": first["id"], "base_version": 0}).status_code == 200
    report = _accepted_section(client, base, report_id, first["id"], "S4")
    scenario = update(client, base, scenario, {"N017": "200000"})
    second = run(client, base, scenario)
    assert second["snapshot"]["results"]["N035"]["value"] == "200000"
    assert client.post(base + f"/analysis/reports/{report_id}/select", json={
        "run_id": second["id"], "base_version": report["version"]}).status_code == 200
    path = base + f"/analysis/reports/{report_id}/refresh"
    preview = client.get(path + "/preview").json()
    assert {item["kind"] for item in preview["actions"]} == {"numbers", "table", "condition"}
    selected = [item["id"] for item in preview["actions"] if item["kind"] != "condition"]
    partial = client.post(path + "/commit", json={
        "run_id": second["id"], "base_version": preview["report_version"],
        "operation_ids": selected})
    assert partial.status_code == 200, partial.text
    assert any(issue["code"] == "ANALYSIS_CONDITION_STALE" for issue in partial.json()["report"]["issues"])
    assert client.post(base + f"/reports/{report_id}/review").status_code == 400
    remaining = client.get(path + "/preview").json()
    assert [item["kind"] for item in remaining["actions"]] == ["condition"]
    changed = client.post(path + "/commit", json={
        "run_id": second["id"], "base_version": remaining["report_version"],
        "operation_ids": [remaining["actions"][0]["id"]]})
    assert changed.status_code == 200, changed.text
    assert "需求低于合格能力" in str(changed.json()["report"]["content"])
    assert not any(issue["code"] == "ANALYSIS_CONDITION_STALE" for issue in changed.json()["report"]["issues"])


def test_supplier_refresh_does_not_overwrite_manual_paragraph(client):
    base, scenario = create_history(client)
    first = run(client, base, scenario)
    report = client.post(base + "/reports", json={"title": "供应商局部更新"}).json()
    report_id = report["id"]
    assert client.post(base + f"/analysis/reports/{report_id}/select", json={
        "run_id": first["id"], "base_version": 0}).status_code == 200
    report = _accepted_section(client, base, report_id, first["id"], "S7.1")
    scenario = update(client, base, scenario, {"supplier": "新供应商甲"})
    second = run(client, base, scenario)
    assert client.post(base + f"/analysis/reports/{report_id}/select", json={
        "run_id": second["id"], "base_version": report["version"]}).status_code == 200
    path = base + f"/analysis/reports/{report_id}/refresh"
    preview = client.get(path + "/preview").json()
    assert [item["kind"] for item in preview["actions"]] == ["judgement"]
    assert "新供应商甲" in preview["actions"][0]["after"]
    assert "禾进装备" not in preview["actions"][0]["after"]
    changed = client.post(path + "/commit", json={
        "run_id": second["id"], "base_version": preview["report_version"],
        "operation_ids": [preview["actions"][0]["id"]]})
    assert changed.status_code == 200, changed.text
    assert "新供应商甲" in str(changed.json()["report"]["content"])
    assert "报价尚未取得" in str(changed.json()["report"]["content"])
    current = changed.json()["report"]
    blocks = [dict(block) for block in current["content"]]
    paragraph = next(block for block in blocks if block.get("section_id") == "S7.1" and block["type"] == "p")
    paragraph["children"] = [{"text": paragraph["children"][0]["text"] + " 人工补充待核。"}]
    save_preview = client.post(base + f"/reports/{report_id}/preview", json={
        "base_version": current["version"], "content": blocks}).json()
    saved = client.put(base + f"/reports/{report_id}", json={
        "base_version": current["version"], "content": blocks,
        "preview_token": save_preview["preview_token"]})
    assert saved.status_code == 200, saved.text
    scenario = update(client, base, scenario, {"supplier": "另一供应商乙"})
    third = run(client, base, scenario)
    assert client.post(base + f"/analysis/reports/{report_id}/select", json={
        "run_id": third["id"], "base_version": saved.json()["report"]["version"]}).status_code == 200
    blocked = client.get(path + "/preview").json()
    assert [item["kind"] for item in blocked["actions"]] == ["manual"]
    assert not blocked["actions"][0]["selectable"]
    assert client.post(path + "/commit", json={
        "run_id": third["id"], "base_version": blocked["report_version"],
        "operation_ids": [blocked["actions"][0]["id"]]}).status_code == 409


def _save_edited(client, base, report_id, report, content):
    path = base + f"/reports/{report_id}"
    preview = client.post(path + "/preview", json={
        "base_version": report["version"], "content": content})
    assert preview.status_code == 200, preview.text
    saved = client.put(path, json={
        "base_version": report["version"], "content": content,
        "preview_token": preview.json()["preview_token"]})
    assert saved.status_code == 200, saved.text
    return saved.json()["report"]


def test_ambiguous_manual_values_can_be_corrected_then_rebound(client):
    base, scenario = create_history(client)
    scenario = update(client, base, scenario, {"N017": "300000"})
    first = run(client, base, scenario)
    report = client.post(base + "/reports", json={"title": "手工重绑测试"}).json()
    report_id = report["id"]
    assert client.post(base + f"/analysis/reports/{report_id}/select", json={
        "run_id": first["id"], "base_version": report["version"]}).status_code == 200
    report = _accepted_section(client, base, report_id, first["id"], "S4")
    content = deepcopy(report["content"])
    paragraph = next(block for block in content if block["type"] == "p" and block.get("analysis_refs"))
    paragraph["children"][0]["text"] += " 交付安排由项目组复核。"
    paragraph["origin"] = "manual"
    report = _save_edited(client, base, report_id, report, content)
    scenario = update(client, base, scenario, {"N017": "200000"})
    second = run(client, base, scenario)
    selected = client.post(base + f"/analysis/reports/{report_id}/select", json={
        "run_id": second["id"], "base_version": report["version"]})
    assert selected.status_code == 200, selected.text
    report = client.get(base + f"/reports/{report_id}").json()
    path = base + f"/analysis/reports/{report_id}/refresh"
    initial = client.get(path + "/preview").json()
    assert any(row["kind"] == "manual" and row["block_id"] == paragraph["id"] for row in initial["actions"])
    assert not any(row["kind"] == "rebind" and row["block_id"] == paragraph["id"] for row in initial["actions"])
    assert client.get(base + f"/reports/{report_id}/export?level=scenario").status_code == 409

    content = deepcopy(report["content"])
    edited = next(block for block in content if block.get("id") == paragraph["id"])
    original = edited["children"][0]["text"]
    assert original.count("254016套") == 2
    edited["children"] = [{"text": original.replace("300000套", "200000套")
                           .replace("计划销售量为254016套", "计划销售量为200000套")
                           .replace("需求超过合格能力，销售计划受产能限制。",
                                    "需求低于合格能力，销售计划以需求为限。")},
                          {"text": "人工格式保留", "bold": True}]
    report = _save_edited(client, base, report_id, report, content)
    ready = client.get(path + "/preview").json()
    rebind = next(row for row in ready["actions"] if row["kind"] == "rebind" and row["block_id"] == edited["id"])
    assert rebind["selectable"] and rebind["before"] == rebind["after"]
    assert {row["key"]: row["after"] for row in rebind["reference_changes"]} == {
        "N017": "200000套/年", "N034": "254016套/年", "N035": "200000套/年"}
    assert client.get(base + f"/reports/{report_id}").json()["version"] == ready["report_version"]
    table = next(row for row in ready["actions"] if row["kind"] == "table")
    committed = client.post(path + "/commit", json={
        "run_id": second["id"], "base_version": ready["report_version"],
        "operation_ids": [rebind["id"], table["id"]]})
    assert committed.status_code == 200, committed.text
    current = committed.json()["report"]
    result = next(block for block in current["content"] if block.get("id") == edited["id"])
    assert result["children"] == edited["children"]
    assert all(ref["run_id"] == second["id"] for ref in result["analysis_refs"])
    assert not any(issue["code"] in {"ANALYSIS_RUN_STALE", "ANALYSIS_CONDITION_STALE"}
                   for issue in current["issues"])
    assert client.post(base + f"/reports/{report_id}/review").status_code == 200
    exported = client.get(base + f"/reports/{report_id}/export?level=scenario")
    assert exported.status_code == 200, exported.text[:200]
    with zipfile.ZipFile(io.BytesIO(exported.content)) as archive:
        audit = json.loads(archive.read("audit.json"))
        assert "rebind" in json.dumps(audit, ensure_ascii=False)
        with zipfile.ZipFile(io.BytesIO(archive.read("report.docx"))) as docx:
            xml = docx.read("word/document.xml")
            assert b"200000" in xml and b"300000" not in xml
        with pymupdf.open(stream=archive.read("report.pdf"), filetype="pdf") as pdf:
            pdf_text = "\n".join(page.get_text() for page in pdf)
            assert "200000" in pdf_text and "300000" not in pdf_text


def test_detach_requires_old_value_removed_and_checks_version(client):
    base, scenario = create_history(client)
    first = run(client, base, scenario)
    report = client.post(base + "/reports", json={"title": "手工解绑测试"}).json()
    report_id = report["id"]
    assert client.post(base + f"/analysis/reports/{report_id}/select", json={
        "run_id": first["id"], "base_version": 0}).status_code == 200
    report = _accepted_section(client, base, report_id, first["id"], "S7.1")
    content = deepcopy(report["content"])
    paragraph = next(block for block in content if block["type"] == "p" and block.get("analysis_refs"))
    paragraph["children"][0]["text"] += " 采购结论待核实。"
    paragraph["origin"] = "manual"
    report = _save_edited(client, base, report_id, report, content)
    scenario = update(client, base, scenario, {"supplier": "新供应商甲"})
    second = run(client, base, scenario)
    selected = client.post(base + f"/analysis/reports/{report_id}/select", json={
        "run_id": second["id"], "base_version": report["version"]})
    assert selected.status_code == 200
    report = client.get(base + f"/reports/{report_id}").json()
    path = base + f"/analysis/reports/{report_id}/refresh"
    blocked = client.get(path + "/preview").json()
    assert any(row["kind"] == "manual" for row in blocked["actions"])
    assert not any(row["kind"] == "detach" for row in blocked["actions"])
    content = deepcopy(report["content"])
    edited = next(block for block in content if block.get("id") == paragraph["id"])
    edited["children"] = [{"text": "设备来源及报价待补充原件后核对。"}]
    report = _save_edited(client, base, report_id, report, content)
    ready = client.get(path + "/preview").json()
    detach = next(row for row in ready["actions"] if row["kind"] == "detach")
    assert detach["reference_changes"][0]["after"] is None
    assert client.get(base + f"/reports/{report_id}").json()["content"] == content
    assert client.post(path + "/commit", json={
        "run_id": second["id"], "base_version": ready["report_version"] - 1,
        "operation_ids": [detach["id"]]}).status_code == 409
    changed = client.post(path + "/commit", json={
        "run_id": second["id"], "base_version": ready["report_version"],
        "operation_ids": [detach["id"]]})
    assert changed.status_code == 200, changed.text
    result = next(block for block in changed.json()["report"]["content"] if block.get("id") == edited["id"])
    assert "analysis_refs" not in result and result["children"] == edited["children"]
    assert any(issue["code"] == "NO_FACT_LINK" for issue in changed.json()["report"]["issues"])
    assert client.post(base + f"/reports/{report_id}/review").status_code == 400


def test_manual_rebind_refuses_wrong_value_unit_condition_and_table_position():
    from app.analysis_refresh import UnsafeUpdate, _manual_detach, _manual_rebind
    old = {"run_id": "old", "result_key": "demand", "value": "300000", "unit": "套/年"}
    run = SimpleNamespace(id="new", snapshot={"results": {
        "demand": {"value": "200000", "unit": "套/年"}}, "condition": "需求低于产能"})
    block = {"type": "p", "id": "x", "section_id": "S4", "origin": "manual",
             "children": [{"text": "需求200000套，需求超过合格能力，销售计划受产能限制。"}],
             "analysis_refs": [old]}
    with pytest.raises(UnsafeUpdate, match="条件判断"):
        _manual_rebind(block, run)
    block["children"][0]["text"] = "需求200000套。"
    with pytest.raises(UnsafeUpdate, match="数值或位置"):
        _manual_rebind(block, run)
    block["children"][0]["text"] = "需求200000套/年。"
    assert _manual_rebind(block, run)["analysis_refs"][0]["run_id"] == "new"
    block["children"][0]["text"] = "旧需求300000套/年。"
    with pytest.raises(UnsafeUpdate, match="正文仍含旧引用值"):
        _manual_detach(block, run)
    block["children"][0]["text"] = "旧需求300,000套/年。"
    with pytest.raises(UnsafeUpdate, match="正文仍含旧引用值"):
        _manual_detach(block, run)
    assert block["analysis_refs"][0]["run_id"] == "old"


def test_manual_table_rebind_preserves_cell_format_and_checks_top_level_refs():
    from app.analysis_refresh import UnsafeUpdate, _manual_rebind
    ref = {"run_id": "old", "result_key": "capacity", "value": "254016", "unit": "套/年"}
    run = SimpleNamespace(id="new", snapshot={"results": {
        "capacity": {"value": "211680", "unit": "套/年"}}, "condition": None})
    paragraph = {"type": "p", "children": [{"text": "合格能力 "},
                                           {"text": "211,680套/年", "bold": True}],
                 "analysis_refs": [ref]}
    table = {"type": "table", "id": "table-edited", "section_id": "S4", "origin": "manual",
             "analysis_refs": [ref], "children": [{"type": "tr", "children": [
                 {"type": "td", "children": [paragraph]}]}]}
    updated = _manual_rebind(table, run)
    assert updated["children"][0]["children"][0]["children"][0]["children"] == paragraph["children"]
    assert updated["analysis_refs"][0]["run_id"] == "new"
    assert updated["children"][0]["children"][0]["children"][0]["analysis_refs"][0]["run_id"] == "new"
    table["analysis_refs"] = []
    with pytest.raises(UnsafeUpdate, match="单元格不一致"):
        _manual_rebind(table, run)


def test_chapter_add_rename_move_preserve_links_versions_and_export(client):
    base, scenario = create_history(client)
    scenario = update(client, base, scenario, {"N017": "300000"})
    chosen = run(client, base, scenario)
    report = client.post(base + "/reports", json={"title": "章节排序报告"}).json()
    report_id = report["id"]
    assert client.post(base + f"/analysis/reports/{report_id}/select", json={
        "run_id": chosen["id"], "base_version": 0}).status_code == 200
    report = _accepted_section(client, base, report_id, chosen["id"], "S4")
    report = _accepted_section(client, base, report_id, chosen["id"], "S7.1")
    original = deepcopy(report["content"])
    original_version = report["version"]
    headings = {block["section_id"]: block for block in original if block["type"] == "h2"}
    path = base + f"/reports/{report_id}/sections/change"
    renamed = client.post(path, json={"action": "rename", "heading_id": headings["S7.1"]["id"],
                                      "title": "设备方案（人工命名）", "base_version": report["version"]})
    assert renamed.status_code == 200, renamed.text
    report = renamed.json()["report"]
    assert next(block for block in report["content"] if block.get("id") == headings["S7.1"]["id"])["origin"] == "manual"
    moved = client.post(path, json={"action": "move", "heading_id": headings["S7.1"]["id"],
                                    "direction": "up", "base_version": report["version"]})
    assert moved.status_code == 200, moved.text
    report = moved.json()["report"]
    assert [block["section_id"] for block in report["content"] if block["type"] == "h2"] == ["S7.1", "S4"]
    assert client.post(path, json={"action": "move", "heading_id": headings["S7.1"]["id"],
                                   "direction": "up", "base_version": report["version"]}).status_code == 400
    added = client.post(path, json={"action": "add", "title": "项目背景", "base_version": report["version"]})
    assert added.status_code == 200, added.text
    report = added.json()["report"]
    manual_id = added.json()["heading_id"]
    new_heading = next(block for block in report["content"] if block.get("id") == manual_id)
    assert new_heading["section_id"].startswith("manual-")
    assert [block["section_id"] for block in report["content"] if block["type"] == "h2"] == ["S7.1", "S4", new_heading["section_id"]]
    assert client.post(path, json={"action": "rename", "heading_id": manual_id,
                                   "title": "  ", "base_version": report["version"]}).status_code == 400
    assert client.post(path, json={"action": "move", "heading_id": manual_id,
                                   "direction": "up", "base_version": original_version}).status_code == 409
    assert client.post(base.replace(BUILTIN_PROJECT_ID, str(uuid4())) + f"/reports/{report_id}/sections/change",
                       json={"action": "move", "heading_id": manual_id,
                             "direction": "up", "base_version": report["version"]}).status_code == 404
    assert client.get(base + f"/reports/{report_id}").json()["version"] == report["version"]
    for block in original:
        if block.get("section_id") in {"S4", "S7.1"} and block["type"] != "h2":
            assert next(current for current in report["content"] if current.get("id") == block["id"]) == block
    comparison = client.get(base + f"/reports/{report_id}/compare?base={original_version}")
    assert comparison.status_code == 200 and comparison.json()["base_content"] == original

    preview = client.post(base + f"/analysis/reports/{report_id}/draft/preview", json={
        "run_id": chosen["id"], "section_id": "S7.1", "mode": "computed"})
    assert preview.status_code == 200, preview.text
    accepted = client.post(base + f"/analysis/reports/{report_id}/draft/commit", json={
        **{key: value for key, value in preview.json().items() if key not in {"issues", "preserved_blocks"}},
        "replace_section": True})
    assert accepted.status_code == 200, accepted.text
    report = accepted.json()["report"]
    assert next(block for block in report["content"] if block.get("section_id") == "S7.1")["children"] == [{"text": "设备方案（人工命名）"}]
    assert client.post(base + f"/reports/{report_id}/review").status_code == 200
    delivery = client.get(base + f"/reports/{report_id}/export?level=scenario")
    assert delivery.status_code == 200, delivery.text[:200]
    with zipfile.ZipFile(io.BytesIO(delivery.content)) as archive:
        word = Document(io.BytesIO(archive.read("report.docx")))
        word_text = "\n".join(paragraph.text for paragraph in word.paragraphs)
        with pymupdf.open(stream=archive.read("report.pdf"), filetype="pdf") as pdf:
            pdf_text = "\n".join(page.get_text() for page in pdf)
        for text in (word_text, pdf_text):
            assert text.index("设备方案（人工命名）") < text.index("产能与交付") < text.index("项目背景")
        audit = json.loads(archive.read("audit.json"))
        assert audit["analysis_run_id"] == chosen["id"]


def test_chapter_add_to_empty_report_is_project_owned_and_idempotent_on_failure(client):
    project = client.post("/api/projects", json={"name": "章节目录空项目"}).json()
    base = f"/api/projects/{project['id']}"
    report = client.post(base + "/reports", json={"title": "空项目报告"}).json()
    path = base + f"/reports/{report['id']}/sections/change"
    created = client.post(path, json={"action": "add", "title": "项目背景", "base_version": 0})
    assert created.status_code == 200, created.text
    content = created.json()["report"]["content"]
    assert len(content) == 3 and content[1]["type"] == "h2" and content[2]["type"] == "p"
    assert content[1]["section_id"] == content[2]["section_id"]
    assert content[1]["id"] != content[2]["id"]
    unchanged = client.post(path, json={"action": "rename", "heading_id": content[1]["id"],
                                        "title": "项目背景", "base_version": 1})
    assert unchanged.status_code == 200 and unchanged.json()["report"]["version"] == 1
    assert client.post(path, json={"action": "add", "title": "项目背景", "base_version": 1}).status_code == 400
    assert client.get(base + f"/reports/{report['id']}").json()["version"] == 1
    assert client.get(base + "/facts").json()["facts"] == []
