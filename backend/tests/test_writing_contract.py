"""NEXT-02A: typed information equivalence is a falsifiable QA experiment."""

from __future__ import annotations

import os
from copy import deepcopy
from decimal import localcontext
from types import SimpleNamespace

os.environ["DATABASE_URL"] = "postgresql+psycopg://report:local_development_only@127.0.0.1:55432/report_platform_test"

import pytest
from fastapi.testclient import TestClient

from app.db import engine
from app.main import app
from app.writing_contract import (SUPPORTED, TypedContractError, _negative_mutation,
                                  _projection_matches, render_typed, typed_fixture)


@pytest.fixture
def input_state():
    return {
        "first_year_demand": {"value": "300000", "status": "PROVIDED", "unit": "套", "data_type": "integer"},
        "qualified_capacity": {"value": "254016", "status": "PROVIDED", "unit": "套", "data_type": "integer"},
        "planned_sales": {"value": "254016", "status": "COMPUTED", "unit": "套", "data_type": "integer"},
        "supplier_name": {"value": "新拓设备", "status": "PROVIDED", "unit": "", "data_type": "text"},
        "equipment_unit_price": {"value": "1234.50", "status": "PROVIDED", "unit": "万元/条", "data_type": "decimal"},
    }


@pytest.fixture
def bindings():
    return {"N017": "first_year_demand", "N034": "qualified_capacity", "N035": "planned_sales",
            "supplier_name": "supplier_name", "N080": "equipment_unit_price"}


@pytest.fixture
def min_rule():
    return [SimpleNamespace(target_key="planned_sales",
                            expression="min(first_year_demand, qualified_capacity)")]


def test_each_claimed_category_has_a_failing_information_mutation(input_state, bindings, min_rule):
    """A renamed record ID alone could not make this test pass."""
    for section_id, categories in SUPPORTED.items():
        original = render_typed(typed_fixture(section_id), input_state, min_rule, bindings)
        assert original["text"]
        for number in categories:
            changed = typed_fixture(section_id)
            _negative_mutation(changed, number)
            with pytest.raises(TypedContractError):
                render_typed(changed, input_state, min_rule, bindings)


def test_typed_min_uses_project_values_and_preserves_zero_missing(input_state, bindings, min_rule):
    zero = deepcopy(input_state)
    zero["first_year_demand"]["value"] = "0"
    zero["planned_sales"]["value"] = "0"
    output = render_typed(typed_fixture("S4"), zero, min_rule, bindings)
    assert output["fact_values"]["N035"] == "0"
    assert "计划销售量为0套" in output["text"]
    missing = deepcopy(input_state)
    missing["first_year_demand"]["value"] = None
    missing["planned_sales"]["value"] = None
    missing["planned_sales"]["status"] = "UNEVALUABLE"
    with pytest.raises(TypedContractError, match="缺少有效项目事实"):
        render_typed(typed_fixture("S4"), missing, min_rule, bindings)
    with pytest.raises(TypedContractError, match="min"):
        render_typed(typed_fixture("S4"), input_state,
                     [SimpleNamespace(target_key="planned_sales",
                                      expression="max(first_year_demand, qualified_capacity)")], bindings)


def test_typed_supplier_and_conflict_keep_boundaries(input_state, bindings, min_rule):
    supplier = render_typed(typed_fixture("S7.1"), input_state, min_rule, bindings)
    assert "新拓设备" in supplier["text"] and "禾进装备" not in supplier["text"]
    assert "QUOTE_SCOPE_UNVERIFIED" in supplier["boundary_codes"]
    conflict = render_typed(typed_fixture("S5.2"), input_state, min_rule, bindings)
    assert "未解决分歧" in conflict["text"]
    assert "POWER_CONFLICT_UNRESOLVED" in conflict["boundary_codes"]
    assert "暂不作配电适配结论" in conflict["text"]


