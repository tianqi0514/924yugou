"""Published project contracts drive runs and chapters without rewriting old snapshots."""

import io
import json
import os
import zipfile
from pathlib import Path
from uuid import uuid4

os.environ["DATABASE_URL"] = "postgresql+psycopg://report:local_development_only@127.0.0.1:55432/report_platform_test"

import pytest
from fastapi.testclient import TestClient

from app.db import Base, engine
from app.main import app


@pytest.fixture(autouse=True)
def isolated_database():
    assert engine.url.database == "report_platform_test"
    tables = [table for table in Base.metadata.sorted_tables if not table.name.startswith("corpus_")]
    Base.metadata.drop_all(engine, tables=tables)
    Base.metadata.create_all(engine, tables=tables)
    yield
    Base.metadata.drop_all(engine, tables=tables)


@pytest.fixture
def client():
    with TestClient(app) as value:
        yield value


FIELDS = [
    {"key": "demand", "label": "需求", "data_type": "integer", "unit": "套", "group": "需求", "computed": False},
    {"key": "capacity", "label": "能力", "data_type": "integer", "unit": "套", "group": "产能", "computed": False},
    {"key": "sales", "label": "计划销售", "data_type": "integer", "unit": "套", "group": "销售", "computed": True},
]
SECTIONS = [{"id": "chapter-1", "title": "销售测算", "result_keys": ["demand", "capacity", "sales"]}]


def project(client, name="配置测试"):
    response = client.post("/api/projects", json={"name": name})
    assert response.status_code == 201, response.text
    return f"/api/projects/{response.json()['id']}"


def draft_update(client, base, config, expression="min(demand, capacity)", fields=None):
    response = client.put(base + f"/analysis/configs/{config['id']}", json={
        "revision": config["revision"], "name": config["name"],
        "definitions": fields or FIELDS,
        "rules": [{"id": "sales_rule", "name": "计划销售", "target_key": "sales", "expression": expression}],
        "sections": SECTIONS})
    assert response.status_code == 200, response.text
    return response.json()


def trial(client, base, config, demand="300000", capacity="254016"):
    return client.post(base + f"/analysis/configs/{config['id']}/test", json={
        "revision": config["revision"], "sample_inputs": {"demand": demand, "capacity": capacity}})


def publish(client, base, config, result):
    return client.post(base + f"/analysis/configs/{config['id']}/publish", json={
        "revision": config["revision"], "test_token": result.json()["test_token"]})


