"""NEXT-01 preregistered model-writing boundary tests. Destructive fixtures target only report_platform_test."""

from __future__ import annotations

import copy
import json
import os
from io import BytesIO
from zipfile import ZipFile

os.environ["DATABASE_URL"] = "postgresql+psycopg://report:local_development_only@127.0.0.1:55432/report_platform_test"

import pytest
from fastapi.testclient import TestClient

from app import writing_model
from app.db import Base, engine
from app.main import app
from app.model_settings import ChatResult


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
    with TestClient(app) as instance:
        yield instance


def ok(response):
    assert response.status_code in (200, 201), response.text
    return response.json()


def project_with_reference(client):
    pid = ok(client.post("/api/projects", json={"name": "NEXT-01 独立验收"}))["id"]
    assert ok(client.get(f"/api/projects/{pid}/facts"))["facts"] == []
    ok(client.put(f"/api/projects/{pid}/writing/reference", json={
        "corpus_id": "cy_tray_20260918", "corpus_version": "v2",
        "target_report_type": "feasibility", "accepted": True,
    }))
    assert ok(client.get(f"/api/projects/{pid}/facts"))["facts"] == []
    return pid


def setup(client, pid):
    assert ok(client.post(f"/api/projects/{pid}/writing/setup", json={}))["historical_values_copied"] == 0
    ok(client.put(f"/api/projects/{pid}/writing/bindings", json={"bindings": {
        "N017": "first_year_demand", "N034": "qualified_capacity", "N035": "planned_sales",
        "supplier_name": "supplier_name", "N080": "equipment_unit_price",
    }}))


def set_fact(client, pid, key, value):
    change = {"fact_key": key, "value": value, "source": "本项目核对输入", "reason": "NEXT-01 验证"}
    preview = ok(client.post(f"/api/projects/{pid}/changes/preview", json=change))
    ok(client.post(f"/api/projects/{pid}/changes/commit", json={
        **change, "base_version": preview["base_version"], "preview_token": preview["preview_token"],
    }))


def scenario(client, section="S4", demand="300000", capacity="254016"):
    pid = project_with_reference(client)
    setup(client, pid)
    if section == "S4":
        if demand is not None:
            set_fact(client, pid, "first_year_demand", demand)
        set_fact(client, pid, "qualified_capacity", capacity)
    else:
        set_fact(client, pid, "supplier_name", "新拓设备")
        set_fact(client, pid, "equipment_unit_price", "1234.50")
    rid = ok(client.post(f"/api/projects/{pid}/reports", json={"title": "模型输入验收稿"}))["id"]
    pack = ok(client.get(f"/api/projects/{pid}/writing/packages/{section}"))
    approved = [item["item_id"] for item in pack["items"] if item["required"]]
    return pid, rid, pack, approved


def model_input(client, pid, rid, section, approved):
    return client.post(f"/api/projects/{pid}/reports/{rid}/writing/model-input", json={
        "section_id": section, "approved_item_ids": approved, "mode": "model",
    })


def preview(client, pid, rid, section, approved, input_sha256):
    return client.post(f"/api/projects/{pid}/reports/{rid}/writing/preview", json={
        "section_id": section, "approved_item_ids": approved, "mode": "model",
        "expected_input_sha256": input_sha256,
    })


def material_id(input_data, role):
    return next(item["item_id"] for item in input_data["materials"] if item["role"] == role)


def s4_answer(input_data):
    skeleton = material_id(input_data, "skeleton")
    rule = material_id(input_data, "rule")
    claim = material_id(input_data, "claim")
    values = {fact["key"]: fact["value"] for fact in input_data["facts"]}
    demand, capacity, sales = (values[key] for key in (
        "first_year_demand", "qualified_capacity", "planned_sales"))
    def formatted(value):
        return format(int(value), ",")
    return {"paragraphs": [
        {"text": f"首年客户需求{formatted(demand)}套，合格能力{formatted(capacity)}套，首年计划销售量{formatted(sales)}套。",
         "fact_keys": ["first_year_demand", "qualified_capacity", "planned_sales"],
         "source_item_ids": [skeleton, rule]},
        {"text": "上述需求仅作为本项目测算输入，不能据此认定已有已签订单或最低采购承诺。",
         "fact_keys": [], "source_item_ids": [claim]},
    ]}


