"""Bounded model drafting from immutable, database-backed corpus chunks."""

from __future__ import annotations

import hashlib
import json
import re
from uuid import uuid4

from sqlalchemy import select

from .corpus_storage import CorpusArchive, CorpusRecord
from .model_settings import chat_json
from .report_pipeline import validate_content


ARTICLE_SECTIONS = {"S4": "产能与交付", "S5.2": "配电资料分歧", "S7.1": "设备购置与报价"}


def source_pack(session, corpus_id: str, section_id: str) -> dict:
    if section_id not in ARTICLE_SECTIONS:
        raise ValueError("该章节尚未开放模型文章起草")
    archive = session.get(CorpusArchive, corpus_id)
    outline = session.scalar(select(CorpusRecord).where(
        CorpusRecord.corpus_id == corpus_id, CorpusRecord.category_number == 4,
        CorpusRecord.semantic_id == section_id))
    if archive is None or outline is None:
        raise ValueError("章节资料不存在")
    chunk_ids = outline.payload.get("chunks") or []
    chunks = session.scalars(select(CorpusRecord).where(
        CorpusRecord.corpus_id == corpus_id, CorpusRecord.category_number == 9,
        CorpusRecord.semantic_id.in_(chunk_ids))).all()
    by_id = {row.semantic_id: row for row in chunks}
    if len(by_id) != len(chunk_ids):
        raise ValueError("章节原文切块不完整")
    sources = [{"id": key, "record_id": by_id[key].record_id, "artifact_id": by_id[key].artifact_id,
                "text": str(by_id[key].payload.get("text") or "")[:1800],
                "page": by_id[key].payload.get("page")}
               for key in chunk_ids]
    if sum(len(row["text"]) for row in sources) > 15000:
        raise ValueError("该章节资料超出当前模型输入范围")
    return {"corpus_id": corpus_id, "corpus_version": archive.version,
            "section_id": section_id, "section_title": outline.payload.get("title"),
            "sources": sources}


def pack_digest(pack: dict) -> str:
    raw = json.dumps(pack, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(raw.encode()).hexdigest()


def number_tokens(text: str) -> set[str]:
    return {match.replace(",", "") for match in re.findall(r"(?<!\d)\d[\d,]*(?:\.\d+)?(?!\d)", text)}


def candidate_from_model(pack: dict, title: str) -> tuple[list[dict], dict]:
    source_text = {row["id"]: row["text"] for row in pack["sources"]}
    prompt = (
        "你在整理一份虚构历史报告的指定章节，写一篇新的中文专题文章工作稿。"
        "仅使用给定原文，不引入外部事实、额外数字、因果或无条件结论。"
        "历史来源未经独立核实；预测不得称为订单，冲突不得自行裁决。"
        "每段给出实际支撑该段的1至4个切块ID；该段的每个数字（包括表号）、名称与条件必须出现在所标注切块中。"
        "例如段落写表4-1时，必须引用含表4-1的切块；没有来源的内容不要写。"
        "只返回JSON：{\"paragraphs\":[{\"text\":\"...\",\"source_ids\":[\"C0001\"]}]}。"
        "写3至6段，每段不超过600字。不要返回标题或其他字段。"
    )
    result = chat_json("writing", [
        {"role": "system", "content": prompt},
        {"role": "user", "content": json.dumps({"title": title, "section": pack["section_title"],
                                               "corpus_version": pack["corpus_version"],
                                               "sources": [{"id": row["id"], "text": row["text"]}
                                                           for row in pack["sources"]]}, ensure_ascii=False)},
    ], max_tokens=2200)
    paragraphs = result.get("paragraphs")
    if not isinstance(paragraphs, list) or not 2 <= len(paragraphs) <= 6:
        raise ValueError("模型没有返回可核对的文章段落")
    blocks = [{"type": "h1", "id": str(uuid4()), "children": [{"text": title}]}]
    for paragraph in paragraphs:
        if not isinstance(paragraph, dict) or set(paragraph) != {"text", "source_ids"}:
            raise ValueError("模型段落结构不正确")
        body, ids = paragraph["text"], paragraph["source_ids"]
        if (not isinstance(body, str) or not body.strip() or len(body) > 600
                or not isinstance(ids, list) or not 1 <= len(ids) <= 4
                or any(not isinstance(key, str) for key in ids)
                or len(set(ids)) != len(ids) or any(key not in source_text for key in ids)):
            raise ValueError("模型段落缺少有效来源或超出长度")
        # A table heading and its rows are separate historical chunks. A model
        # often cites only the rows, so attach the exact heading when named.
        for table_label in re.findall(r"表\d+-\d+", body):
            if any(table_label in source_text[key] for key in ids):
                continue
            headings = [key for key, value in source_text.items() if table_label in value]
            if len(headings) == 1 and len(ids) < 4:
                ids = [*ids, headings[0]]
        cited = " ".join(source_text[key] for key in ids)
        extra_numbers = number_tokens(body) - number_tokens(cited)
        if extra_numbers:
            raise ValueError(f"模型段落出现所标注来源中没有的数字：{'、'.join(sorted(extra_numbers))}")
        refs = []
        for key in ids:
            row = next(item for item in pack["sources"] if item["id"] == key)
            refs.append({"corpus_id": pack["corpus_id"], "corpus_version": pack["corpus_version"],
                         "category_id": "CAT-09", "artifact_id": row["artifact_id"],
                         "record_id": row["record_id"], "semantic_id": key, "use": "历史原文"})
        blocks.append({"type": "p", "id": str(uuid4()), "section_id": pack["section_id"],
                       "origin": "model", "source_refs": refs, "children": [{"text": body.strip()}]})
    if pack["section_id"] == "S5.2":
        full_text = " ".join(paragraph["text"] for paragraph in paragraphs)
        if not all(token in full_text for token in ("800", "650")) or not any(
                token in full_text for token in ("冲突", "不一致", "分歧", "未解决", "未核实")):
            raise ValueError("模型未完整披露800kW与650kW的未解决分歧")
    validate_content(blocks)
    return blocks, dict(getattr(result, "model_call", {}))
