"""
UMAP projection of reaction embeddings with Plotly interactive scatter.
Colors reactions by yield quartile; hover shows SMILES + paper.
"""
from __future__ import annotations

from pathlib import Path

import numpy as np


def visualize_embedding_space(
    index,
    output_path: str | Path,
    n_neighbors: int = 15,
    min_dist: float = 0.1,
) -> None:
    """
    Args:
        index: a built ReactionIndex instance
        output_path: path to write the interactive HTML file
    """
    try:
        import umap
    except ImportError:
        raise ImportError("Install umap-learn: pip install umap-learn")
    try:
        import plotly.graph_objects as go
    except ImportError:
        raise ImportError("Install plotly: pip install plotly")

    assert index._index is not None, "Build or load the index first"
    n = len(index._meta)
    matrix = index._index.reconstruct_n(0, n).astype(np.float32)

    print(f"Running UMAP on {n} reactions…")
    reducer = umap.UMAP(
        n_components=2,
        n_neighbors=n_neighbors,
        min_dist=min_dist,
        metric="cosine",
        random_state=42,
    )
    coords = reducer.fit_transform(matrix)

    yields = [m.get("yield") for m in index._meta]
    valid_yields = [y for y in yields if y is not None]

    if valid_yields:
        q25, q50, q75 = np.percentile(valid_yields, [25, 50, 75])

        def yield_color(y):
            if y is None:
                return "grey"
            if y < q25:
                return "#d32f2f"   # low — red
            if y < q50:
                return "#f57c00"   # q1-q2 — orange
            if y < q75:
                return "#388e3c"   # q2-q3 — green
            return "#1565c0"       # high — blue
    else:
        def yield_color(_):
            return "grey"

    colors = [yield_color(y) for y in yields]
    hover_texts = [
        f"<b>{m['paper'][:40]}</b><br>"
        f"Table: {m['table_name']} | Entry: {m['entry_id']}<br>"
        f"Yield: {m['yield']}%<br>"
        f"<i>{m['reaction_smiles'][:80]}</i>"
        for m in index._meta
    ]

    fig = go.Figure(
        go.Scatter(
            x=coords[:, 0],
            y=coords[:, 1],
            mode="markers",
            marker=dict(color=colors, size=7, opacity=0.8, line=dict(width=0.5, color="white")),
            text=hover_texts,
            hoverinfo="text",
        )
    )
    fig.update_layout(
        title="Reaction Embedding Space (UMAP)",
        xaxis_title="UMAP-1",
        yaxis_title="UMAP-2",
        plot_bgcolor="#1a1a2e",
        paper_bgcolor="#16213e",
        font=dict(color="white"),
        height=750,
    )

    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    fig.write_html(str(output_path))
    print(f"Embedding visualization saved → {output_path}")
