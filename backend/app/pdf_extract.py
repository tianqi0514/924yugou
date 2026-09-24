"""Read-only PDF structure baseline with physical-page and bounding-box provenance.

The output contains candidates for human review. It does not assert project facts,
calculate rules, or treat text in a source document as instructions.
"""

from __future__ import annotations

import hashlib
import re
from pathlib import Path

import pymupdf


CHAPTER = re.compile(r"^第[一二三四五六七八九十百零〇\d]+章\s*\S+")
SECTION = re.compile(r"^[一二三四五六七八九十]+、\s*\S+")
SUBSECTION = re.compile(r"^[（(][一二三四五六七八九十]+[）)]\s*\S+")
TABLE_CAPTION = re.compile(r"^表\s*\d+(?:[-－—.]\d+)+\s*\S+")
NUMBER = re.compile(r"^[+-]?\d{1,3}(?:,\d{3})*(?:\.\d+)?$|^[+-]?\d+(?:\.\d+)?$")


def _box(values: tuple[float, ...]) -> list[float]:
    return [round(float(value), 2) for value in values[:4]]


def _page_kind(text: str, image_count: int) -> str:
    if len(text.strip()) >= 30:
        return "native_text"
    if image_count:
        return "ocr_required"
    return "sparse_or_blank"


def _table_values(pdf_page: int, tables: list[dict]) -> list[dict]:
    """Surface numeric table rows as review candidates, never as project facts."""
    candidates = []
    for table in tables:
        if not table["cells"]:
            continue
        header = [re.sub(r"\s+", "", cell or "") for cell in table["cells"][0]]
        label_col = next((i for i, cell in enumerate(header) if cell in ("项目", "项目名称", "指标", "指标名称")), None)
        value_col = next((i for i, cell in enumerate(header) if re.search(r"数量|金额|合计|指标值|取值|结果", cell)), None)
        unit_col = next((i for i, cell in enumerate(header) if cell == "单位"), None)
        if label_col is None or value_col is None:
            continue
        header_unit = re.search(r"[（(]([^（）()]+)[）)]", header[value_col])
        for row_index, row in enumerate(table["cells"][1:], start=1):
            if max(label_col, value_col, unit_col or 0) >= len(row):
                continue
            label = (row[label_col] or "").strip()
            value = re.sub(r"\s+", "", row[value_col] or "")
            if not label or not (NUMBER.fullmatch(value) or value in ("-", "—", "/")):
                continue
            unit = (row[unit_col] or "").strip() if unit_col is not None else ""
            unit = unit or (header_unit.group(1) if header_unit else "")
            cell_boxes = table["cell_bboxes"][row_index]
            candidates.append({
                "pdf_page": pdf_page,
                "table_id": table["table_id"],
                "row_index": row_index,
                "label": label,
                "value": value if NUMBER.fullmatch(value) else None,
                "unit": unit,
                "status": "numeric_candidate" if NUMBER.fullmatch(value) else "missing_marker",
                "label_bbox": cell_boxes[label_col],
                "value_bbox": cell_boxes[value_col],
            })
    return candidates


def extract_pdf(source: str | Path, page_numbers: list[int] | None = None) -> dict:
    """Extract page blocks, heading candidates and table cells without DB writes.

    Page numbers are 1-based physical PDF pages. Bboxes are PDF point coordinates.
    Neither heading levels nor detected tables are considered reviewed facts.
    """
    path = Path(source)
    if not path.is_file() or path.suffix.lower() != ".pdf":
        raise ValueError("请提供存在的 PDF 文件")
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    with pymupdf.open(path) as document:
        if document.is_encrypted:
            raise ValueError("PDF 已加密，暂不能解析")
        total_pages = len(document)
        selected = list(range(1, total_pages + 1)) if page_numbers is None else sorted(set(page_numbers))
        if not selected or any(number < 1 or number > total_pages for number in selected):
            raise ValueError("页码超出 PDF 范围")
        pages: list[dict] = []
        for number in selected:
            page = document[number - 1]
            text = page.get_text("text", sort=True)
            raw_blocks = page.get_text("blocks", sort=True)
            blocks = [
                {"block_id": int(block[5]), "bbox": _box(block), "text": block[4].strip()}
                for block in raw_blocks if block[6] == 0 and block[4].strip()
            ]
            # A contents page is not a source of report-body headings.
            is_contents = len(re.findall(r"\.{5,}", text)) >= 4
            headings = []
            captions = []
            for block in blocks:
                value = re.sub(r"\s+", " ", block["text"]).strip()
                if len(value) > 100:
                    continue
                if TABLE_CAPTION.match(value):
                    captions.append(block)
                if is_contents:
                    continue
                # PDF generators may split a chapter number and title over two lines.
                level = None
                if CHAPTER.match(value):
                    level = 1
                elif "\n" not in block["text"].strip() and SECTION.match(value):
                    level = 2
                elif "\n" not in block["text"].strip() and SUBSECTION.match(value):
                    level = 3
                if level is not None:
                    headings.append({"level": level, "text": value, "block_id": block["block_id"], "bbox": block["bbox"]})
            tables = []
            for index, table in enumerate(page.find_tables().tables, start=1):
                preceding = [block for block in captions if 0 <= table.bbox[1] - block["bbox"][3] <= 90]
                caption = min(preceding, key=lambda block: table.bbox[1] - block["bbox"][3]) if preceding else None
                tables.append({
                    "table_id": f"p{number}-t{index}",
                    "bbox": _box(table.bbox),
                    "rows": table.row_count,
                    "columns": table.col_count,
                    "caption": caption["text"] if caption else None,
                    "caption_block_id": caption["block_id"] if caption else None,
                    "cells": table.extract(),
                    "cell_bboxes": [[_box(cell) if cell else None for cell in row.cells] for row in table.rows],
                })
            images = len(page.get_images(full=True))
            pages.append({
                "pdf_page": number,
                "width": round(page.rect.width, 2),
                "height": round(page.rect.height, 2),
                "text_status": _page_kind(text, images),
                "native_text_chars": len(text.strip()),
                "image_count": images,
                "is_contents_candidate": is_contents,
                "text": text,
                "blocks": blocks,
                "heading_candidates": headings,
                "table_candidates": tables,
                "table_value_candidates": _table_values(number, tables),
            })
    return {
        "source_file": path.name,
        "sha256": digest,
        "pdf_pages": total_pages,
        "extracted_pages": len(pages),
        "pages": pages,
    }
