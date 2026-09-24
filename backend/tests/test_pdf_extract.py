"""Document-layer assertions against independently downloaded public PDFs."""

from decimal import Decimal
from pathlib import Path

from app.pdf_extract import extract_pdf
from app.document_pipeline import parse_original, source_supports, table_segments


FIXTURES = Path(__file__).resolve().parents[2] / "test-fixtures" / "public-reports"


def test_beijing_holdout_has_located_chapters_and_recoverable_tables():
    result = extract_pdf(FIXTURES / "01_beijing_feasibility.pdf", page_numbers=[6, 7, 8, 177, 191])
    pages = {page["pdf_page"]: page for page in result["pages"]}
    assert result["pdf_pages"] == 205
    assert any(item["text"] == "第一章 概述" for item in pages[6]["heading_candidates"])
    assert any(item["text"] == "第十章 研究结论与建议" for item in pages[191]["heading_candidates"])

    indicators = next(table for table in pages[7]["table_candidates"] if "技术经济指标表" in (table["caption"] or ""))
    assert (indicators["rows"], indicators["columns"]) == (20, 5)
    assert ["1", "用地总面积", "73431.105", "㎡"] == indicators["cells"][1][:4]
    values = {item["label"]: item for item in pages[7]["table_value_candidates"]}
    assert values["用地总面积"]["value"] == "73431.105"
    assert values["地下机动车位停车位"]["value"] is None
    assert values["地下机动车位停车位"]["status"] == "missing_marker"

    investment = next(table for table in pages[177]["table_candidates"] if "项目总投资汇总表" in (table["caption"] or ""))
    rows = {row[1]: Decimal(row[3]) for row in investment["cells"][1:] if row[3]}
    assert rows["工程费"] + rows["工程建设其他费"] + rows["预备费"] + rows["征地拆迁费"] == rows["总投资"] == Decimal("31255.57")
    assert all(page["text_status"] == "native_text" for page in pages.values())


def test_scanned_audit_requires_ocr_instead_of_empty_success():
    result = extract_pdf(FIXTURES / "03_taizhou_audit.pdf", page_numbers=[2])
    page = result["pages"][0]
    assert page["native_text_chars"] == 0
    assert page["text_status"] == "ocr_required"
    assert page["image_count"] > 0


def test_mixed_eia_document_is_classified_by_page():
    result = extract_pdf(FIXTURES / "02_jiangmen_eia.pdf", page_numbers=[1, 2, 3])
    assert [page["text_status"] for page in result["pages"]] == ["native_text", "ocr_required", "native_text"]


def test_upload_segments_keep_paragraph_values_and_normalized_dates():
    pages, segments, status = parse_original((FIXTURES / "04_jinjiang_accident.pdf").read_bytes(), "pdf")
    assert pages == 15 and status == "READY"
    page_three = [item for item in segments if item["page"] == 3]
    assert any("1 人死亡" in item["text"] and "77.0239" in item["text"] for item in page_three)
    assert any("晋江市应急管理局牵头" in item["text"] for item in page_three)
    assert source_supports("2025-08-02", page_three[0]["text"])
    assert source_supports("06:59", page_three[0]["text"])
    assert not source_supports("650", page_three[0]["text"])


def test_year_month_normalization_stays_source_bound():
    excerpt = "项目拟于2025 年12 月开工，2026 年11月底完工。"
    assert source_supports("2025-12", excerpt)
    assert source_supports("2026-11", excerpt)
    assert not source_supports("2027-11", excerpt)


def test_numeric_source_match_requires_complete_value():
    assert source_supports("0.291", "容积率0.291-限值：0.39")
    assert not source_supports("0.29", "容积率0.291-限值：0.39")
    assert not source_supports("500", "总投资1500万元")
    assert source_supports("500", "总投资500万元")


def test_public_report_table_rows_keep_field_value_and_location():
    beijing = table_segments((FIXTURES / "01_beijing_feasibility.pdf").read_bytes(), 7)
    assert len(beijing) == 19
    ratio = next(row for row in beijing if row["ref"] == "p7-t1-r8")
    assert ratio["facts"] == [{"label": "容积率", "value_text": "0.291", "unit": "", "data_type": "decimal"}]
    assert "容积率" in ratio["text"] and "0.291" in ratio["text"]
    assert next(row for row in beijing if row["ref"] == "p7-t1-r13")["facts"] == []

    eia = table_segments((FIXTURES / "02_jiangmen_eia.pdf").read_bytes(), 5)
    choices = next(row for row in eia if row["ref"] == "p5-t1-r6")
    assert "建设性质" in choices["text"] and "新建（迁建）" in choices["text"]
    assert "建设项目 申报情形" in choices["text"] or "建设项目申报情形" in choices["text"]
    amounts = next(row for row in eia if row["ref"] == "p5-t1-r8")
    assert {(fact["label"], fact["value_text"], fact["unit"]) for fact in amounts["facts"]} == {
        ("总投资", "500", "万元"), ("环保投资", "17", "万元")}

    investment = table_segments((FIXTURES / "01_beijing_feasibility.pdf").read_bytes(), 177)
    engineering = next(row for row in investment if row["ref"] == "p177-t1-r1")
    assert engineering["facts"] == [{"label": "工程费", "value_text": "22122.45", "unit": "万元", "data_type": "decimal"}]
    assert all(fact["label"] != "万元" for row in investment for fact in row["facts"])
