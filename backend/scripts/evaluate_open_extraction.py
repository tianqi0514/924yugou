"""对固定公开报告快照复核开放式候选，不调用模型或修改项目数据库。"""

from __future__ import annotations

import hashlib
import json
import re
from collections import Counter
from decimal import Decimal, InvalidOperation
from pathlib import Path

from app.document_pipeline import source_supports


ROOT = Path(__file__).resolve().parents[2]
FIXTURES = ROOT / "test-fixtures" / "public-reports"
OUTPUT = FIXTURES / "baseline-extraction"
SNAPSHOT = OUTPUT / "open-extraction-snapshot-2026-09-24.json"
REVIEW = OUTPUT / "open-extraction-review-2026-09-24.json"
REPORT = OUTPUT / "open-extraction-evaluation-2026-09-24.json"
CHAIN = OUTPUT / "chain-validation-2026-09-24.json"
CATEGORIES = ("usable", "needs_context", "needs_edit", "unsupported", "duplicate")


def compact(value: object) -> str:
    """合并源文件排版空白，保留文本中的实际字符。"""
    return re.sub(r"\s+", "", str(value))


def same_value(actual: str, expected: str) -> bool:
    """比较事实值，数值忽略无意义的尾零。

    Args:
        actual: 候选中的值。
        expected: 人工核对点的值。

    Returns:
        两值在本轮评分口径下是否相同。
    """
    try:
        return Decimal(compact(actual).replace(",", "")) == Decimal(compact(expected).replace(",", ""))
    except InvalidOperation:
        return compact(actual) == compact(expected)


def same_unit(actual: str, expected: str) -> bool:
    """只合并本轮样本中无争议的面积、月份写法。"""
    aliases = {"平方米": "㎡", "m2": "㎡", "m²": "㎡", "月": "个月"}
    left, right = compact(actual), compact(expected)
    return aliases.get(left, left) == aliases.get(right, right)


def gold_records(gold: dict) -> dict[str, dict]:
    """读取先于本轮开放式抽取建立的人工核对点。"""
    if "fields" in gold:
        return {item["id"]: item for item in gold["fields"]}
    return {item["id"]: item for item in gold["fact_checkpoints"]}


def checked_match(candidate: dict, gold: dict, segment: dict) -> None:
    """核实人工语义配对至少在值、单位和引用片段上成立。

    Args:
        candidate: 固定快照中的模型候选。
        gold: 既有人工核对点。
        segment: 候选引用的原文片段。

    Raises:
        ValueError: 配对的值、单位或来源位置不成立。
    """
    if "expected" in gold:
        expected = compact(gold["expected"]).lower()
        actual = compact(candidate["value"] + candidate["unit"]).lower()
        if actual != expected:
            raise ValueError(f"事故核对点与候选值不符：{gold['id']}")
    elif not same_value(candidate["value"], gold["value"]) or not same_unit(candidate["unit"], gold["unit"]):
        raise ValueError(f"字段核对点与候选值或单位不符：{gold['id']}")
    if not source_supports(candidate["value"], segment["text"]):
        raise ValueError(f"核对点所引用的片段不包含该值：{gold['id']}")


