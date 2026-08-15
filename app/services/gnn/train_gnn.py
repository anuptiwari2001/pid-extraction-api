"""
Training script for the relationship-refinement GNN (Stage 4b). Standalone
CLI, run OUTSIDE the API process — the trained checkpoint it produces is
what GNN_MODEL_PATH points at.

HONEST LIMITATION, same one as gnn_model.py's module docstring: there is no
labeled P&ID connectivity dataset to point this at yet. It becomes usable
once you've accumulated enough of the two ingredients the project already
collects: (a) human-in-the-loop symbol labels (UnknownSymbol /
symbol_dictionary — gives you node classes), and (b) rule-based
relationship output that a domain expert has spot-corrected (gives you
labeled edges). Until then this script runs correctly against a toy/synthetic
dataset (see `--dataset` format below) but has nothing real to learn from.

Dataset format (JSON): a list of per-page graphs —
[
  {
    "page_width": 3300, "page_height": 2550,
    "nodes": [
      {"id": "e1", "entity_type": "instrument", "class_name": "PT",
       "bbox": [100, 100, 140, 140], "tag": "PT-101"},
      {"id": "e2", "entity_type": "equipment", "class_name": "vessel",
       "bbox": [200, 90, 260, 220], "tag": "V-101"},
      ...
    ],
    "rule_based_relationships": [
      {"source_entity_id": "e1", "target_entity_id": "e2", "relation_type": "belongs_to", ...}
    ],
    "labeled_edges": [
      {"source_entity_id": "e1", "target_entity_id": "e2", "relation_type": "measures"}
    ]
  },
  ...
]
`labeled_edges` is the ground truth (typically: rule-based output after a
domain expert corrected the wrong ones) — every candidate edge NOT present
in labeled_edges is trained as the "none" class, so labeled_edges only
needs to list the positives.

Usage:
    python -m app.services.gnn.train_gnn \\
        --dataset ./gnn_training_data.json \\
        --output ./models_weights/pid_relations_gnn.pt \\
        --epochs 100 --val-split 0.15

Then set USE_GNN=true and GNN_MODEL_PATH to the output path in .env.
"""
from __future__ import annotations

import argparse
import json
import logging
import random
import sys
from pathlib import Path

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger("train_gnn")


def _load_dataset(path: Path) -> list[dict]:
    data = json.loads(path.read_text())
    if not isinstance(data, list) or not data:
        raise ValueError(f"{path} must contain a non-empty JSON list of page graphs")
    return data


def _build_page_data(page: dict):
    """One page's JSON record -> (PyG Data for message passing, candidate
    edge_index, edge_geom_feats tensor, label tensor). Imports torch/PyG
    lazily so `--help` and dataset validation work without those installed.
    """
    import torch
    from torch_geometric.data import Data

    from app.services.gnn.relationship_gnn_model import (
        ALL_CLASSES,
        GraphEntity,
        build_candidate_edges,
        encode_edge_geometry,
        encode_node,
    )

    page_w, page_h = page["page_width"], page["page_height"]
    entities = [
        GraphEntity(
            id=n["id"], entity_type=n["entity_type"], class_name=n.get("class_name", "unknown"),
            bbox=tuple(n["bbox"]), tag=n.get("tag"),
        )
        for n in page["nodes"]
    ]
    entity_by_id = {e.id: e for e in entities}
    node_index = {e.id: i for i, e in enumerate(entities)}

    x = torch.tensor([encode_node(e, page_w, page_h) for e in entities], dtype=torch.float)

    rule_based = page.get("rule_based_relationships", [])
    candidates = build_candidate_edges(entities, rule_based)
    if not candidates:
        return None  # nothing to learn from on this page (e.g. a single-node graph)

    # Message-passing structure: the candidate edges themselves, made
    # undirected (concat with reversed) so information flows both ways
    # during encoding regardless of which direction a relation happens to
    # point.
    src_idx = [node_index[s] for s, t in candidates]
    tgt_idx = [node_index[t] for s, t in candidates]
    edge_index = torch.tensor(
        [src_idx + tgt_idx, tgt_idx + src_idx], dtype=torch.long
    )

    label_lookup = {
        (rel["source_entity_id"], rel["target_entity_id"]): rel["relation_type"]
        for rel in page.get("labeled_edges", [])
    }
    class_to_idx = {c: i for i, c in enumerate(ALL_CLASSES)}
    none_idx = class_to_idx["none"]

    labels = []
    geom_feats = []
    cand_src, cand_tgt = [], []
    for s, t in candidates:
        rel_type = label_lookup.get((s, t))
        labels.append(class_to_idx.get(rel_type, none_idx))
        geom_feats.append(encode_edge_geometry(entity_by_id[s], entity_by_id[t], page_w, page_h))
        cand_src.append(node_index[s])
        cand_tgt.append(node_index[t])

    candidate_edge_index = torch.tensor([cand_src, cand_tgt], dtype=torch.long)
    edge_geom_feats = torch.tensor(geom_feats, dtype=torch.float)
    y = torch.tensor(labels, dtype=torch.long)

    data = Data(x=x, edge_index=edge_index)
    return data, candidate_edge_index, edge_geom_feats, y