def model_result(answer):
    return ChatResult(answer, {
        "model_id": "qa-model", "model": "stub-writing-model", "model_version": "stub-v1",
        "source": "test", "protocol": "chat", "temperature": 0,
        "max_tokens": 1200, "response_id": "qa-response",
    })


def sent_payload(messages):
    user = json.loads(messages[-1]["content"])
    return user.get("model_input", user)


def audits(client, pid, rid):
    return ok(client.get(f"/api/projects/{pid}/reports/{rid}/writing/model-audits"))["audits"]


def test_s4_model_input_is_the_exact_sanitized_sent_payload_and_cancel_is_read_only(client, monkeypatch):
    pid, rid, pack, approved = scenario(client)
    response = ok(model_input(client, pid, rid, "S4", approved))
    data = response["model_input"]
    assert data["schema_version"] == "writing-input/v1"
    assert data["project_id"] == pid and data["report_id"] == rid
    assert isinstance(data["project_version"], int) and isinstance(data["report_version"], int)
    assert data["section"]["id"] == "S4"
    assert data["corpus"] == {"id": pack["corpus_id"], "version": pack["corpus_version"]}
    assert len(response["input_sha256"]) == 64
    assert {fact["key"] for fact in data["facts"]} == {
        "first_year_demand", "qualified_capacity", "planned_sales"}
    assert {fact["key"]: fact["value"] for fact in data["facts"]} == {
        "first_year_demand": "300000", "qualified_capacity": "254016", "planned_sales": "254016"}
    assert all(fact["revision"] >= 1 and "status" in fact["evidence"] for fact in data["facts"])
    assert any(rule["expression"] == "min(first_year_demand, qualified_capacity)"
               and rule["target_key"] == "planned_sales" and rule["result"] == "254016"
               for rule in data["rules"])
    sent_ids = {item["item_id"] for item in data["materials"]}
    assert set(approved) <= sent_ids
    assert all({"item_id", "role", "source_ref", "review_status", "usage", "content"} <= set(item)
               for item in data["materials"])
    assert not any(item["role"] in {"style", "historical_text", "historical_evidence", "historical_excerpt"}
                   for item in data["materials"])
    forbidden = ("禾进装备", "12,345,000", "180,000", "需求低于能力")
    assert not any(term in json.dumps(data, ensure_ascii=False) for term in forbidden)
    assert isinstance(response["excluded_items"], list)

    captured = []
    def call(task, messages, max_tokens):
        assert task == "writing" and max_tokens > 0
        sent = sent_payload(messages)
        captured.append(sent)
        return model_result(s4_answer(sent))
    monkeypatch.setattr(writing_model, "chat_json", call)
    old = ok(client.get(f"/api/projects/{pid}/reports/{rid}"))
    candidate = ok(preview(client, pid, rid, "S4", approved, response["input_sha256"]))
    assert captured == [data]
    assert candidate["input_sha256"] == response["input_sha256"]
    assert candidate["model_input"] == data
    assert candidate["expected_input_sha256"] == response["input_sha256"]
    assert candidate["model_call"] and candidate["model_usage"]
    assert {tuple(row["source_item_ids"]) for row in candidate["model_usage"]} == {
        (material_id(data, "skeleton"), material_id(data, "rule")), (material_id(data, "claim"),)}
    assert {row["paragraph_id"] for row in candidate["model_usage"]} <= {
        paragraph["id"] for paragraph in candidate["paragraphs"]}
    assert all(set(row["source_item_ids"]) <= sent_ids for row in candidate["model_usage"])
    assert all(ref["record_id"] in sent_ids for node in candidate["paragraphs"]
               for ref in node.get("source_refs", []))
    assert ok(client.get(f"/api/projects/{pid}/reports/{rid}"))["version"] == old["version"]
    assert audits(client, pid, rid) == []


def test_optional_style_is_sent_only_after_selection_and_only_as_safe_fields(client, monkeypatch):
    pid, rid, pack, approved = scenario(client)
    without = ok(model_input(client, pid, rid, "S4", approved))["model_input"]
    assert not any(item["role"] == "style" for item in without["materials"])
    style = next(item for item in pack["items"] if item["role"] == "style")
    selected = [*approved, style["item_id"]]
    snapshot = ok(model_input(client, pid, rid, "S4", selected))
    with_style = snapshot["model_input"]
    styles = [item for item in with_style["materials"] if item["role"] == "style"]
    assert len(styles) == 1 and styles[0]["item_id"] == style["item_id"]
    content = styles[0]["content"]
    assert isinstance(content, dict) and "tone" in content and "person" in content
    serialized = json.dumps(content, ensure_ascii=False).lower()
    assert not any(word in serialized for word in ("禾进装备", "报价", "术语表", "历史判断", "term_index"))
    sent = []
    def call(task, messages, max_tokens):
        sent.append(sent_payload(messages))
        return model_result(s4_answer(sent[-1]))
    monkeypatch.setattr(writing_model, "chat_json", call)
    ok(preview(client, pid, rid, "S4", selected, snapshot["input_sha256"]))
    assert sent == [with_style]


