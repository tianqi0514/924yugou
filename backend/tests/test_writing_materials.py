"""30-category chapter controls and non-persistent, falsifiable ablation."""

from __future__ import annotations

import os

os.environ["DATABASE_URL"] = "postgresql+psycopg://report:local_development_only@127.0.0.1:55432/report_platform_test"

import pytest
from fastapi.testclient import TestClient

from app.db import Base, SessionLocal, engine, ProjectFact, ReportDraft
from app.main import app
from app.writing_materials import WritingMaterialSetting


@pytest.fixture(autouse=True)
def isolated_database():
    assert engine.url.database == "report_platform_test", "Refusing to reset a non-test database"
    assert WritingMaterialSetting.__table__.name in Base.metadata.tables
    writable = [table for table in Base.metadata.sorted_tables if not table.name.startswith("corpus_")]
    Base.metadata.drop_all(engine, tables=writable)
    Base.metadata.create_all(engine, tables=writable)
    yield
    Base.metadata.drop_all(engine, tables=writable)


@pytest.fixture
def client():
    with TestClient(app) as instance:
        yield instance


def ok(response):
    assert response.status_code in (200, 201), response.text
    return response.json()


def project(client):
    project_id = ok(client.post("/api/projects", json={"name": "产物贡献实验"}))["id"]
    ok(client.put(f"/api/projects/{project_id}/writing/reference", json={
        "corpus_id": "cy_tray_20260918", "corpus_version": "v2",
        "target_report_type": "feasibility", "accepted": True,
    }))
    return project_id


def url(project_id, section_id="S4"):
    return f"/api/projects/{project_id}/writing/materials/{section_id}"


def catalog(client, project_id, section_id="S4"):
    return ok(client.get(f"/api/projects/{project_id}/writing/materials", params={"section_id": section_id}))


def simulate(client, project_id, section_id="S4", **body):
    request = {"input_mode": "example", "selected_categories": ["CAT-10", "CAT-13", "CAT-11"]}
    request.update(body)
    return ok(client.post(url(project_id, section_id) + "/simulate", json=request))


def test_catalog_exposes_all_thirty_with_actual_chapter_records(client):
    project_id = project(client)
    result = catalog(client, project_id)
    assert result["config_version"] == 0
    assert len(result["items"]) == 30
    assert sum(group["count"] for group in result["groups"]) == 30
    by_id = {item["category_id"]: item for item in result["items"]}
    assert len(by_id) == 30
    assert by_id["CAT-10"]["effective_role"] == "input"
    assert "10:000051" in by_id["CAT-10"]["record_ids"]
    assert len(by_id["CAT-10"]["record_ids"]) > 1
    assert "13:000005" in by_id["CAT-13"]["record_ids"]
    pack = ok(client.get(f"/api/projects/{project_id}/writing/packages/S4"))
    for category_id in ("CAT-10", "CAT-13"):
        assert by_id[category_id]["record_ids"] == [
            item["record_id"] for item in pack["items"] if item["category_id"] == category_id
        ]
    assert by_id["CAT-11"]["effective_role"] == "not_generated"
    assert by_id["CAT-30"]["effective_role"] == "not_generated"
    assert by_id["CAT-11"]["record_ids"] == []
    assert by_id["CAT-10"]["artifact_count"] >= 1
    assert by_id["CAT-10"]["purpose"]
    assert by_id["CAT-10"]["chapter_role"]
    assert by_id["CAT-10"]["limitations"]
    assert {slot["slot_id"] for slot in result["slots"]} == {"N017", "N034", "N035"}
    assert all(slot["value"] is None for slot in result["slots"])


