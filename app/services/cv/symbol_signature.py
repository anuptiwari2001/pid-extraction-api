"""
Cheap geometric "signature" for a symbol crop, used to auto-resolve unknown
symbols that a human has already labeled once — without retraining a model.

This is deliberately simple (aspect ratio + normalized contour/edge
histogram) rather than a learned embedding. It's meant as a fast first-pass
filter: "have I seen this exact shape before?" If you outgrow it (many
visually-similar-but-distinct symbols), swap `compute_signature` for a small
CNN embedding (e.g. a frozen ResNet feature vector) and compare with cosine
similarity instead of the histogram distance below — the call sites in
db/*_repository.py don't need to change, only this module.
"""
from typing import Any

import numpy as np


def compute_signature(crop_gray: "np.ndarray") -> dict:
    """
    crop_gray: 2D numpy array (grayscale crop of the symbol, already
    isolated by the bounding box). Returns a small JSON-serializable dict.
    """
    h, w = crop_gray.shape[:2]
    aspect_ratio = round(w / h, 3) if h else 0.0

    # Coarse 8x8 intensity grid, normalized 0-1 — cheap "shape fingerprint"
    # that's robust to minor scale/DPI differences but sensitive to gross
    # shape (circle vs. diamond vs. triangle silhouette).
    grid_size = 8
    ys = np.linspace(0, h, grid_size + 1).astype(int)
    xs = np.linspace(0, w, grid_size + 1).astype(int)
    grid = np.zeros((grid_size, grid_size))
    for i in range(grid_size):
        for j in range(grid_size):
            cell = crop_gray[ys[i]:ys[i + 1], xs[j]:xs[j + 1]]
            grid[i, j] = float(cell.mean()) / 255.0 if cell.size else 0.0

    return {
        "aspect_ratio": aspect_ratio,
        "intensity_grid": grid.flatten().round(3).tolist(),
    }


def signatures_match(sig_a: dict, sig_b: Any, aspect_tolerance: float = 0.15, grid_distance_threshold: float = 0.35) -> bool:
    """
    True if two signatures likely represent the same symbol category.
    Cheap L2 distance over the intensity grid + an aspect-ratio gate.
    """
    if not isinstance(sig_b, dict):
        return False
    ar_a, ar_b = sig_a.get("aspect_ratio"), sig_b.get("aspect_ratio")
    if ar_a is None or ar_b is None:
        return False
    if abs(ar_a - ar_b) > aspect_tolerance:
        return False

    grid_a = sig_a.get("intensity_grid")
    grid_b = sig_b.get("intensity_grid")
    if not grid_a or not grid_b or len(grid_a) != len(grid_b):
        return False

    arr_a, arr_b = np.array(grid_a), np.array(grid_b)
    distance = float(np.linalg.norm(arr_a - arr_b)) / len(arr_a) ** 0.5
    return distance <= grid_distance_threshold