def test_zero_is_valid_missing_is_refused_and_old_hash_does_not_call_model(client, monkeypatch):
    pid, rid, _, approved = scenario(client, demand="0")
    snapshot = ok(model_input(client, pid, rid, "S4", approved))
    values = {fact["key"]: fact["value"] for fact in snapshot["model_input"]["facts"]}
    assert values["first_year_demand"] == values["planned_sales"] == "0"
    calls = []
    def call(task, messages, max_tokens):
        sent = sent_payload(messages)
        calls.append(sent)
        return model_result(s4_answer(sent))
    monkeypatch.setattr(writing_model, "chat_json", call)
    assert ok(preview(client, pid, rid, "S4", approved, snapshot["input_sha256"]))["paragraphs"]
    assert len(calls) == 1
    set_fact(client, pid, "first_year_demand", "200000")
    stale = preview(client, pid, rid, "S4", approved, snapshot["input_sha256"])
    assert stale.status_code == 409, stale.text
    assert len(calls) == 1 and audits(client, pid, rid) == []

    pid2, rid2, _, approved2 = scenario(client, demand=None)
    missing = model_input(client, pid2, rid2, "S4", approved2)
    if missing.status_code == 200:
        denied = preview(client, pid2, rid2, "S4", approved2, missing.json()["input_sha256"])
        assert denied.status_code >= 400, denied.text
    else:
        assert missing.status_code == 409, missing.text
    assert len(calls) == 1


def test_confirm_persists_signed_model_audit_and_preview_export(client, monkeypatch):
    pid, rid, _, approved = scenario(client)
    snapshot = ok(model_input(client, pid, rid, "S4", approved))
    monkeypatch.setattr(writing_model, "chat_json", lambda task, messages, max_tokens:
                        model_result(s4_answer(sent_payload(messages))))
    candidate = ok(preview(client, pid, rid, "S4", approved, snapshot["input_sha256"]))
    old = ok(client.get(f"/api/projects/{pid}/reports/{rid}"))["version"]
    result = ok(client.post(f"/api/projects/{pid}/reports/{rid}/writing/commit", json=candidate))
    assert result["report"]["version"] == old + 1
    assert result["model_audit"]["input_sha256"] == snapshot["input_sha256"]
    assert result["model_audit"]["schema_version"] == "writing-model-audit/v1"
    records = audits(client, pid, rid)
    assert len(records) == 1
    record = records[0]
    assert record["event_id"] == result["event_id"]
    assert record["report_version"] == old + 1 and record["section_id"] == "S4"
    assert record["model_audit"]["model_input"] == snapshot["model_input"]
    assert record["model_audit"]["model_usage"] == candidate["model_usage"]
    assert record["model_audit"]["model_call"] == candidate["model_call"]
    exported = client.get(f"/api/projects/{pid}/reports/{rid}/export?level=preview")
    assert exported.status_code == 200, exported.text
    with ZipFile(BytesIO(exported.content)) as archive:
        audit = json.loads(archive.read("audit.json"))
    assert audit["writing_model_audits"] == records


def test_tampered_preview_or_model_input_cannot_commit(client, monkeypatch):
    pid, rid, _, approved = scenario(client)
    snapshot = ok(model_input(client, pid, rid, "S4", approved))
    monkeypatch.setattr(writing_model, "chat_json", lambda task, messages, max_tokens:
                        model_result(s4_answer(sent_payload(messages))))
    candidate = ok(preview(client, pid, rid, "S4", approved, snapshot["input_sha256"]))
    old = ok(client.get(f"/api/projects/{pid}/reports/{rid}"))["version"]
    for change in ("paragraphs", "model_input", "model_usage", "expected_input_sha256"):
        tampered = copy.deepcopy(candidate)
        if change == "paragraphs":
            tampered["paragraphs"][1]["children"][0]["text"] += "未经确认。"
        elif change == "model_input":
            tampered["model_input"]["facts"][0]["value"] = "999"
        elif change == "model_usage":
            tampered["model_usage"][0]["source_item_ids"] = ["forged"]
        else:
            tampered["expected_input_sha256"] = "f" * 64
        denied = client.post(f"/api/projects/{pid}/reports/{rid}/writing/commit", json=tampered)
        assert denied.status_code >= 400, (change, denied.text)
        assert ok(client.get(f"/api/projects/{pid}/reports/{rid}"))["version"] == old
        assert audits(client, pid, rid) == []


