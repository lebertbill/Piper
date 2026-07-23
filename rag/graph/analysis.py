"""
Network analysis functions over the reaction graph.
All functions accept a NetworkX MultiDiGraph produced by graph.builder.build_graph().
"""
from __future__ import annotations

from collections import defaultdict

import networkx as nx
import pandas as pd

from .utils import display_name


def compound_centrality(G: nx.MultiDiGraph) -> pd.DataFrame:
    """
    PageRank of compound nodes — identifies the most connected/influential molecules.
    Returns DataFrame sorted by pagerank descending.
    """
    pr = nx.pagerank(G, alpha=0.85)
    rows = []
    for node, score in pr.items():
        data = G.nodes[node]
        if data.get("node_type") != "Compound":
            continue
        rows.append(
            {
                "inchikey": node,
                "smiles": data.get("smiles_canonical", ""),
                "name": display_name(data),
                "aliases": ", ".join(data.get("aliases", [])),
                "papers": ", ".join(data.get("papers", [])),
                "pagerank": round(score, 6),
            }
        )
    df = pd.DataFrame(rows)
    if df.empty:
        return df
    return df.sort_values("pagerank", ascending=False).reset_index(drop=True)


def reagent_cooccurrence(G: nx.MultiDiGraph) -> pd.DataFrame:
    """
    Catalyst × solvent co-occurrence matrix.
    Counts how many reactions pair each (catalyst_label, solvent_smiles).
    """
    counts: dict[tuple[str, str], int] = defaultdict(int)
    for node, data in G.nodes(data=True):
        if data.get("node_type") != "Reaction":
            continue
        catalysts = [
            G.nodes[v].get("label_text") or G.nodes[v].get("smiles_canonical", "?")
            for _, v, ed in G.out_edges(node, data=True)
            if ed.get("edge_type") == "USES_CATALYST"
        ]
        solvent = data.get("solvent_smiles") or "unknown"
        for cat in catalysts:
            counts[(cat, solvent)] += 1

    rows = [{"catalyst": k[0], "solvent": k[1], "count": v} for k, v in counts.items()]
    df = pd.DataFrame(rows)
    if df.empty:
        return df
    return (
        df.pivot_table(index="catalyst", columns="solvent", values="count", fill_value=0)
        .astype(int)
    )


def shortest_reaction_path(G: nx.MultiDiGraph, smiles_a: str, smiles_b: str) -> list[str] | None:
    """
    Finds the shortest path between two compounds through reactions.
    Useful for retrosynthesis-style path discovery across papers.
    Returns list of node keys, or None if no path exists.
    """
    from .deduplicator import smiles_to_inchikey

    key_a = smiles_to_inchikey(smiles_a) or smiles_a
    key_b = smiles_to_inchikey(smiles_b) or smiles_b

    if key_a not in G or key_b not in G:
        return None

    try:
        path = nx.shortest_path(G.to_undirected(as_view=True), source=key_a, target=key_b)
        return path
    except nx.NetworkXNoPath:
        return None


def yield_by_catalyst(G: nx.MultiDiGraph) -> pd.DataFrame:
    """
    Average yield grouped by catalyst node.
    Returns DataFrame with catalyst info and yield statistics.
    """
    cat_yields: dict[str, list[float]] = defaultdict(list)

    for rxn_node, data in G.nodes(data=True):
        if data.get("node_type") != "Reaction":
            continue
        yld = data.get("yield")
        if yld is None:
            continue
        for _, cat_node, ed in G.out_edges(rxn_node, data=True):
            if ed.get("edge_type") == "USES_CATALYST":
                cat_yields[cat_node].append(float(yld))

    rows = []
    for cat_node, yields in cat_yields.items():
        cdata = G.nodes[cat_node]
        rows.append(
            {
                "catalyst_key": cat_node,
                "name": display_name(cdata),
                "smiles": cdata.get("smiles_canonical", ""),
                "n_reactions": len(yields),
                "mean_yield": round(sum(yields) / len(yields), 1),
                "max_yield": max(yields),
                "min_yield": min(yields),
            }
        )
    df = pd.DataFrame(rows)
    if df.empty:
        return df
    return df.sort_values("mean_yield", ascending=False).reset_index(drop=True)


def paper_overlap(G: nx.MultiDiGraph) -> pd.DataFrame:
    """
    Which compounds appear in more than one paper.
    Returns DataFrame with compound info and list of papers.
    """
    rows = []
    for node, data in G.nodes(data=True):
        if data.get("node_type") != "Compound":
            continue
        papers = data.get("papers", [])
        if len(papers) > 1:
            rows.append(
                {
                    "inchikey": node,
                    "smiles": data.get("smiles_canonical", ""),
                    "name": display_name(data),
                    "n_papers": len(papers),
                    "papers": "; ".join(papers),
                }
            )
    df = pd.DataFrame(rows)
    if df.empty:
        return df
    return df.sort_values("n_papers", ascending=False).reset_index(drop=True)