def test_config_version_run_and_chapter_are_immutable(client):
    base = project(client)
    created = client.post(base + "/analysis/configs", json={"name": "测算配置"})
    assert created.status_code == 201, created.text
    first_config = draft_update(client, base, created.json())
    first_trial = trial(client, base, first_config)
    assert first_trial.status_code == 200, first_trial.text
    assert first_trial.json()["snapshot"]["results"]["sales"]["value"] == "254016"
    assert publish(client, base, first_config, first_trial).status_code == 200
    assert client.put(base + f"/analysis/configs/{first_config['id']}", json={
        "revision": first_config["revision"], "name": "改写", "definitions": FIELDS,
        "rules": first_config["rules"], "sections": SECTIONS}).status_code == 409
    created = client.post(base + "/analysis/scenarios", json={
        "name": "基准", "source": "config", "config_id": first_config["id"]})
    assert created.status_code == 201, created.text
    scenario = created.json()
    assert scenario["inputs"]["demand"]["value"] is None
    response = client.put(base + f"/analysis/scenarios/{scenario['id']}", json={
        "base_revision": 1, "changes": {"demand": "300000", "capacity": "254016"}})
    assert response.status_code == 200, response.text
    scenario = response.json()
    response = client.post(base + f"/analysis/scenarios/{scenario['id']}/runs", json={
        "scenario_revision": scenario["revision"], "request_key": str(uuid4())})
    assert response.status_code == 201, response.text
    old_run = response.json()
    assert old_run["snapshot"]["configuration"]["id"] == first_config["id"]
    assert old_run["snapshot"]["results"]["sales"]["value"] == "254016"
    report = client.post(base + "/reports", json={"title": "销售报告"}).json()
    assert client.post(base + f"/analysis/reports/{report['id']}/select", json={
        "run_id": old_run["id"], "base_version": 0}).status_code == 200
    candidate = client.post(base + f"/analysis/reports/{report['id']}/draft/preview", json={
        "run_id": old_run["id"], "section_id": "chapter-1", "mode": "computed"})
    assert candidate.status_code == 200, candidate.text
    assert "254016" in str(candidate.json()["content"])
    assert "禾进装备" not in str(candidate.json()["content"])
    response = client.post(base + f"/analysis/reports/{report['id']}/draft/commit", json={
        **{key: value for key, value in candidate.json().items() if key not in {"issues", "preserved_blocks"}},
        "replace_section": False})
    assert response.status_code == 200, response.text
    assert any(block.get("analysis_refs") for block in response.json()["report"]["content"])
    assert client.post(base + f"/reports/{report['id']}/review").status_code == 200

    second = client.post(base + "/analysis/configs", json={
        "name": "备选口径", "from_config_id": first_config["id"]})
    assert second.status_code == 201, second.text
    new_config = draft_update(client, base, second.json(), "max(demand, capacity)")
    new_trial = trial(client, base, new_config)
    assert new_trial.json()["snapshot"]["results"]["sales"]["value"] == "300000"
    assert publish(client, base, new_config, new_trial).status_code == 200
    response = client.post(base + "/analysis/scenarios", json={
        "name": "新配置", "source": "config", "config_id": new_config["id"], "copy_from": scenario["id"]})
    assert response.status_code == 201, response.text
    copied = response.json()
    assert copied["inputs"]["demand"]["origin"] == "scenario_assumption"
    response = client.post(base + f"/analysis/scenarios/{copied['id']}/runs", json={
        "scenario_revision": 1, "request_key": str(uuid4())})
    assert response.status_code == 201, response.text
    assert response.json()["snapshot"]["results"]["sales"]["value"] == "300000"
    assert client.get(base + f"/analysis/runs/{old_run['id']}").json()["snapshot"]["results"]["sales"]["value"] == "254016"
    assert client.get(base + f"/reports/{report['id']}").json()["analysis_run_id"] == old_run["id"]


def test_config_zero_missing_cycle_division_unit_and_project_isolation(client):
    base = project(client)
    created = client.post(base + "/analysis/configs", json={"name": "边界配置"}).json()
    path = base + f"/analysis/configs/{created['id']}"
    bad = client.put(path, json={"revision": 1, "name": "边界配置", "definitions": FIELDS,
        "rules": [{"id": "bad", "name": "坏规则", "target_key": "sales",
                   "expression": "__import__('os').system('true')"}], "sections": SECTIONS})
    assert bad.status_code == 400
    draft = draft_update(client, base, created)
    zero = trial(client, base, draft, demand="0")
    assert zero.status_code == 200 and zero.json()["snapshot"]["results"]["sales"]["value"] == "0"
    missing = trial(client, base, draft, demand=None)
    assert missing.status_code == 200 and missing.json()["test_token"] is None
    assert client.post(path + "/publish", json={"revision": draft["revision"],
        "test_token": ""}).status_code == 409
    wrong_units = [{**field, "unit": "元" if field["key"] == "capacity" else field["unit"]}
                   for field in FIELDS]
    changed = draft_update(client, base, draft, fields=wrong_units)
    assert trial(client, base, changed).status_code == 400
    assert client.post(path + "/publish", json={"revision": changed["revision"],
        "test_token": zero.json()["test_token"]}).status_code == 409
    cycle_fields = [*FIELDS, {"key": "other", "label": "另一结果", "data_type": "integer",
                              "unit": "套", "group": "销售", "computed": True}]
    cycle = client.put(path, json={"revision": changed["revision"], "name": "边界配置",
        "definitions": cycle_fields,
        "rules": [{"id": "r1", "name": "一", "target_key": "sales", "expression": "other + demand"},
                  {"id": "r2", "name": "二", "target_key": "other", "expression": "sales + capacity"}],
        "sections": SECTIONS})
    assert cycle.status_code == 400
    assert client.get(path).json()["revision"] == changed["revision"]
    division = draft_update(client, base, changed, "demand / (capacity - capacity)", fields=FIELDS)
    assert trial(client, base, division).status_code == 400
    other = project(client, "另一项目")
    assert client.get(other + f"/analysis/configs/{created['id']}").status_code == 404
    assert client.post(other + "/analysis/scenarios", json={
        "name": "跨项目", "source": "config", "config_id": created["id"]}).status_code == 404


