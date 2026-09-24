"""Reproducible NEXT-02 chapter-material verification on an isolated QA database.

Run with DATABASE_URL pointing to report_platform_test. This script creates one
new project and report, but never resets a table or edits another project.
"""

from __future__ import annotations

import argparse
import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from sqlalchemy.engine import make_url


DATABASE_URL = os.environ.get("DATABASE_URL", "")
try:
    database_name = make_url(DATABASE_URL).database if DATABASE_URL else None
except Exception:
    database_name = None
if database_name != "report_platform_test":
    raise SystemExit("拒绝运行：DATABASE_URL 必须明确指向 report_platform_test")

from fastapi.testclient import TestClient  # noqa: E402

from app.db import engine  # noqa: E402
from app.main import app  # noqa: E402

assert engine.url.database == "report_platform_test", "拒绝连接非测试数据库"


ROOT = Path(__file__).resolve().parents[1]
CORPUS_ID = "cy_tray_20260918"
CORPUS_VERSION = "v2"
CHAPTERS = ("S4", "S7.1", "S5.2")
FIXED_EXAMPLE_INPUTS: dict[str, dict[str, Any]] = {
    "S4": {"N017": 300000, "N034": 254016},
    "S7.1": {"supplier_name": "新拓设备", "N080": "1234.50"},
    "S5.2": {},
}
REQUIRED_BINDINGS = {
    "N017": "first_year_demand", "N034": "qualified_capacity",
    "N035": "planned_sales", "supplier_name": "supplier_name",
    "N080": "equipment_unit_price",
}


def checked(response, action: str) -> dict:
    if response.status_code not in (200, 201):
        raise RuntimeError(f"{action} 失败：HTTP {response.status_code}")
    return response.json()


def snapshot(client: TestClient, project_id: str, report_id: str, section_id: str) -> dict:
    project = checked(client.get(f"/api/projects/{project_id}"), "读取项目")
    facts = checked(client.get(f"/api/projects/{project_id}/facts"), "读取事实")
    report = checked(client.get(f"/api/projects/{project_id}/reports/{report_id}"), "读取报告")
    materials = checked(client.get(f"/api/projects/{project_id}/writing/materials",
                                   params={"section_id": section_id}), "读取材料配置")
    return {"project_version": project["version"], "facts": facts["facts"],
            "report_version": report["version"], "report_content": report["content"],
            "config_version": materials["config_version"]}


def simulate_without_writes(client: TestClient, project_id: str, report_id: str,
                            section_id: str, request: dict) -> dict:
    before = snapshot(client, project_id, report_id, section_id)
    result = checked(client.post(
        f"/api/projects/{project_id}/writing/materials/{section_id}/simulate", json=request,
    ), f"模拟 {section_id}")
    after = snapshot(client, project_id, report_id, section_id)
    assert before == after, f"{section_id} 模拟改动了数据库记录"
    assert result["simulation_only"] is True
    return result


def make_qa_project(client: TestClient) -> tuple[str, str]:
    name = "NEXT-02 材料应用 QA " + datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    project_id = checked(client.post("/api/projects", json={"name": name}), "创建 QA 项目")["id"]
    assert checked(client.get(f"/api/projects/{project_id}/facts"), "读取初始事实")["facts"] == []
    checked(client.put(f"/api/projects/{project_id}/writing/reference", json={
        "corpus_id": CORPUS_ID, "corpus_version": CORPUS_VERSION,
        "target_report_type": "feasibility", "accepted": True,
    }), "选择只读语料")
    report_id = checked(client.post(f"/api/projects/{project_id}/reports", json={
        "title": "NEXT-02 材料应用验证稿",
    }), "创建 QA 报告")["id"]
    checked(client.post(f"/api/projects/{project_id}/writing/setup", json={}), "创建空白验证字段")
    checked(client.put(f"/api/projects/{project_id}/writing/bindings", json={
        "bindings": REQUIRED_BINDINGS,
    }), "绑定项目事实槽位")
    return project_id, report_id


