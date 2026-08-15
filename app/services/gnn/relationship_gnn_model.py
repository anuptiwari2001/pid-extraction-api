"""
Model architecture + feature encoding for the relationship-refinement GNN
(Stage 4b). Split out from gnn_model.py (the inference-time wrapper used by
the extraction pipeline) so train_gnn.py and gnn_model.py share exactly one
definition of "how an entity becomes a feature vector" and "what the model
looks like" — a mismatch between training-time and inference-time encoding
is the single most common way GNN pipelines silently produce garbage.

Task: classify each CANDIDATE edge (a pair of entities that might be
related) into one of RELATION_TYPES, or "none" if they aren't related at
all. This is edge classification, not link prediction from scratch — the
candidate edges come from rule_based_relations.py's output plus k-nearest-
neighbor spatial pairs (see build_candidate_edges below), so the model only
has to decide which candidates are real and what type they are, which is a
much better-posed problem than proposing edges over all O(n^2) pairs.

Relation type vocabulary: this project's rule-based engine
(rule_based_relations.py) and ORM (models/orm.py:RelationshipEdge) already
define the vocabulary in production use — connected_to, controls, measures,
belongs_to — and every relationship this GNN predicts gets written to the
same `relationships` table via the same relation_type column. Rather than
introduce a second, incompatible vocabulary that would need translating on
every write, RELATION_TYPES below reuses those four names. If you want the
model to reason in terms of P&ID line semantics instead (process_flow /
instrument_signal / control / belonging), change RELATION_TYPES and add the
mapping to your DB's relation_type values in one place — everything else in
this file is vocabulary-agnostic.
"""
from __future__ import annotations

import math
from dataclasses import dataclass

RELATION_TYPES: list[str] = ["connected_to", "controls", "measures", "belongs_to"]
NO_RELATION = "none"
ALL_CLASSES: list[str] = RELATION_TYPES + [NO_RELATION]  # index NO_RELATION is always last

ENTITY_TYPES = ["symbol", "instrument", "equipment", "line"]
ISA_FIRST_LETTERS = ["F", "P", "T", "L", "A", "C", "S", "V"]  # see rule_based_relations.INSTRUMENT_FUNCTION_HINTS

# entity_type one-hot (4) + bbox geometry (7: x1,y1,x2,y2 norm, w, h, aspect)
# + has_tag (1) + ISA first-letter one-hot incl. "other" (9)
NODE_FEATURE_DIM = len(ENTITY_TYPES) + 7 + 1 + (len(ISA_FIRST_LETTERS) + 1)
# candidate-edge geometric features appended alongside the node embeddings:
# normalized center-to-center distance + angle (sin, cos)
EDGE_GEOM_FEATURE_DIM = 3


@dataclass
class GraphEntity:
    """Minimal shape the encoder needs — a superset of rule_based_relations.Entity
    (which this is interchangeable with; pass those instances directly)."""
    id: str
    entity_type: str  # symbol|instrument|equipment|line
    class_name: str
    bbox: tuple[float, float, float, float]
    tag: str | None = None


def encode_node(entity: GraphEntity, page_width: float, page_height: float) -> list[float]:
    """Entity -> fixed-length feature vector. page_width/height normalize
    bbox geometry so the same model generalizes across different drawing
    sheet sizes/DPI."""
    page_width = max(page_width, 1.0)
    page_height = max(page_height, 1.0)
    x1, y1, x2, y2 = entity.bbox

    type_onehot = [1.0 if entity.entity_type == t else 0.0 for t in ENTITY_TYPES]

    nx1, ny1, nx2, ny2 = x1 / page_width, y1 / page_height, x2 / page_width, y2 / page_height
    w, h = max(nx2 - nx1, 0.0), max(ny2 - ny1, 0.0)
    aspect = (w / h) if h > 1e-6 else 0.0
    geometry = [nx1, ny1, nx2, ny2, w, h, min(aspect, 10.0) / 10.0]  # aspect clipped+scaled to keep features bounded

    has_tag = [1.0 if entity.tag else 0.0]

    isa_letter = entity.tag[0].upper() if entity.tag else None
    isa_onehot = [1.0 if isa_letter == letter else 0.0 for letter in ISA_FIRST_LETTERS]
    isa_onehot.append(1.0 if (isa_letter is not None and isa_letter not in ISA_FIRST_LETTERS) else 0.0)

    return type_onehot + geometry + has_tag + isa_onehot


def _center(bbox: tuple[float, float, float, float]) -> tuple[float, float]:
    x1, y1, x2, y2 = bbox
    return (x1 + x2) / 2, (y1 + y2) / 2


