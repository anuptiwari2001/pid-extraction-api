"""
Builds a NetworkX graph from entities + relationship edges, and converts it
back into the flat dict shape app/db/*_repository.py expects when writing to
the `relationships` table (see models/orm.py:RelationshipEdge and
schemas.py:GraphExport). Shared by both relationship sources — rule-based
(rule_based_relations.py) and GNN (gnn_model.py) — so there is exactly one
"entities + edges -> graph" implementation regardless of which stage
produced the edges.

NetworkX here is a convenience representation for callers that want graph
algorithms (connected components to find isolated/orphan symbols, shortest
path between two tags, cycle detection on control loops, etc.) before
persisting — it is not itself the storage layer. Persist via
`graph_to_relationship_records`, not by serializing the nx.DiGraph.
"""
from __future__ import annotations

import networkx as nx


def build_networkx_graph(entities: list, relationships: list[dict]) -> nx.DiGraph:
    """
    entities: objects with .id, .entity_type, .class_name, .bbox, .tag
      (rule_based_relations.Entity / relationship_gnn_model.GraphEntity —
      either works, only those attributes are read).
    relationships: list of {source_entity_id, source_entity_type,
      target_entity_id, target_entity_type, relation_type, confidence,
      inferred_by} — the exact shape both infer_relationships() and
      predict_relationships_gnn() return.

    Returns a directed graph: nodes keyed by entity id with
    type/label/tag/bbox attributes, edges carrying relation_type/
    confidence/inferred_by. Directed because several relation types are
    inherently asymmetric (an instrument "controls"/"measures" equipment,
    not the reverse) and edge direction is meaningful data, not an
    implementation detail to discard.
    """
    graph = nx.DiGraph()
    for entity in entities:
        graph.add_node(
            entity.id,
            type=entity.entity_type,
            label=entity.tag or entity.class_name,
            class_name=entity.class_name,
            tag=entity.tag,
            bbox=list(entity.bbox),
        )
    for rel in relationships:
        # Defensive: only add edges between entities we actually have nodes
        # for. Candidate-edge generation elsewhere (rule engine, GNN
        # k-nearest-neighbor) can only ever reference known entities, but a
        # caller passing a filtered entity list shouldn't produce a graph
        # with dangling edge references.
        if rel["source_entity_id"] not in graph or rel["target_entity_id"] not in graph:
            continue
        graph.add_edge(
            rel["source_entity_id"],
            rel["target_entity_id"],
            relation_type=rel["relation_type"],
            confidence=rel.get("confidence", 1.0),
            inferred_by=rel.get("inferred_by", "rule_based"),
        )
    return graph


def graph_to_relationship_records(graph: nx.DiGraph, project_id: str, page_id: str) -> list[dict]:
    """
    nx.DiGraph -> list of dicts matching RelationshipEdge's constructor
    kwargs (minus `id`, which the ORM default_factory generates), ready for
    `repository.bulk_insert_relationships(records)` / `session.add_all(...)`.
    This is the one place a graph turns back into rows — keeping it
    separate from build_networkx_graph means round-tripping (build graph,
    run an algorithm, persist result) never has to re-derive the mapping.
    """
    records = []
    for source_id, target_id, data in graph.edges(data=True):
        records.append({
            "project_id": project_id,
            "page_id": page_id,
            "source_entity_id": source_id,
            "source_entity_type": graph.nodes[source_id].get("type", "unknown"),
            "target_entity_id": target_id,
            "target_entity_type": graph.nodes[target_id].get("type", "unknown"),
            "relation_type": data["relation_type"],
            "confidence": data.get("confidence", 1.0),
            "inferred_by": data.get("inferred_by", "rule_based"),
        })
    return records