def verify_all_categories(client: TestClient, project_id: str, report_id: str) -> dict:
    observations = []
    chapter_summaries = []
    for section_id in CHAPTERS:
        catalog = checked(client.get(f"/api/projects/{project_id}/writing/materials",
                                     params={"section_id": section_id}), "读取 30 类目录")
        assert len(catalog["items"]) == 30
        assert sum(group["count"] for group in catalog["groups"]) == 30
        source_by_id = {item["category_id"]: item for item in catalog["items"]}
        category_ids = [item["category_id"] for item in catalog["items"]]
        assert len(category_ids) == len(set(category_ids)) == 30
        configured_baseline = None
        for offset in range(0, 30, 6):
            selected = category_ids[offset:offset + 6]
            result = simulate_without_writes(client, project_id, report_id, section_id, {
                "input_mode": "example", "slot_values": FIXED_EXAMPLE_INPUTS[section_id],
                "selected_categories": selected,
            })
            assert len(result["experiments"]) == 6
            assert {entry["category_id"] for entry in result["experiments"]} == set(selected)
            if configured_baseline is None:
                configured_baseline = result["configured"]
            else:
                assert result["configured"]["text"] == configured_baseline["text"]
                assert result["configured"]["used_records"] == configured_baseline["used_records"]
            for experiment in result["experiments"]:
                metadata = source_by_id[experiment["category_id"]]
                assert set(experiment["used_records"]).issubset(set(metadata["record_ids"]))
                observations.append({
                    "section_id": section_id,
                    "category_id": experiment["category_id"],
                    "status": experiment["status"], "reason": experiment["reason"],
                    "before_text": experiment["before_text"], "after_text": experiment["after_text"],
                    "used_record_ids": experiment["used_records"],
                    "chapter_record_ids": metadata["record_ids"],
                    "configured_mode": metadata["configured_mode"],
                    "effective_role": metadata["effective_role"],
                })
        chapter_summaries.append({
            "section_id": section_id, "status": configured_baseline["status"],
            "text": configured_baseline["text"],
            "used_categories": configured_baseline["used_categories"],
            "used_record_ids": configured_baseline["used_records"],
            "issues": configured_baseline["issues"],
        })
    assert len(observations) == 90
    assert len({(row["section_id"], row["category_id"]) for row in observations}) == 90
    return {"chapters": chapter_summaries, "removals": observations,
            "removal_count": len(observations), "simulation_only": True}


def verify_fixed_cases(client: TestClient, project_id: str, report_id: str) -> list[dict]:
    cases = []
    for name, section_id, values, expected, expected_status in (
        ("S4-首年较小值", "S4", {"N017": 300000, "N034": 254016}, "254016", "ready"),
        ("S4-有效零值", "S4", {"N017": 0, "N034": 254016}, "0", "ready"),
        ("S4-输入缺失", "S4", {"N017": None, "N034": 254016}, None, "blocked"),
        ("S4-条件变化", "S4", {"N017": 100000, "N034": 254016}, "100000", "ready"),
        ("S7.1-新供应商", "S7.1", {"supplier_name": "新拓设备", "N080": "1234.50"},
         "新拓设备", "ready"),
    ):
        result = simulate_without_writes(client, project_id, report_id, section_id, {
            "input_mode": "example", "slot_values": values, "selected_categories": [],
        })
        candidate = result["configured"]
        assert candidate["status"] == expected_status, name
        if section_id == "S4":
            sales = next(fact for fact in result["facts"] if fact["slot_id"] == "N035")
            assert sales["value"] == expected, name
            assert "需求低于能力" not in candidate["text"]
            if values["N017"] is None:
                assert sales["status"] == "UNEVALUABLE"
        else:
            assert expected in candidate["text"]
            assert "禾进装备" not in candidate["text"]
        cases.append({"name": name, "status": candidate["status"],
                      "text": candidate["text"], "facts": result["facts"], "trace": result["trace"]})
    return cases