def _set_fact(client, base: str, key: str, label: str, value: str, unit: str, data_type="decimal"):
    created = client.post(base + "/facts", json={"key": key, "label": label,
        "data_type": data_type, "unit": unit, "source": "晋江事故调查公开原件"})
    assert created.status_code == 201, created.text
    change = {"fact_key": key, "value": value, "source": "晋江事故调查公开原件", "reason": "固定样本核对"}
    preview = client.post(base + "/changes/preview", json=change)
    assert preview.status_code == 200, preview.text
    committed = client.post(base + "/changes/commit", json={**change,
        "base_version": preview.json()["base_version"], "preview_token": preview.json()["preview_token"]})
    assert committed.status_code == 200, committed.text


def test_accident_sample_conditions_evidence_model_and_partial_adoption(client, monkeypatch, tmp_path):
    import app.main as main
    import app.analysis_writing as writing
    from app.model_settings import ChatResult
    monkeypatch.setattr(main, "STORAGE", tmp_path)
    base = project(client, "事故调查条件证据 QA")
    original = Path(__file__).resolve().parents[2] / "test-fixtures/public-reports/04_jinjiang_accident.pdf"
    uploaded = client.post(base + "/documents", files={"file": (
        "晋江事故调查报告.pdf", original.read_bytes(), "application/pdf")})
    assert uploaded.status_code == 201, uploaded.text
    document_id = uploaded.json()["id"]
    source_page = client.get(base + f"/documents/{document_id}?page=3").json()["segments"]
    death_ref = next(row["ref"] for row in source_page if "1 人死亡" in row["text"] or "1人死亡" in row["text"])
    loss_ref = next(row["ref"] for row in source_page if "77.0239" in row["text"])
    _set_fact(client, base, "death_count", "死亡人数", "1", "人", "integer")
    _set_fact(client, base, "direct_loss", "直接经济损失", "77.0239", "万元")
    for key, ref in (("death_count", death_ref), ("direct_loss", loss_ref)):
        response = client.post(base + f"/facts/{key}/evidence/bind", json={
            "document_id": document_id, "source_refs": [ref]})
        assert response.status_code == 200, response.text

    fields = [
        {"key": "death_count", "label": "死亡人数", "data_type": "integer", "unit": "人", "group": "人员", "computed": False},
        {"key": "direct_loss", "label": "直接经济损失", "data_type": "decimal", "unit": "万元", "group": "损失", "computed": False},
        {"key": "loss_threshold", "label": "方案阈值", "data_type": "decimal", "unit": "万元", "group": "假设", "computed": False},
        {"key": "remaining", "label": "距阈值差额", "data_type": "decimal", "unit": "万元", "group": "推演", "computed": True},
    ]
    chapters = [
        {"id": "casualties", "title": "人员伤亡", "result_keys": ["death_count"],
         "evidence_keys": ["death_count"], "forbidden_terms": ["责任认定", "禾进装备"],
         "conditions": [{"id": "has_death", "expression": "death_count > 0",
                         "when_true": "记录存在人员死亡。", "when_false": "本方案记录死亡人数为零。"}]},
        {"id": "loss", "title": "经济损失情景", "result_keys": ["direct_loss", "loss_threshold", "remaining"],
         "evidence_keys": ["direct_loss"], "forbidden_terms": ["依法处罚", "事故等级"],
         "conditions": [{"id": "under_threshold", "expression": "direct_loss <= loss_threshold",
                         "when_true": "损失低于或等于本方案设定阈值。",
                         "when_false": "损失高于本方案设定阈值。"}]},
    ]
    config = client.post(base + "/analysis/configs", json={"name": "事故报告试验配置"}).json()
    saved = client.put(base + f"/analysis/configs/{config['id']}", json={
        "revision": config["revision"], "name": config["name"], "definitions": fields,
        "rules": [{"id": "remaining_rule", "name": "差额", "target_key": "remaining",
                   "expression": "max(loss_threshold - direct_loss, 0)"}], "sections": chapters})
    assert saved.status_code == 200, saved.text
    config = saved.json()
    trial = client.post(base + f"/analysis/configs/{config['id']}/test", json={
        "revision": config["revision"], "sample_inputs": {"death_count": "1",
            "direct_loss": "77.0239", "loss_threshold": "80"}})
    assert trial.status_code == 200, trial.text
    assert trial.json()["snapshot"]["results"]["remaining"]["value"] == "2.9761"
    assert trial.json()["snapshot"]["condition_results"]["loss:under_threshold"]["outcome"] is True
    assert client.post(base + f"/analysis/configs/{config['id']}/publish", json={
        "revision": config["revision"], "test_token": trial.json()["test_token"]}).status_code == 200
    scenario = client.post(base + "/analysis/scenarios", json={
        "name": "原件数据情景", "source": "config", "config_id": config["id"]}).json()
    edited = client.put(base + f"/analysis/scenarios/{scenario['id']}", json={
        "base_revision": scenario["revision"], "changes": {"death_count": "1",
            "direct_loss": "77.0239", "loss_threshold": "80"}})
    assert edited.status_code == 200, edited.text
    scenario = edited.json()
    run = client.post(base + f"/analysis/scenarios/{scenario['id']}/runs", json={
        "scenario_revision": scenario["revision"], "request_key": str(uuid4())}).json()
    report = client.post(base + "/reports", json={"title": "事故报告双章试验"}).json()
    report_id = report["id"]
    assert client.post(base + f"/analysis/reports/{report_id}/select", json={
        "run_id": run["id"], "base_version": 0}).status_code == 200
    chapter = client.post(base + f"/analysis/reports/{report_id}/draft/preview", json={
        "run_id": run["id"], "section_id": "casualties", "mode": "computed"})
    assert chapter.status_code == 200, chapter.text
    assert chapter.json()["model_audit"]["evidence"][0]["status"] == "VERIFIED"
    assert "记录存在人员死亡" in str(chapter.json()["content"])
    selected = [block["id"] for block in chapter.json()["content"] if block["type"] == "p"]
    adopted = client.post(base + f"/analysis/reports/{report_id}/draft/commit", json={
        **{key: value for key, value in chapter.json().items() if key not in {"issues", "preserved_blocks"}},
        "replace_section": False, "selected_block_ids": selected})
    assert adopted.status_code == 200, adopted.text
    assert not any(block["type"] == "table" for block in adopted.json()["report"]["content"])
    original_paragraph = next(block for block in adopted.json()["report"]["content"]
                              if block.get("section_id") == "casualties" and block["type"] == "p")
    extra = client.post(base + f"/analysis/reports/{report_id}/draft/preview", json={
        "run_id": run["id"], "section_id": "casualties", "mode": "computed"})
    table_id = next(block["id"] for block in extra.json()["content"] if block["type"] == "table")
    appended = client.post(base + f"/analysis/reports/{report_id}/draft/commit", json={
        **{key: value for key, value in extra.json().items() if key not in {"issues", "preserved_blocks"}},
        "replace_section": True, "selected_block_ids": [table_id]})
    assert appended.status_code == 200, appended.text
    assert any(block["type"] == "table" for block in appended.json()["report"]["content"])
    assert any(block.get("id") == original_paragraph["id"] for block in appended.json()["report"]["content"])

    monkeypatch.setattr(writing, "chat_json", lambda *_args, **_kwargs: {
        "text": "本方案死亡人数为1人。记录存在人员死亡。", "used_result_keys": ["death_count"]})
    modeled = client.post(base + f"/analysis/reports/{report_id}/draft/preview", json={
        "run_id": run["id"], "section_id": "loss", "mode": "model"})
    assert modeled.status_code == 422  # wrong chapter condition and number claims are rejected
    monkeypatch.setattr(writing, "chat_json", lambda *_args, **_kwargs: ChatResult({
        "text": "直接经济损失为77.0239万元，距本方案阈值差额为2.9761万元。损失低于或等于本方案设定阈值。",
        "used_result_keys": ["direct_loss", "remaining"]}, {"model_id": "qa-model"}))
    modeled = client.post(base + f"/analysis/reports/{report_id}/draft/preview", json={
        "run_id": run["id"], "section_id": "loss", "mode": "model"})
    assert modeled.status_code == 200, modeled.text
    assert modeled.json()["content"][-1]["origin"] == "model"
    accepted = client.post(base + f"/analysis/reports/{report_id}/draft/commit", json={
        **{key: value for key, value in modeled.json().items() if key not in {"issues", "preserved_blocks"}},
        "replace_section": False})
    assert accepted.status_code == 200, accepted.text
    assert not any("禾进装备" in str(block) for block in accepted.json()["report"]["content"])

    scenario = client.put(base + f"/analysis/scenarios/{scenario['id']}", json={
        "base_revision": scenario["revision"], "changes": {"loss_threshold": "70"}}).json()
    second = client.post(base + f"/analysis/scenarios/{scenario['id']}/runs", json={
        "scenario_revision": scenario["revision"], "request_key": str(uuid4())}).json()
    assert second["snapshot"]["results"]["remaining"]["value"] == "0"
    assert second["snapshot"]["condition_results"]["loss:under_threshold"]["outcome"] is False
    current = client.get(base + f"/reports/{report_id}").json()
    assert client.post(base + f"/analysis/reports/{report_id}/select", json={
        "run_id": second["id"], "base_version": current["version"]}).status_code == 200
    changed = client.get(base + f"/analysis/reports/{report_id}/refresh/preview")
    assert changed.status_code == 200, changed.text
    assert any(row["kind"] == "config_condition" and "损失高于" in row["after"]
               for row in changed.json()["actions"])
    actions = [row["id"] for row in changed.json()["actions"] if row["selectable"]]
    refreshed = client.post(base + f"/analysis/reports/{report_id}/refresh/commit", json={
        "run_id": second["id"], "base_version": changed.json()["report_version"],
        "operation_ids": actions})
    assert refreshed.status_code == 200, refreshed.text
    assert "损失高于本方案设定阈值" in str(refreshed.json()["report"]["content"])
    assert "2.9761" in str(refreshed.json()["report"]["content"])  # 旧模型句仍须人工重写
    assert any(issue["code"] == "ANALYSIS_RUN_STALE" for issue in refreshed.json()["report"]["issues"])
    monkeypatch.setattr(writing, "chat_json", lambda *_args, **_kwargs: ChatResult({
        "text": "直接经济损失为77.0239万元，距本方案阈值差额为0万元。损失高于本方案设定阈值。",
        "used_result_keys": ["direct_loss", "remaining"]}, {"model_id": "qa-model"}))
    revised = client.post(base + f"/analysis/reports/{report_id}/draft/preview", json={
        "run_id": second["id"], "section_id": "loss", "mode": "model"})
    assert revised.status_code == 200, revised.text
    accepted = client.post(base + f"/analysis/reports/{report_id}/draft/commit", json={
        **{key: value for key, value in revised.json().items() if key not in {"issues", "preserved_blocks"}},
        "replace_section": True})
    assert accepted.status_code == 200, accepted.text
    assert "2.9761" not in str(accepted.json()["report"]["content"])
    reviewed = client.post(base + f"/reports/{report_id}/review")
    assert reviewed.status_code == 200, reviewed.text
    exported = client.get(base + f"/reports/{report_id}/export?level=scenario")
    assert exported.status_code == 200, exported.text[:500]
    with zipfile.ZipFile(io.BytesIO(exported.content)) as archive:
        audit = json.loads(archive.read("audit.json"))
        assert audit["analysis_run_id"] == second["id"]
        assert any(item["run_id"] == second["id"] and item["applied_to_current"]
                   for item in audit["writing_model_audits"])
        with zipfile.ZipFile(io.BytesIO(archive.read("report.docx"))) as word:
            xml = word.read("word/document.xml")
            assert b"77.0239" in xml and b"2.9761" not in xml
    assert "77.0239" in str(refreshed.json()["report"]["content"])
    change = {"fact_key": "direct_loss", "value": "77.024", "source": "复核差异",
              "reason": "检验已选运行与项目事实不一致"}
    preview = client.post(base + "/changes/preview", json=change).json()
    assert client.post(base + "/changes/commit", json={**change,
        "base_version": preview["base_version"],
        "preview_token": preview["preview_token"]}).status_code == 200
    stale = client.get(base + f"/reports/{report_id}").json()
    assert any(item["code"] == "CHAPTER_EVIDENCE_MISMATCH" for item in stale["issues"])
    assert client.get(base + f"/reports/{report_id}/export?level=scenario").status_code == 409