@pytest.mark.parametrize("failure", [
    "unknown_source", "empty_source", "unsupported_number", "old_supplier", "false_order",
    "stale_condition", "missing_claim", "invalid_structure",
])
def test_model_hallucination_or_missing_provenance_is_rejected(client, monkeypatch, failure):
    pid, rid, _, approved = scenario(client)
    snapshot = ok(model_input(client, pid, rid, "S4", approved))
    answer = s4_answer(snapshot["model_input"])
    first = answer["paragraphs"][0]
    if failure == "unknown_source":
        first["source_item_ids"] = ["not-sent"]
    elif failure == "empty_source":
        first["source_item_ids"] = []
    elif failure == "unsupported_number":
        first["text"] += "另有999套订单。"
    elif failure == "old_supplier":
        first["text"] += "由禾进装备供应。"
    elif failure == "false_order":
        first["text"] += "需求已经形成已签订单。"
    elif failure == "stale_condition":
        first["text"] += "需求低于能力。"
    elif failure == "missing_claim":
        answer["paragraphs"] = [first]
    else:
        answer = {"paragraphs": "not-a-list"}
    monkeypatch.setattr(writing_model, "chat_json", lambda *args, **kwargs: model_result(answer))
    old = ok(client.get(f"/api/projects/{pid}/reports/{rid}"))["version"]
    denied = preview(client, pid, rid, "S4", approved, snapshot["input_sha256"])
    assert denied.status_code >= 400, (failure, denied.text)
    assert ok(client.get(f"/api/projects/{pid}/reports/{rid}"))["version"] == old
    assert audits(client, pid, rid) == []


def test_model_failure_does_not_claim_a_fallback_candidate(client, monkeypatch):
    pid, rid, _, approved = scenario(client)
    snapshot = ok(model_input(client, pid, rid, "S4", approved))
    def unavailable(*args, **kwargs):
        raise ValueError("模型服务不可用")
    monkeypatch.setattr(writing_model, "chat_json", unavailable)
    old = ok(client.get(f"/api/projects/{pid}/reports/{rid}"))["version"]
    denied = preview(client, pid, rid, "S4", approved, snapshot["input_sha256"])
    assert denied.status_code == 503, denied.text
    assert ok(client.get(f"/api/projects/{pid}/reports/{rid}"))["version"] == old
    assert audits(client, pid, rid) == []


def test_s71_new_supplier_and_price_never_inherit_historical_identity(client, monkeypatch):
    pid, rid, _, approved = scenario(client, section="S7.1")
    snapshot = ok(model_input(client, pid, rid, "S7.1", approved))
    data = snapshot["model_input"]
    assert {fact["key"] for fact in data["facts"]} == {"supplier_name", "equipment_unit_price"}
    assert "禾进装备" not in json.dumps(data, ensure_ascii=False)
    skeleton = material_id(data, "skeleton")
    answer = {"paragraphs": [{
        "text": "本项目设备供应商为新拓设备，自动焊接线购置单价为1,234.50万元/条。",
        "fact_keys": ["supplier_name", "equipment_unit_price"],
        "source_item_ids": [skeleton],
    }]}
    monkeypatch.setattr(writing_model, "chat_json", lambda *args, **kwargs: model_result(answer))
    candidate = ok(preview(client, pid, rid, "S7.1", approved, snapshot["input_sha256"]))
    serialized = json.dumps(candidate["paragraphs"], ensure_ascii=False)
    assert "新拓设备" in serialized and "1,234.50" in serialized
    assert "禾进装备" not in serialized and "12,345,000" not in serialized
    assert all(set(item["source_item_ids"]) <= {material["item_id"] for material in data["materials"]}
               for item in candidate["model_usage"])
