"""Read-only consistency checks for the frozen corpus' graph and lookup projections."""

import os
import csv
from collections import defaultdict
from io import BytesIO, StringIO
from zipfile import ZipFile

os.environ["DATABASE_URL"] = "postgresql+psycopg://report:local_development_only@127.0.0.1:55432/report_platform_test"

from lxml import etree
from sqlalchemy import select

from app.db import SessionLocal, engine
from app.corpus_storage import CorpusArtifact, CorpusGraphEdge, CorpusRecord


def rows(session, category):
    return session.scalars(select(CorpusRecord).where(
        CorpusRecord.corpus_id == "cy_tray_20260918",
        CorpusRecord.category_number == category,
    ).order_by(CorpusRecord.ordinal)).all()


def test_snapshot_counts_match_the_authoritative_category_rows():
    assert engine.url.database == "report_platform_test"
    sources = {"nodes": 12, "rules": 13, "relations": 14, "claims": 15,
               "invariants": 16, "evidence_nodes": 18, "sections": 4,
               "chunk_nodes": 9, "conflicts": 23, "gaps": 24}
    with SessionLocal() as session:
        snapshot = {item.payload["collection"]: item.payload["count"] for item in rows(session, 17)}
        assert set(snapshot) == set(sources)
        assert all(snapshot[name] == len(rows(session, number)) for name, number in sources.items())


def test_all_1666_explicit_relations_are_preserved_in_the_graph_projection():
    assert engine.url.database == "report_platform_test"
    with SessionLocal() as session:
        relations = rows(session, 14)
        graph = {edge.id: edge for edge in session.scalars(select(CorpusGraphEdge).where(
            CorpusGraphEdge.corpus_id == "cy_tray_20260918")).all()}
        assert len(relations) == 1666
        for row in relations:
            edge = graph[row.semantic_id]
            assert edge.derivation == "raw"
            assert (edge.source, edge.target, edge.type) == (
                row.payload["from_id"], row.payload["to_id"], row.payload["type"])
        assert len(graph) > len(relations)  # The browsing graph also holds marked derived edges.


def test_node_index_direct_and_secondary_mentions_are_explicit():
    assert engine.url.database == "report_platform_test"
    with SessionLocal() as session:
        nodes = {row.semantic_id: row.payload for row in rows(session, 12)}
        index = {row.semantic_id: row.payload for row in rows(session, 28)}
        assert len(nodes) == len(index) == 339
        for node_id, source in nodes.items():
            projection = index[node_id]
            assert projection["source_locations"] == source.get("source_locations", [])
            direct = set(source.get("mentions", []))
            enriched = {mention["id"]: mention for mention in projection["mentions"]}
            assert direct.issubset(enriched)
            for mention_id in enriched.keys() - direct:
                assert node_id in enriched[mention_id].get("also_supports_node_ids", [])
            assert set(projection["chunks"]) == {mention["chunk_id"] for mention in enriched.values()}


def _word_text(element):
    """Read paragraph/cell text from the stored Word XML, including line breaks."""
    return "".join(
        (part.text or "") if part.tag.endswith("}t") else
        ("\n" if part.tag.endswith("}br") else "\t")
        for part in element.iter() if part.tag.endswith(("}t", "}br", "}tab")))


def _stored_word(session):
    artifact = session.scalar(select(CorpusArtifact).where(
        CorpusArtifact.corpus_id == "cy_tray_20260918",
        CorpusArtifact.category_number == 5))
    assert artifact is not None
    return etree.fromstring(ZipFile(BytesIO(bytes(artifact.content))).read("word/document.xml"))


def test_all_194_offsets_point_to_the_stored_word_and_normalized_text():
    assert engine.url.database == "report_platform_test"
    ns = {"w": "http://schemas.openxmlformats.org/wordprocessingml/2006/main"}
    with SessionLocal() as session:
        word = _stored_word(session)
        normalized = rows(session, 6)[0].payload["text"]
        offsets = rows(session, 7)
        assert len(offsets) == 194
        for record in offsets:
            item = record.payload
            if item["type"] == "table":
                for cell in item["cells"]:
                    match = word.xpath(cell["source_locator"]["xpath"], namespaces=ns)
                    assert len(match) == 1, record.semantic_id
                    assert _word_text(match[0]) == cell["raw_text"], record.semantic_id
                    start, end = cell["span"]
                    assert normalized[start:end] == cell["normalized_text"], record.semantic_id
            else:
                match = word.xpath(item["source_locator"]["xpath"], namespaces=ns)
                assert len(match) == 1, record.semantic_id
                raw_start, raw_end = item["raw_paragraph_span"]
                start, end = item["content_span"]
                assert _word_text(match[0])[raw_start:raw_end] == normalized[start:end], record.semantic_id


def test_all_18_tables_match_stored_csv_rows_and_word_cells():
    assert engine.url.database == "report_platform_test"
    with SessionLocal() as session:
        table_rows = rows(session, 8)
        offset_tables = [row.payload for row in rows(session, 7) if row.payload["type"] == "table"]
        artifacts = {item.path: item for item in session.scalars(select(CorpusArtifact).where(
            CorpusArtifact.corpus_id == "cy_tray_20260918",
            CorpusArtifact.category_number == 8)).all()}
        assert len(table_rows) == len(offset_tables) == len(artifacts) == 18
        for record, offset in zip(table_rows, offset_tables, strict=True):
            table = record.payload
            csv_rows = list(csv.reader(StringIO(bytes(artifacts[table["file"]].content).decode("utf-8-sig"))))
            assert csv_rows == table["rows"]
            assert len(csv_rows) == table["row_count"]
            assert max(map(len, csv_rows)) == table["column_count"]
            assert len({cell["row"] for cell in offset["cells"]}) == table["row_count"]
            assert len({cell["column"] for cell in offset["cells"]}) == table["column_count"]
            for cell in offset["cells"]:
                assert csv_rows[cell["row"]][cell["column"]] == cell["raw_text"]


def test_term_aliases_do_not_cross_identify_nodes_and_unobserved_names_stay_disabled():
    assert engine.url.database == "report_platform_test"
    with SessionLocal() as session:
        terms = rows(session, 29)
        nodes = {record.semantic_id for record in rows(session, 12)}
        labels = defaultdict(list)
        assert len(terms) == 20
        for record in terms:
            item = record.payload
            assert set(item["node_ids"]).issubset(nodes)
            assert item["disambiguation"]
            for label in [item["canonical"], *item["observed_aliases"]]:
                labels[label].append(item["canonical"])
            assert not set(item["unobserved_aliases_not_enabled"]) & set(item["observed_aliases"])
        assert len(labels) == 39
        assert all(len(owners) == 1 for owners in labels.values())
