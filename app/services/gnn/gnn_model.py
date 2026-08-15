"""
Optional GNN refinement pass (Stage 4b), built on PyTorch Geometric.

HONEST LIMITATION: no pretrained weights ship with this repo, and there is
no labeled P&ID connectivity graph dataset publicly available to bootstrap
from — the rule-based engine in rule_based_relations.py is what actually
produces relationships by default. This module becomes useful once you've
trained a checkpoint with train_gnn.py (see that file's docstring for the
dataset format and what it takes to bootstrap one: human-in-the-loop symbol
labels + expert-corrected rule-based output). Until then, calling this with
USE_GNN=true and no valid GNN_MODEL_PATH falls back to the rule-based
relationships unchanged — see maybe_refine_with_gnn below.

Node/edge feature encoding lives in relationship_gnn_model.py and is shared
verbatim between this file and train_gnn.py — the model here is loaded with
the SAME encode_node/encode_edge_geometry functions used at training time,
which is the one thing that must never drift between the two.
"""
from dataclasses import dataclass
from typing import Optional

import networkx as nx

from app.core.config import get_settings
from app.core.logging_config import get_logger
from app.services.gnn.graph_export import build_networkx_graph
from app.services.gnn.relationship_gnn_model import (
    ALL_CLASSES,
    NO_RELATION,
    GraphEntity,
    build_candidate_edges,
    build_model,
    encode_edge_geometry,
    encode_node,
)

logger = get_logger(__name__)


@dataclass
class GnnRefinementResult:
    relationships: list[dict]
    model_used: bool


class RelationshipGNN:
    """Thin wrapper around a trained PyTorch Geometric model. Only imports
    torch/PyG lazily (inside methods) so the rest of the app works without
    those heavy dependencies installed when USE_GNN=False."""

    def __init__(self, model_path: Optional[str]):
        self.model_path = model_path
        self._model = None
        self._relation_types: list[str] = ALL_CLASSES

    def _load(self):
        if self._model is not None:
            return self._model
        import torch

        if not self.model_path:
            raise FileNotFoundError("GNN_MODEL_PATH is not set")

        checkpoint = torch.load(self.model_path, map_location="cpu", weights_only=False)
        self._relation_types = checkpoint.get("relation_types", ALL_CLASSES)
        model = build_model(
            hidden_dim=checkpoint.get("hidden_dim", 64),
            num_layers=checkpoint.get("num_layers", 2),
        )
        model.load_state_dict(checkpoint["state_dict"])
        model.eval()
        self._model = model
        return self._model

    def predict(
        self,
        entities: list[GraphEntity],
        candidate_edges: list[dict],
        page_width: float,
        page_height: float,
    ) -> list[dict]:
        """
        Scores every candidate edge and returns only the ones the model
        assigns a real relation type (i.e. NOT the "none" class) as
        relationship dicts in the same shape rule_based_relations.
        infer_relationships() returns, tagged inferred_by="gnn".
        """
        model = self._load()  # raises if no weights configured — caller should catch
        import torch

        entity_by_id = {e.id: e for e in entities}
        node_index = {e.id: i for i, e in enumerate(entities)}

        x = torch.tensor(
            [encode_node(e, page_width, page_height) for e in entities], dtype=torch.float
        )

        pairs = build_candidate_edges(entities, candidate_edges)
        if not pairs:
            return []

        valid_pairs = [(s, t) for s, t in pairs if s in node_index and t in node_index]
        if not valid_pairs:
            return []
        src_idx = [node_index[s] for s, t in valid_pairs]
        tgt_idx = [node_index[t] for s, t in valid_pairs]

        edge_index = torch.tensor([src_idx + tgt_idx, tgt_idx + src_idx], dtype=torch.long)
        candidate_edge_index = torch.tensor([src_idx, tgt_idx], dtype=torch.long)
        geom_feats = torch.tensor(
            [encode_edge_geometry(entity_by_id[s], entity_by_id[t], page_width, page_height) for s, t in valid_pairs],
            dtype=torch.float,
        )

        with torch.no_grad():
            logits = model(x, edge_index, candidate_edge_index, geom_feats)
            probs = torch.softmax(logits, dim=-1)
            pred_idx = probs.argmax(dim=-1)

        results = []
        for i, (s, t) in enumerate(valid_pairs):
            rel_type = self._relation_types[int(pred_idx[i])]
            if rel_type == NO_RELATION:
                continue
            results.append({
                "source_entity_id": s,
                "source_entity_type": entity_by_id[s].entity_type,
                "target_entity_id": t,
                "target_entity_type": entity_by_id[t].entity_type,
                "relation_type": rel_type,
                "confidence": float(probs[i, int(pred_idx[i])]),
                "inferred_by": "gnn",
            })
        return results

    def refine(self, entities: list, candidate_edges: list[dict]) -> list[dict]:
        """
        Backward-compatible entry point: infers page_width/page_height from
        the entities' own bboxes when the caller doesn't have the source
        page dimensions handy, then delegates to predict().
        """
        graph_entities = [
            e if isinstance(e, GraphEntity) else GraphEntity(e.id, e.entity_type, e.class_name, e.bbox, getattr(e, "tag", None))
            for e in entities
        ]
        if graph_entities:
            page_width = max(e.bbox[2] for e in graph_entities)
            page_height = max(e.bbox[3] for e in graph_entities)
        else:
            page_width = page_height = 1.0
        return self.predict(graph_entities, candidate_edges, page_width, page_height)


def maybe_refine_with_gnn(entities: list, rule_based_relationships: list[dict]) -> GnnRefinementResult:
    settings = get_settings()
    if not settings.USE_GNN:
        return GnnRefinementResult(relationships=rule_based_relationships, model_used=False)

    try:
        gnn = RelationshipGNN(settings.GNN_MODEL_PATH)
        refined = gnn.refine(entities, rule_based_relationships)
        return GnnRefinementResult(relationships=refined, model_used=True)
    except Exception as exc:
        logger.warning(
            "gnn_refinement_unavailable_falling_back_to_rule_based",
            extra={"context": {"error": str(exc)}},
        )
        return GnnRefinementResult(relationships=rule_based_relationships, model_used=False)


def infer_graph(
    entities: list,
    rule_based_relationships: list[dict],
    page_width: float,
    page_height: float,
    model_path: Optional[str] = None,
) -> nx.DiGraph:
    """
    End-to-end convenience for callers who just want a graph: runs GNN
    refinement (or falls back to the rule-based relationships if no model
    is available) and returns a ready-to-persist NetworkX DiGraph — see
    graph_export.graph_to_relationship_records() to turn it into DB rows.
    """
    settings = get_settings()
    gnn = RelationshipGNN(model_path or settings.GNN_MODEL_PATH)
    try:
        graph_entities = [
            e if isinstance(e, GraphEntity) else GraphEntity(e.id, e.entity_type, e.class_name, e.bbox, getattr(e, "tag", None))
            for e in entities
        ]
        relationships = gnn.predict(graph_entities, rule_based_relationships, page_width, page_height)
        model_used = True
    except Exception as exc:
        logger.warning("gnn_inference_unavailable_falling_back_to_rule_based", extra={"context": {"error": str(exc)}})
        relationships = rule_based_relationships
        model_used = False

    graph = build_networkx_graph(entities, relationships)
    graph.graph["model_used"] = model_used
    return graph