def evaluate(snapshot: dict, review: dict) -> dict:
    """计算限定页内的值命中、单片段可核对命中与候选审核分布。

    Args:
        snapshot: 项目接口读取的固定公开报告候选与原文。
        review: 人工逐条配对和分类。

    Returns:
        每份报告及总体指标、遗漏原因和需处理候选。

    Raises:
        ValueError: 样本摘要、标注覆盖或人工映射不一致。
    """
    if review.get("schema_version") != 1:
        raise ValueError("不支持的人工复核格式")
    documents = {item["filename"]: item for item in snapshot["documents"]}
    if set(documents) != {item["source_file"] for item in review["documents"]}:
        raise ValueError("快照与复核报告集合不一致")
    output = {"captured_at": snapshot["captured_at"], "scope": "已抽取的四个 PDF 物理页；仅评价明确存在的 found 核对点", "documents": []}
    for specification in review["documents"]:
        filename = specification["source_file"]
        item = documents[filename]
        original_hash = hashlib.sha256((FIXTURES / filename).read_bytes()).hexdigest()
        gold = json.loads((FIXTURES / specification["gold_file"]).read_text(encoding="utf-8"))
        if item["sha256"] != original_hash or (gold.get("sha256") and gold["sha256"] != original_hash):
            raise ValueError(f"原件与固定快照摘要不一致：{filename}")
        source_gold = gold_records(gold)
        requested = set(specification["gold_ids"])
        matched, missed = specification["gold_matches"], specification["gold_misses"]
        if requested != set(matched) | set(missed) or set(matched) & set(missed):
            raise ValueError(f"核对点没有被完整且唯一地判定：{filename}")
        if any(field not in source_gold or source_gold[field].get("status", "found") != "found" for field in requested):
            raise ValueError(f"核对点不在既有明确事实中：{filename}")
        pages = {str(page["page"]): page for page in item["pages"]}
        if set(pages) != set(specification["candidate_reviews"]):
            raise ValueError(f"页码与复核范围不一致：{filename}")
        selected_text = "".join(segment["text"] for page in pages.values() for segment in page["segments"])
        for field_id in requested:
            expected = source_gold[field_id].get("value", source_gold[field_id].get("expected"))
            if not source_supports(str(expected), selected_text) and compact(expected).lower() not in compact(selected_text).lower():
                raise ValueError(f"人工核对点的答案不在已抽取页内：{filename} {field_id}")
        categories: Counter[str] = Counter()
        first_categories: Counter[str] = Counter()
        assigned_by_page = {}
        issues = []
        for page_no, page in pages.items():
            assessments = specification["candidate_reviews"][page_no]
            assigned = {}
            first_count = assessments["first_pass_count"]
            if not 0 <= first_count <= len(page["candidates"]):
                raise ValueError(f"首次抽取数量不在候选范围内：{filename} 第 {page_no} 页")
            for category in CATEGORIES:
                group = assessments[category]
                indices = group if isinstance(group, list) else group.keys()
                for raw_index in indices:
                    index = int(raw_index)
                    if index in assigned or not 1 <= index <= len(page["candidates"]):
                        raise ValueError(f"候选分类重叠或序号错误：{filename} 第 {page_no} 页第 {index} 条")
                    assigned[index] = category
                    categories[category] += 1
                    if index <= first_count:
                        first_categories[category] += 1
                    if category != "usable":
                        candidate = page["candidates"][index - 1]
                        issues.append({"page": int(page_no), "candidate_index": index, "label": candidate["label"],
                                       "value": candidate["value"], "category": category, "reason": group[raw_index]})
            if set(assigned) != set(range(1, len(page["candidates"]) + 1)):
                raise ValueError(f"候选尚未逐条复核：{filename} 第 {page_no} 页")
            assigned_by_page[page_no] = assigned
        direct_hits = 0
        first_value_hits = first_direct_hits = 0
        for field_id, address in matched.items():
            page_no, index_text = address.split(":")
            page = pages[page_no]
            index = int(index_text)
            if index not in assigned_by_page[page_no]:
                raise ValueError(f"核对点候选序号不存在：{filename} {field_id}")
            candidate = page["candidates"][index - 1]
            category = assigned_by_page[page_no][index]
            if category not in ("usable", "needs_context"):
                raise ValueError(f"核对点映射到不准确或重复候选：{filename} {field_id}")
            refs = {part["ref"]: part for part in page["segments"]}
            if candidate["source_ref"] not in refs:
                raise ValueError(f"候选引用不在当前页：{filename} {field_id}")
            checked_match(candidate, source_gold[field_id], refs[candidate["source_ref"]])
            direct_hits += category == "usable"
            if index <= specification["candidate_reviews"][page_no]["first_pass_count"]:
                first_value_hits += 1
                first_direct_hits += category == "usable"
        candidate_total = sum(len(page["candidates"]) for page in pages.values())
        gate_pass = sum(bool(candidate["source_valid"]) for page in pages.values() for candidate in page["candidates"])
        first_gate_pass = sum(bool(candidate["source_valid"]) for page_no, page in pages.items()
                              for candidate in page["candidates"][:specification["candidate_reviews"][page_no]["first_pass_count"]])
        if sum(categories.values()) != candidate_total:
            raise ValueError(f"候选数量不一致：{filename}")
        output["documents"].append({
            "source_file": filename, "pages": sorted(int(number) for number in pages), "sha256": original_hash,
            "gold_total": len(requested), "value_hits": len(matched), "direct_hits": direct_hits,
            "gold_misses": missed, "candidate_total": candidate_total, "source_gate_pass": gate_pass,
            "review_counts": dict(categories), "candidate_issues": issues,
            "first_pass": {"gold_total": len(requested), "value_hits": first_value_hits,
                           "direct_hits": first_direct_hits,
                           "candidate_total": sum(specification["candidate_reviews"][page_no]["first_pass_count"] for page_no in pages),
                           "source_gate_pass": first_gate_pass, "review_counts": dict(first_categories)},
        })
    output["total"] = {
        "gold_total": sum(item["gold_total"] for item in output["documents"]),
        "value_hits": sum(item["value_hits"] for item in output["documents"]),
        "direct_hits": sum(item["direct_hits"] for item in output["documents"]),
        "candidate_total": sum(item["candidate_total"] for item in output["documents"]),
        "source_gate_pass": sum(item["source_gate_pass"] for item in output["documents"]),
        "review_counts": dict(sum((Counter(item["review_counts"]) for item in output["documents"]), Counter())),
        "first_pass": {
            "gold_total": sum(item["first_pass"]["gold_total"] for item in output["documents"]),
            "value_hits": sum(item["first_pass"]["value_hits"] for item in output["documents"]),
            "direct_hits": sum(item["first_pass"]["direct_hits"] for item in output["documents"]),
            "candidate_total": sum(item["first_pass"]["candidate_total"] for item in output["documents"]),
            "source_gate_pass": sum(item["first_pass"]["source_gate_pass"] for item in output["documents"]),
            "review_counts": dict(sum((Counter(item["first_pass"]["review_counts"]) for item in output["documents"]), Counter())),
        },
    }
    return output


