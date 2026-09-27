"""NEXT-30: typed reports start with structure and never inherit old project values."""

import os

os.environ["DATABASE_URL"] = "postgresql+psycopg://report:local_development_only@127.0.0.1:55432/report_platform_test"

import pytest
from fastapi.testclient import TestClient

from app.db import Base, engine
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


def test_government_and_enterprise_outlines_are_distinct_and_value_free(client):
    project = client.post("/api/projects", json={"name": "空项目"}).json()
    base = f"/api/projects/{project['id']}"
    catalog = client.get("/api/report-types")
    assert catalog.status_code == 200
    assert {row["id"] for row in catalog.json()} == {
        "government_feasibility", "enterprise_feasibility", "custom"}
    reports = {}
    for kind in ("government_feasibility", "enterprise_feasibility"):
        created = client.post(base + "/reports", json={"title": kind, "report_type": kind})
        assert created.status_code == 201, created.text
        row = created.json()
        assert row["report_type"] == kind and row["template_version"] == "2026-09-27.1"
        assert len([block for block in row["content"] if block["type"] == "h2"]) == 9
        assert all(block.get("fact_keys") is None and block.get("source_refs") is None
                   for block in row["content"])
        assert not row["facts"] and not row["bound_facts"]
        chapters = client.get(base + f"/reports/{row['id']}/chapters")
        assert chapters.status_code == 200, chapters.text
        assert len(chapters.json()["chapters"]) == 9
        assert {chapter["status"] for chapter in chapters.json()["chapters"]} == {"待配置"}
        reports[kind] = row
    assert reports["government_feasibility"]["template_snapshot"]["sections"][1]["title"] != \
        reports["enterprise_feasibility"]["template_snapshot"]["sections"][1]["title"]
    assert client.get(base + f"/reports/{reports['government_feasibility']['id']}").json()[
        "template_snapshot"] == reports["government_feasibility"]["template_snapshot"]
    other = client.post("/api/projects", json={"name": "别人的项目"}).json()
    assert client.get(f"/api/projects/{other['id']}/reports/{reports['government_feasibility']['id']}").status_code == 404


def test_invalid_type_does_not_create_report_and_legacy_default_stays_custom(client):
    project = client.post("/api/projects", json={"name": "类型拒绝"}).json()
    base = f"/api/projects/{project['id']}"
    assert client.post(base + "/reports", json={"title": "X", "report_type": "made-up"}).status_code == 400
    assert client.get(base + "/reports").json() == []
    legacy = client.post(base + "/reports", json={"title": "旧式报告"})
    assert legacy.status_code == 201 and legacy.json()["report_type"] == "custom"
    assert len(legacy.json()["content"]) == 2