def verify_saved_configuration(client: TestClient, project_id: str, report_id: str) -> dict:
    endpoint = f"/api/projects/{project_id}/writing/materials/S4"
    before = snapshot(client, project_id, report_id, "S4")
    saved = checked(client.put(endpoint, json={"base_version": before["config_version"], "settings": [
        {"category_id": "CAT-18", "mode": "review"},
        {"category_id": "CAT-26", "mode": "exclude"},
    ]}), "保存材料配置")
    assert saved["config_version"] == before["config_version"] + 1
    current = checked(client.get(f"/api/projects/{project_id}/writing/materials",
                                 params={"section_id": "S4"}), "读取保存配置")
    modes = {item["category_id"]: item["configured_mode"] for item in current["items"]}
    assert modes["CAT-18"] == "review" and modes["CAT-26"] == "exclude"
    assert checked(client.get(f"/api/projects/{project_id}/reports/{report_id}"), "读取报告")["version"] == before["report_version"]
    restored = checked(client.put(endpoint, json={"base_version": saved["config_version"], "settings": [
        {"category_id": "CAT-18", "mode": "auto"},
        {"category_id": "CAT-26", "mode": "auto"},
    ]}), "恢复默认配置")
    assert restored["config_version"] == saved["config_version"] + 1
    assert all(item["configured_mode"] == "auto" for item in restored["items"])
    after = snapshot(client, project_id, report_id, "S4")
    assert after["facts"] == before["facts"]
    assert after["report_version"] == before["report_version"]
    assert after["report_content"] == before["report_content"]
    return {"save_version": saved["config_version"], "restore_version": restored["config_version"],
            "project_version_after_restore": restored["project_version"], "restored": True}


def set_project_fact(client: TestClient, project_id: str, key: str, value: str) -> None:
    change = {"fact_key": key, "value": value, "source": "NEXT-02 合成 QA 输入", "reason": "模型待审候选验证"}
    preview = checked(client.post(f"/api/projects/{project_id}/changes/preview", json=change), "预览事实")
    checked(client.post(f"/api/projects/{project_id}/changes/commit", json={
        **change, "base_version": preview["base_version"], "preview_token": preview["preview_token"],
    }), "确认事实")


def verify_real_model(client: TestClient, project_id: str, report_id: str, output_dir: Path) -> dict:
    for key, value in (("first_year_demand", "300000"), ("qualified_capacity", "254016"),
                       ("supplier_name", "新拓设备"), ("equipment_unit_price", "1234.50")):
        set_project_fact(client, project_id, key, value)
    attempts = []
    committed = 0
    for section_id in ("S4", "S7.1"):
        pack = checked(client.get(f"/api/projects/{project_id}/writing/packages/{section_id}"),
                       "读取写作包")
        approved = [item["item_id"] for item in pack["items"]
                    if item["decision"] == "selectable" and item.get("required")]
        request = {"section_id": section_id, "approved_item_ids": approved, "mode": "model"}
        prepared_response = client.post(
            f"/api/projects/{project_id}/reports/{report_id}/writing/model-input", json=request,
        )
        if prepared_response.status_code != 200:
            attempts.append({"section_id": section_id, "status": "unavailable",
                             "message": f"模型输入未就绪（HTTP {prepared_response.status_code}）"})
            continue
        prepared = prepared_response.json()
        before = snapshot(client, project_id, report_id, section_id)
        preview_response = client.post(
            f"/api/projects/{project_id}/reports/{report_id}/writing/preview",
            json={**request, "expected_input_sha256": prepared["input_sha256"]},
        )
        after = snapshot(client, project_id, report_id, section_id)
        assert before == after, f"{section_id} 模型预览改动了项目或报告"
        if preview_response.status_code != 200:
            attempts.append({"section_id": section_id, "status": "failed",
                             "message": f"模型候选未通过校验（HTTP {preview_response.status_code}）"})
            continue
        preview = preview_response.json()
        assert preview["mode"] == "model" and preview["input_sha256"] == prepared["input_sha256"]
        # Closing this candidate is the cancellation path: no commit call follows.
        canceled_report = checked(client.get(f"/api/projects/{project_id}/reports/{report_id}"), "核对取消结果")
        assert canceled_report["version"] == before["report_version"]
        second_response = client.post(
            f"/api/projects/{project_id}/reports/{report_id}/writing/preview",
            json={**request, "expected_input_sha256": prepared["input_sha256"]},
        )
        if second_response.status_code != 200:
            attempts.append({"section_id": section_id, "status": "failed",
                             "message": f"重新预览未通过校验（HTTP {second_response.status_code}）"})
            continue
        second = second_response.json()
        committed_response = client.post(
            f"/api/projects/{project_id}/reports/{report_id}/writing/commit", json=second,
        )
        if committed_response.status_code != 200:
            attempts.append({"section_id": section_id, "status": "failed",
                             "message": f"待审候选确认失败（HTTP {committed_response.status_code}）"})
            continue
        committed += 1
        saved = committed_response.json()
        attempts.append({"section_id": section_id, "status": "committed_working_draft",
                         "message": "验收脚本已确认候选，仍待人工核对",
                         "report_version": saved["report"]["version"], "event_id": saved["event_id"]})
    export = {"status": "skipped", "message": "没有已确认的模型候选"}
    if committed:
        response = client.get(f"/api/projects/{project_id}/reports/{report_id}/export",
                              params={"level": "preview"})
        if response.status_code == 200:
            path = output_dir / "model-preview.zip"
            path.write_bytes(response.content)
            export = {"status": "created", "path": os.path.relpath(path, ROOT),
                      "byte_size": len(response.content)}
        else:
            export = {"status": "blocked", "message": f"预审导出受阻（HTTP {response.status_code}）"}
    return {"attempts": attempts, "confirmed_count": committed, "export": export}


