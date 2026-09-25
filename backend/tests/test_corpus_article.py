"""Historical corpus remains immutable while a project-owned article is saved."""

import os

os.environ["DATABASE_URL"] = "postgresql+psycopg://report:local_development_only@127.0.0.1:55432/report_platform_test"

import pytest
from fastapi.testclient import TestClient

from app.db import Base, engine
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
    with TestClient(app) as test_client:
        yield test_client


def test_preview_cancel_commit_and_plate_save(client, monkeypatch):
    def model(_task, _messages, max_tokens):
        assert max_tokens == 2200
        return {"paragraphs": [
            {"text": "首年客户需求180,000套低于同一规划能力。", "source_ids": ["C0052"]},
            {"text": "若设备可用时长变化，应重算计划销量。", "source_ids": ["C0052"]},
        ]}
    monkeypatch.setattr("app.corpus_article.chat_json", model)
    base = f"/api/projects/{BUILTIN_PROJECT_ID}"
    assert client.get(base + "/corpus/articles/sections").status_code == 200
    before = client.get(base + "/reports").json()
    candidate = client.post(base + "/corpus/articles/preview", json={
        "section_id": "S4", "title": "产能专题整理"})
    assert candidate.status_code == 200, candidate.text
    assert len(candidate.json()["content"]) == 3
    assert client.get(base + "/reports").json() == before  # cancel: no commit
    proposal = {key: value for key, value in candidate.json().items() if key != "sources"}
    tampered = {**proposal, "title": "篡改标题"}
    assert client.post(base + "/corpus/articles/commit", json=tampered).status_code == 409
    committed = client.post(base + "/corpus/articles/commit", json=proposal)
    assert committed.status_code == 201, committed.text
    report = committed.json()["report"]
    assert report["project_id"] == BUILTIN_PROJECT_ID and report["version"] == 0
    assert report["content"][1]["source_refs"][0]["semantic_id"] == "C0052"
    assert len(client.get(base + "/reports").json()) == len(before) + 1
    source = client.get(base + "/corpus/articles/sources/09:000051")
    assert source.status_code == 200 and "180,000" in source.json()["summary"]
    assert source.json()["impacts"][0]["report_id"] == report["id"]
    unsupported = [*report["content"]]
    unsupported[2] = {**unsupported[2], "children": [{"text": "新增999套已证实。"}]}
    url = base + f"/reports/{report['id']}"
    rejected = client.post(url + "/preview", json={"content": unsupported, "base_version": 0})
    assert rejected.status_code == 409 and "999" in rejected.json()["detail"]
    edited = [*report["content"]]
    edited[2] = {**edited[2], "children": [{"text": "设备可用时长变化后，需要重新核对计划销量。"}], "origin": "manual"}
    preview = client.post(url + "/preview", json={"content": edited, "base_version": 0})
    assert preview.status_code == 200, preview.text
    saved = client.put(url, json={"content": edited, "base_version": 0,
                                  "preview_token": preview.json()["preview_token"]})
    assert saved.status_code == 200, saved.text
    refreshed = client.get(url).json()
    assert refreshed["version"] == 1 and "重新核对" in refreshed["content"][2]["children"][0]["text"]
    assert len(client.get(url + "/versions").json()) == 2
    assert client.get(base + "/facts").json()["facts"] == []


def test_model_rejects_unreferenced_number_and_conflict_omission(client, monkeypatch):
    base = f"/api/projects/{BUILTIN_PROJECT_ID}"
    monkeypatch.setattr("app.corpus_article.chat_json", lambda *_args, **_kwargs: {
        "paragraphs": [{"text": "项目新增123456789套。", "source_ids": ["C0052"]},
                       {"text": "需要核对。", "source_ids": ["C0052"]}]})
    response = client.post(base + "/corpus/articles/preview", json={
        "section_id": "S4", "title": "测试"})
    assert response.status_code == 409 and "数字" in response.json()["detail"]
    assert client.get(base + "/reports").json() == []
    monkeypatch.setattr("app.corpus_article.chat_json", lambda *_args, **_kwargs: {
        "paragraphs": [{"text": "资料记载可用余量800kW。", "source_ids": ["C0063"]},
                       {"text": "仍需核对资料。", "source_ids": ["C0063"]}]})
    conflict = client.post(base + "/corpus/articles/preview", json={
        "section_id": "S5.2", "title": "配电专题"})
    assert conflict.status_code == 409 and "800kW与650kW" in conflict.json()["detail"]
    monkeypatch.setattr("app.corpus_article.chat_json", lambda *_args, **_kwargs: {
        "paragraphs": [{"text": "同一余量分别为800kW与650kW，资料不一致。", "source_ids": ["C0063"]},
                       {"text": "分歧尚未解决。", "source_ids": ["C0063"]}]})
    valid = client.post(base + "/corpus/articles/preview", json={
        "section_id": "S5.2", "title": "配电专题"})
    assert valid.status_code == 200, valid.text
    created = client.post(base + "/corpus/articles/commit", json={
        key: value for key, value in valid.json().items() if key != "sources"})
    assert created.status_code == 201, created.text
    report = created.json()["report"]
    removal = client.post(base + f"/reports/{report['id']}/preview", json={
        "content": report["content"][:1], "base_version": 0})
    assert removal.status_code == 409 and "未解决分歧" in removal.json()["detail"]


def test_exact_table_heading_is_added_to_paragraph_sources(client, monkeypatch):
    monkeypatch.setattr("app.corpus_article.chat_json", lambda *_args, **_kwargs: {
        "paragraphs": [{"text": "表4-1列出生产线数量2条。", "source_ids": ["C0047"]},
                       {"text": "首年客户需求180,000套。", "source_ids": ["C0052"]}]})
    response = client.post(f"/api/projects/{BUILTIN_PROJECT_ID}/corpus/articles/preview", json={
        "section_id": "S4", "title": "产能专题"})
    assert response.status_code == 200, response.text
    ids = [ref["semantic_id"] for ref in response.json()["content"][1]["source_refs"]]
    assert ids == ["C0047", "C0046"]