def train(
    dataset_path: Path,
    output_path: Path,
    epochs: int,
    lr: float,
    hidden_dim: int,
    num_layers: int,
    val_split: float,
    seed: int,
) -> None:
    import torch
    import torch.nn.functional as F

    from app.services.gnn.relationship_gnn_model import ALL_CLASSES, build_model

    random.seed(seed)
    torch.manual_seed(seed)

    raw_pages = _load_dataset(dataset_path)
    examples = []
    for page in raw_pages:
        built = _build_page_data(page)
        if built is not None:
            examples.append(built)
    if not examples:
        raise ValueError("No trainable page graphs after filtering — check your dataset's nodes/edges")

    random.shuffle(examples)
    n_val = max(1, int(len(examples) * val_split)) if val_split > 0 else 0
    val_examples = examples[:n_val]
    train_examples = examples[n_val:]
    if not train_examples:
        raise ValueError("val_split leaves zero training examples — reduce it or add more pages")
    logger.info("dataset: %d train pages, %d val pages", len(train_examples), len(val_examples))

    # Class weighting: "none" massively outnumbers real relation types in
    # any candidate-edge formulation (most candidate pairs aren't related),
    # so weight it down or the model trivially collapses to predicting
    # "none" for everything.
    class_counts = [0] * len(ALL_CLASSES)
    for _, _, _, y in train_examples:
        for c in y.tolist():
            class_counts[c] += 1
    total = sum(class_counts)
    weights = torch.tensor(
        [total / (len(ALL_CLASSES) * max(c, 1)) for c in class_counts], dtype=torch.float
    )
    logger.info("class counts: %s", dict(zip(ALL_CLASSES, class_counts)))

    model = build_model(hidden_dim=hidden_dim, num_layers=num_layers)
    optimizer = torch.optim.Adam(model.parameters(), lr=lr)

    def run_epoch(examples_, train_mode: bool):
        model.train(train_mode)
        total_loss, correct, total_n = 0.0, 0, 0
        for data, cand_edge_index, edge_geom, y in examples_:
            if train_mode:
                optimizer.zero_grad()
            logits = model(data.x, data.edge_index, cand_edge_index, edge_geom)
            loss = F.cross_entropy(logits, y, weight=weights)
            if train_mode:
                loss.backward()
                optimizer.step()
            total_loss += loss.item() * len(y)
            correct += int((logits.argmax(dim=-1) == y).sum())
            total_n += len(y)
        return total_loss / max(total_n, 1), correct / max(total_n, 1)

    best_val_acc = -1.0
    best_state = None
    for epoch in range(1, epochs + 1):
        train_loss, train_acc = run_epoch(train_examples, train_mode=True)
        if val_examples:
            with torch.no_grad():
                val_loss, val_acc = run_epoch(val_examples, train_mode=False)
        else:
            val_loss, val_acc = train_loss, train_acc

        if val_acc >= best_val_acc:
            best_val_acc = val_acc
            best_state = {k: v.clone() for k, v in model.state_dict().items()}

        if epoch == 1 or epoch % max(1, epochs // 20) == 0 or epoch == epochs:
            logger.info(
                "epoch %d/%d  train_loss=%.4f train_acc=%.3f  val_loss=%.4f val_acc=%.3f",
                epoch, epochs, train_loss, train_acc, val_loss, val_acc,
            )

    if best_state is not None:
        model.load_state_dict(best_state)

    output_path.parent.mkdir(parents=True, exist_ok=True)
    torch.save(
        {
            "state_dict": model.state_dict(),
            "hidden_dim": hidden_dim,
            "num_layers": num_layers,
            "relation_types": ALL_CLASSES,
            "best_val_acc": best_val_acc,
        },
        output_path,
    )
    logger.info("saved checkpoint to %s (best val_acc=%.3f)", output_path, best_val_acc)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--dataset", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--epochs", type=int, default=100)
    parser.add_argument("--lr", type=float, default=1e-3)
    parser.add_argument("--hidden-dim", type=int, default=64)
    parser.add_argument("--num-layers", type=int, default=2)
    parser.add_argument("--val-split", type=float, default=0.15)
    parser.add_argument("--seed", type=int, default=42)
    args = parser.parse_args(argv)

    try:
        import torch  # noqa: F401
        import torch_geometric  # noqa: F401
    except ImportError:
        logger.error("torch + torch-geometric are required to train (see requirements.txt's GNN section)")
        return 1

    train(
        dataset_path=args.dataset,
        output_path=args.output,
        epochs=args.epochs,
        lr=args.lr,
        hidden_dim=args.hidden_dim,
        num_layers=args.num_layers,
        val_split=args.val_split,
        seed=args.seed,
    )
    return 0


if __name__ == "__main__":
    sys.path.insert(0, str(Path(__file__).resolve().parents[3]))  # allow `python train_gnn.py` outside -m
    sys.exit(main())
