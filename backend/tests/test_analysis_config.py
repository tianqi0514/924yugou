"""Published project contracts drive runs and chapters without rewriting old snapshots."""

import os
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