def test_typed_quote_precision_is_context_independent(input_state, bindings, min_rule):
    with localcontext() as context:
        context.prec = 2
        output = render_typed(typed_fixture("S7.1"), input_state, min_rule, bindings)
    assert "12,345,000元/条" in output["text"]
    invalid = deepcopy(input_state)
    invalid["equipment_unit_price"]["value"] = "1234.567"
    with pytest.raises(TypedContractError, match="报价精度"):
        render_typed(typed_fixture("S7.1"), invalid, min_rule, bindings)


def test_same_record_label_with_wrong_information_is_not_equivalent():
    s4 = typed_fixture("S4")
    assert not _projection_matches("S4", 9, s4, [{"id": "C0052", "subsection": "S4",
                                                  "page": 99, "span": [4381, 4487]}])
    assert not _projection_matches("S4", 13, s4, [{"id": "R006", "target": "N035",
                                                   "deps": ["N017", "N034"], "expr": "max(N017,N034)"}])
    s52 = typed_fixture("S5.2")
    assert not _projection_matches("S5.2", 23, s52, [{"id": "X001", "status": "open",
                                                      "resolution": None, "evidence": ["EV-06A", "EV-06B"],
                                                      "what": "同口径 800kW 与 800kW"}])


def _ok(response):
    assert response.status_code in (200, 201), response.text
    return response.json()


def test_api_equivalent_information_uses_real_db_mapping_without_writes():
    assert engine.url.database == "report_platform_test", "Refusing to use non-test database"
    with TestClient(app) as client:
        project_id = _ok(client.post("/api/projects", json={"name": "NEXT-02A 等价信息合成 QA"}))["id"]
        assert _ok(client.get(f"/api/projects/{project_id}/facts"))["facts"] == []
        _ok(client.put(f"/api/projects/{project_id}/writing/reference", json={
            "corpus_id": "cy_tray_20260918", "corpus_version": "v2",
            "target_report_type": "feasibility", "accepted": True,
        }))
        before = _ok(client.get(f"/api/projects/{project_id}"))["version"]
        tested = []
        values = {"S4": {"N017": 300000, "N034": 254016},
                  "S7.1": {"supplier_name": "新拓设备", "N080": "1234.50"}, "S5.2": {}}
        for section_id, categories in SUPPORTED.items():
            ids = [f"CAT-{number:02d}" for number in sorted(categories)]
            for start in range(0, len(ids), 6):
                result = _ok(client.post(
                    f"/api/projects/{project_id}/writing/materials/{section_id}/simulate",
                    json={"experiment_mode": "equivalent", "input_mode": "example",
                          "slot_values": values[section_id], "selected_categories": ids[start:start + 6]},
                ))
                assert result["experiment_mode"] == "equivalent"
                assert result["simulation_only"] is True
                for experiment in result["experiments"]:
                    tested.append((section_id, experiment["category_id"]))
                    assert experiment["status"] == "equivalent", experiment
                    assert experiment["checks"] == {
                        "text_equal": True, "project_values_equal": True, "boundaries_equal": True,
                        "legacy_refs_reused": False, "source_information_equal": True,
                        "negative_probe_passed": True,
                    }
                    assert experiment["contract"]["schema_version"] == "writing-equivalent/v1"
                    assert experiment["contract"]["source_record_ids"] == experiment["used_records"]
                    assert experiment["contract"]["validation"]["negative_result"] == "blocked"
                    assert experiment["contract"]["simulation_only"] is True
        assert len(tested) == sum(len(numbers) for numbers in SUPPORTED.values()) == 15
        unsupported = _ok(client.post(
            f"/api/projects/{project_id}/writing/materials/S4/simulate",
            json={"experiment_mode": "equivalent", "input_mode": "example",
                  "selected_categories": ["CAT-11"]},
        ))
        assert unsupported["experiments"][0]["status"] == "not_supported"
        assert _ok(client.get(f"/api/projects/{project_id}"))["version"] == before
        assert _ok(client.get(f"/api/projects/{project_id}/facts"))["facts"] == []
