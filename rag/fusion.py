"""
Reciprocal Rank Fusion (RRF) for combining ranked result lists.

RRF score = Σ weight_i / (k + rank_i)
Standard k=60 (Cormack et al., 2009). Higher score = more relevant.
"""
from __future__ import annotations

from collections import defaultdict


def reciprocal_rank_fusion(
    ranked_lists: list[list[tuple[str, float]]],
    weights: list[float] | None = None,
    k: int = 60,
) -> list[tuple[str, float]]:
    """
    Fuse multiple ranked lists into one unified ranking.

    Args:
        ranked_lists: Each list is [(doc_id, score), ...] sorted best-first.
                      Scores are used only for ordering within each list.
        weights:      Per-list weights. Defaults to equal weights.
        k:            RRF constant (default 60).

    Returns:
        [(doc_id, rrf_score), ...] sorted descending by rrf_score, deduplicated.
    """
    if not ranked_lists:
        return []

    n = len(ranked_lists)
    if weights is None:
        weights = [1.0 / n] * n
    elif len(weights) != n:
        raise ValueError(f"weights length {len(weights)} != ranked_lists length {n}")

    scores: dict[str, float] = defaultdict(float)
    for lst, w in zip(ranked_lists, weights):
        for rank, (doc_id, _) in enumerate(lst, start=1):
            scores[doc_id] += w / (k + rank)

    return sorted(scores.items(), key=lambda x: x[1], reverse=True)