def verify_initial_candidate_ids(snapshot: dict, review: dict, chain: dict) -> None:
    """用首次全链路记录核对首次抽取的候选边界。

    Args:
        snapshot: 当前固定快照。
        review: 含首次抽取数量的人工复核。
        chain: 首次全链路验证保存的原始候选。

    Raises:
        ValueError: 首次候选列表与当前快照前缀不一致。
    """
    by_page = {(doc["filename"], page["page"]): page for doc in snapshot["documents"] for page in doc["pages"]}
    by_review = {(doc["source_file"], int(number)): spec for doc in review["documents"]
                 for number, spec in doc["candidate_reviews"].items()}
    for item in chain["items"]:
        address = (item["filename"], item["page"])
        if address not in by_page:
            continue
        expected = [candidate["id"] for candidate in item["candidates"]]
        actual = [candidate["id"] for candidate in by_page[address]["candidates"][:by_review[address]["first_pass_count"]]]
        if actual != expected:
            raise ValueError(f"首次抽取候选 ID 与原始记录不一致：{address}")


def main() -> None:
    """运行固定样本评估并输出无密钥的可复核结果。"""
    snapshot = json.loads(SNAPSHOT.read_text(encoding="utf-8"))
    review = json.loads(REVIEW.read_text(encoding="utf-8"))
    chain = json.loads(CHAIN.read_text(encoding="utf-8"))
    verify_initial_candidate_ids(snapshot, review, chain)
    result = evaluate(snapshot, review)
    REPORT.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"documents": [{key: value for key, value in item.items() if key not in ("candidate_issues", "gold_misses")}
                                    for item in result["documents"]], "total": result["total"]}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
