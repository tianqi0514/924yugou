"""Read-only consistency checks for the frozen corpus' graph and lookup projections."""

import os

os.environ["DATABASE_URL"] = "postgresql+psycopg://report:local_development_only@127.0.0.1:55432/report_platform_test"

from sqlalchemy import select

from app.db import SessionLocal, engine
from app.corpus_storage import CorpusGraphEdge, CorpusRecord


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