def test_explicit_save_is_versioned_and_keeps_facts_and_reports(client):
    project_id = project(client)
    report_id = ok(client.post(f"/api/projects/{project_id}/reports", json={"title": "原报告"}))["id"]
    before = ok(client.get(f"/api/projects/{project_id}"))["version"]
    saved = ok(client.put(url(project_id), json={"base_version": 0, "settings": [
        {"category_id": "CAT-10", "mode": "review"},
        {"category_id": "CAT-26", "mode": "exclude"},
    ]}))
    assert saved["config_version"] == 1
    assert saved["project_version"] == before + 1
    assert client.put(url(project_id), json={"base_version": 0, "settings": []}).status_code == 409
    assert catalog(client, project_id)["config_version"] == 1
    with SessionLocal() as session:
        assert session.get(WritingMaterialSetting, (project_id, "S4")).settings == {
            "CAT-10": "review", "CAT-26": "exclude",
        }
        assert session.query(ProjectFact).filter_by(project_id=project_id).count() == 0
        assert session.get(ReportDraft, report_id).title == "原报告"
    pack = ok(client.get(f"/api/projects/{project_id}/writing/packages/S4"))
    skeleton = next(item for item in pack["items"] if item["category_id"] == "CAT-10" and
                    item["semantic_id"] == "C0052")
    assert skeleton["decision"] == "review_only"
    assert skeleton["item_id"] in pack["required_item_ids"]
    assert all(item["category_id"] != "CAT-26" for item in pack["items"])
    assert any(issue["code"] == "REQUIRED_MATERIAL_DISABLED" for issue in pack["issues"])


def test_s4_example_ablation_and_zero_missing_are_memory_only(client):
    project_id = project(client)
    before = ok(client.get(f"/api/projects/{project_id}"))["version"]
    result = simulate(client, project_id)
    assert result["simulation_only"] is True
    assert result["baseline"]["status"] == "ready"
    assert result["configured"]["status"] == "ready"
    assert "300,000" in result["configured"]["text"]
    assert "254,016" in result["configured"]["text"]
    assert "需求低于能力" not in result["configured"]["text"]
    assert "禾进装备" not in result["configured"]["text"]
    assert next(fact for fact in result["facts"] if fact["slot_id"] == "N035")["value"] == "254016"
    assert next(step for step in result["trace"] if step["rule_id"] == "simulation:R006")["result"] == "254016"
    experiments = {item["category_id"]: item for item in result["experiments"]}
    assert experiments["CAT-10"]["status"] == "blocked"
    assert "实现依赖" in experiments["CAT-13"]["reason"]
    assert experiments["CAT-11"]["status"] == "not_consumed"
    zero = simulate(client, project_id, slot_values={"N017": 0, "N034": 254016})
    assert zero["configured"]["status"] == "ready"
    assert next(fact for fact in zero["facts"] if fact["slot_id"] == "N035")["value"] == "0"
    missing = simulate(client, project_id, slot_values={"N017": None})
    assert missing["configured"]["status"] == "blocked"
    assert next(fact for fact in missing["facts"] if fact["slot_id"] == "N035")["status"] == "UNEVALUABLE"
    assert ok(client.get(f"/api/projects/{project_id}"))["version"] == before
    assert ok(client.get(f"/api/projects/{project_id}/facts"))["facts"] == []
    with SessionLocal() as session:
        assert session.get(WritingMaterialSetting, (project_id, "S4")) is None


def test_simulation_config_override_does_not_save_and_cannot_fake_consumption(client):
    project_id = project(client)
    result = simulate(client, project_id, settings=[{"category_id": "CAT-15", "mode": "review"}],
                      selected_categories=["CAT-15", "CAT-30"])
    assert result["baseline"]["status"] == "ready"
    assert result["configured"]["status"] == "blocked"
    assert result["experiments"][0]["status"] == "not_consumed"
    assert catalog(client, project_id)["config_version"] == 0
    assert next(item for item in catalog(client, project_id)["items"] if item["category_id"] == "CAT-15")["configured_mode"] == "auto"
    assert client.post(url(project_id) + "/simulate", json={
        "input_mode": "example", "selected_categories": ["CAT-01"] * 7,
    }).status_code == 422
    assert client.post(url(project_id) + "/simulate", json={
        "input_mode": "example", "selected_categories": ["CAT-10"], "slot_values": {"N035": 99},
    }).status_code == 422