def main() -> None:
    parser = argparse.ArgumentParser(description="在 report_platform_test 中验证 30 类材料的章节应用")
    parser.add_argument("--output", default="tmp/material-application/verification.json",
                        help="结果 JSON 路径，或以 / 结尾的目录")
    parser.add_argument("--real-model", action="store_true", help="可选：调用当前配置的真实文本模型")
    args = parser.parse_args()
    output = Path(args.output).expanduser()
    if not output.is_absolute():
        output = ROOT / output
    if args.output.endswith(("/", os.sep)) or output.is_dir():
        output /= "verification.json"
    output.parent.mkdir(parents=True, exist_ok=True)
    with TestClient(app) as client:
        project_id, report_id = make_qa_project(client)
        application = verify_all_categories(client, project_id, report_id)
        fixed_cases = verify_fixed_cases(client, project_id, report_id)
        configuration = verify_saved_configuration(client, project_id, report_id)
        model = verify_real_model(client, project_id, report_id, output.parent) if args.real_model else {
            "status": "not_requested",
        }
        project = checked(client.get(f"/api/projects/{project_id}"), "读取最终项目")
        report = checked(client.get(f"/api/projects/{project_id}/reports/{report_id}"), "读取最终报告")
    overall_status = "passed" if not args.real_model or model.get("confirmed_count") == 2 else "partial"
    result = {
        "status": overall_status,
        "schema_version": "material-application-verification/v1",
        "database": engine.url.database,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "fixed_input_reference": "docs/NEXT-01模型写作包验收.md；S5.2 使用 X001 双来源历史冲突",
        "project": {"id": project_id, "name": project["name"], "version": project["version"]},
        "report": {"id": report_id, "title": report["title"], "version": report["version"]},
        "corpus": {"id": CORPUS_ID, "version": CORPUS_VERSION},
        "application": application, "fixed_cases": fixed_cases,
        "configuration": configuration, "real_model": model,
    }
    output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"status": overall_status, "output": str(output),
                      "project_id": project_id, "report_id": report_id,
                      "removal_count": application["removal_count"],
                      "real_model": model.get("status", "attempted")}, ensure_ascii=False))


if __name__ == "__main__":
    main()