def encode_edge_geometry(
    source: GraphEntity, target: GraphEntity, page_width: float, page_height: float
) -> list[float]:
    """Candidate-edge geometric features: normalized distance + direction.
    Concatenated onto [node_embedding_src, node_embedding_tgt] before the
    edge classifier head — direction matters for P&ID semantics (e.g. an
    instrument's tag is conventionally drawn just above/beside the
    equipment it belongs to), distance alone would throw that away.
    """
    diag = math.hypot(max(page_width, 1.0), max(page_height, 1.0))
    sx, sy = _center(source.bbox)
    tx, ty = _center(target.bbox)
    dist = math.hypot(tx - sx, ty - sy) / diag
    angle = math.atan2(ty - sy, tx - sx)
    return [dist, math.sin(angle), math.cos(angle)]


def build_candidate_edges(
    entities: list[GraphEntity],
    rule_based_relationships: list[dict],
    k_nearest: int = 4,
) -> list[tuple[str, str]]:
    """
    Candidate (source_id, target_id) pairs for the model to score: every
    rule-based edge (so the GNN can confirm, reclassify, or reject them),
    plus k-nearest-neighbor spatial pairs (so the model can also surface
    connections the rule-based pass missed entirely — dense drawings,
    crossing lines, off-page connectors are exactly the cases the GNN
    module docstring in gnn_model.py calls out as the target use case).
    """
    pairs: set[tuple[str, str]] = set()
    for rel in rule_based_relationships:
        pairs.add((rel["source_entity_id"], rel["target_entity_id"]))

    centers = {e.id: _center(e.bbox) for e in entities}
    for e in entities:
        dists = sorted(
            ((math.hypot(centers[e.id][0] - centers[o.id][0], centers[e.id][1] - centers[o.id][1]), o.id)
             for o in entities if o.id != e.id),
            key=lambda t: t[0],
        )
        for _, other_id in dists[:k_nearest]:
            pairs.add((e.id, other_id))

    return list(pairs)


def build_model(hidden_dim: int = 64, num_layers: int = 2):
    """
    Lazy-imports torch/PyG so the rest of the app works without those heavy
    deps installed when USE_GNN=False (same pattern as gnn_model.py).

    Architecture: a small GraphSAGE encoder (message-passing over the
    candidate-edge structure so each node's embedding reflects its local
    neighborhood — e.g. an instrument bubble "sees" the equipment it sits
    near) feeding an MLP edge classifier over
    [src_embedding, tgt_embedding, edge_geometry]. GraphSAGE over GAT/GCN
    because P&ID candidate graphs are sparse and irregular (a symbol may
    have anywhere from 1 to a dozen nearby candidates) — SAGE's neighbor-
    sampling/mean-aggregation formulation handles that variability without
    the extra attention-weight parameters GAT would need to learn from a
    small dataset.
    """
    import torch
    import torch.nn as nn
    from torch_geometric.nn import SAGEConv

    class RelationshipGNNModel(nn.Module):
        def __init__(self, in_dim: int, hidden_dim: int, num_layers: int, num_classes: int, edge_geom_dim: int):
            super().__init__()
            self.convs = nn.ModuleList()
            dims = [in_dim] + [hidden_dim] * num_layers
            for i in range(num_layers):
                self.convs.append(SAGEConv(dims[i], dims[i + 1]))
            self.edge_mlp = nn.Sequential(
                nn.Linear(hidden_dim * 2 + edge_geom_dim, hidden_dim),
                nn.ReLU(),
                nn.Dropout(0.1),
                nn.Linear(hidden_dim, num_classes),
            )

        def encode_nodes(self, x, edge_index):
            h = x
            for i, conv in enumerate(self.convs):
                h = conv(h, edge_index)
                if i < len(self.convs) - 1:
                    h = torch.relu(h)
            return h

        def forward(self, x, edge_index, candidate_edge_index, edge_geom_feats):
            h = self.encode_nodes(x, edge_index)
            src, tgt = candidate_edge_index
            edge_repr = torch.cat([h[src], h[tgt], edge_geom_feats], dim=-1)
            return self.edge_mlp(edge_repr)  # logits: [num_candidates, num_classes]

    return RelationshipGNNModel(
        in_dim=NODE_FEATURE_DIM,
        hidden_dim=hidden_dim,
        num_layers=num_layers,
        num_classes=len(ALL_CLASSES),
        edge_geom_dim=EDGE_GEOM_FEATURE_DIM,
    )