def test_review_mode_preserves_inspection_but_stops_candidate_use(client):
    project_id = project(client)
    review_evidence = simulate(client, project_id, settings=[{"category_id": "CAT-18", "mode": "review"}],
                               selected_categories=["CAT-18"])
    assert review_evidence["configured"]["status"] == "ready"
    assert "CAT-18" in review_evidence["baseline"]["used_categories"]
    assert "CAT-18" not in review_evidence["configured"]["used_categories"]
    review_raw = simulate(client, project_id, settings=[{"category_id": "CAT-09", "mode": "review"}],
                          selected_categories=["CAT-09"])
    assert review_raw["configured"]["status"] == "ready"
    assert "CAT-09" in review_raw["baseline"]["used_categories"]
    assert "CAT-09" not in review_raw["configured"]["used_categories"]
    review_conflict = simulate(client, project_id, "S5.2",
                               settings=[{"category_id": "CAT-23", "mode": "review"}],
                               selected_categories=["CAT-23"])
    assert review_conflict["configured"]["status"] == "blocked"
    ok(client.put(url(project_id), json={"base_version": 0,
                                         "settings": [{"category_id": "CAT-18", "mode": "review"}]}))
    pack = ok(client.get(f"/api/projects/{project_id}/writing/packages/S4"))
    evidence = next(item for item in pack["items"] if item["category_id"] == "CAT-18")
    assert evidence["decision"] == "review_only"
    assert evidence["configured_mode"] == "review"


@pytest.mark.parametrize("category_id", ["CAT-04", "CAT-27"])
@pytest.mark.parametrize("mode", ["review", "exclude"])
def test_required_chapter_structure_cannot_be_silently_disabled(client, category_id, mode):
    project_id = project(client)
    result = simulate(client, project_id, settings=[{"category_id": category_id, "mode": mode}],
                      selected_categories=[category_id])
    assert result["baseline"]["status"] == "ready"
    assert result["configured"]["status"] == "blocked"
    assert result["configured"]["text"] == ""
    assert any(issue["severity"] == "block" for issue in result["configured"]["issues"])


def test_other_guided_chapters_and_unadapted_chapter_are_explicit(client):
    project_id = project(client)
    supplier = simulate(client, project_id, "S7.1", selected_categories=["CAT-10", "CAT-09"])
    assert supplier["configured"]["status"] == "ready"
    assert "示例供应商A" in supplier["configured"]["text"]
    assert "禾进装备" not in supplier["configured"]["text"]
    assert supplier["experiments"][0]["status"] == "blocked"
    conflict = simulate(client, project_id, "S5.2", selected_categories=["CAT-23", "CAT-18"])
    assert conflict["configured"]["status"] == "ready"
    assert "未解决分歧" in conflict["configured"]["text"]
    assert "配电适配结论" in conflict["configured"]["text"]
    assert conflict["experiments"][0]["status"] == "blocked"
    sections = ok(client.get(f"/api/projects/{project_id}/writing/sections"))["sections"]
    unadapted_section = next(item["id"] for item in sections if not item["guided_available"])
    assert len(catalog(client, project_id, unadapted_section)["items"]) == 30
    unadapted = simulate(client, project_id, unadapted_section, selected_categories=["CAT-27"])
    assert unadapted["configured"]["status"] == "unadapted"
    assert unadapted["experiments"][0]["status"] == "not_consumed"


def test_project_input_can_try_values_without_persisting_them(client):
    project_id = project(client)
    ok(client.post(f"/api/projects/{project_id}/writing/setup", json={}))
    ok(client.put(f"/api/projects/{project_id}/writing/bindings", json={"bindings": {
        "N017": "first_year_demand", "N034": "qualified_capacity", "N035": "planned_sales",
        "supplier_name": "supplier_name", "N080": "equipment_unit_price",
    }}))
    before = ok(client.get(f"/api/projects/{project_id}"))["version"]
    result = simulate(client, project_id, input_mode="project", slot_values={"N017": 300000, "N034": 254016})
    assert result["configured"]["status"] == "ready"
    assert next(fact for fact in result["facts"] if fact["slot_id"] == "N035")["value"] == "254016"
    assert ok(client.get(f"/api/projects/{project_id}"))["version"] == before
    persisted = {fact["key"]: fact for fact in ok(client.get(f"/api/projects/{project_id}/facts"))["facts"]}
    assert persisted["first_year_demand"]["value"] is None
    assert persisted["qualified_capacity"]["value"] is None
