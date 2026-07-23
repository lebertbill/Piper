"""
Personalized PageRank (PPR) utilities for HippoRAG-style graph retrieval.

PPR is run on an undirected view of the reaction graph so relevance
diffuses in all directions from the seed nodes, not just along directed edges.
When no seeds are found (empty query match), falls back to global PageRank.
"""
from __future__ import annotations

import networkx as nx


def _build_undirected(G: nx.MultiDiGraph) -> nx.Graph:
    """
    Collapses the MultiDiGraph into a simple undirected Graph.
    For edges with a 'tanimoto' weight, keeps the max weight between any pair.
    """
    UG = nx.Graph()
    UG.add_nodes_from(G.nodes(data=True))
    for u, v, data in G.edges(data=True):
        w = data.get("tanimoto") or data.get("cosine") or 1.0
        if UG.has_edge(u, v):
            if w > UG[u][v].get("weight", 0):
                UG[u][v]["weight"] = w
        else:
            UG.add_edge(u, v, weight=w)
    return UG


def personalized_pagerank(
    G: nx.MultiDiGraph,
    seed_nodes: list[str],
    alpha: float = 0.85,
    max_nodes: int = 200,
) -> dict[str, float]:
    """
    Run PPR with uniform mass over seed_nodes on an undirected view of G.

    Args:
        G: the reaction network graph
        seed_nodes: list of node keys to personalise toward
        alpha: teleportation probability (0.85 = standard HippoRAG setting)
        max_nodes: return only top-N nodes by PPR score

    Returns:
        {node_key: ppr_score} sorted descending, length ≤ max_nodes
    """
    UG = _build_undirected(G)

    valid_seeds = [n for n in seed_nodes if n in UG]
    if valid_seeds:
        personalization = {n: 1.0 / len(valid_seeds) for n in valid_seeds}
    else:
        personalization = None  # falls back to global PageRank

    scores: dict[str, float] = nx.pagerank(
        UG,
        alpha=alpha,
        personalization=personalization,
        weight="weight",
        max_iter=200,
        tol=1e-6,
    )

    top = sorted(scores.items(), key=lambda x: x[1], reverse=True)[:max_nodes]
    return dict(top)


def seed_nodes_from_query(
    G: nx.MultiDiGraph,
    query: str,
    reaction_index=None,
    k: int = 5,
) -> list[str]:
    """
    Finds seed nodes in G matching the query.

    Strategy:
      1. If query contains ">>" → reaction SMILES, call reaction_index.search().
      2. Text scan: Compound aliases/names, Temperature, Yield, Reaction, Concept nodes.
      3. Multi-hop expansion: follow SIMILAR_TO and SUBSTRUCTURE_OF edges from matched
         Compound nodes to surface structurally related compounds.
      4. Concept boost: if a Concept node matches, also seed its linked Reaction nodes.

    Returns [] when nothing matches (caller falls back to global PageRank).
    """
    query_lower = query.strip().lower()

    # ── Path 1: reaction SMILES ──────────────────────────────────────────────
    if ">>" in query and reaction_index is not None:
        try:
            results = reaction_index.search(query, k=k)
            keys = []
            for r in results:
                node_key = f"{r['paper']}__{r['table_name']}__{r['entry_id']}"
                if node_key in G:
                    keys.append(node_key)
            return keys
        except Exception:
            pass

    # ── Path 2: text scan ─────────────────────────────────────────────────────
    matched: list[str] = []
    concept_matches: list[str] = []

    for node, data in G.nodes(data=True):
        ntype = data.get("node_type", "")

        if ntype == "Compound":
            texts = (data.get("aliases", [])
                     + [data.get("label_text", ""),
                        data.get("smiles_canonical", ""),
                        data.get("pubchem_name", ""),
                        data.get("context_resolved_name", "")])
            if any(query_lower in str(t).lower() for t in texts if t):
                matched.append(node)

        elif ntype == "Temperature":
            label = data.get("label", "")
            val_str = str(data.get("value", ""))
            if query_lower in label.lower() or query_lower in val_str:
                matched.append(node)

        elif ntype in ("YieldRange", "Yield"):
            if query_lower in data.get("label", "").lower():
                matched.append(node)

        elif ntype == "Reaction":
            paper = data.get("paper", "").lower()
            table = data.get("table", "").lower()
            if query_lower in paper or query_lower in table:
                matched.append(node)

        elif ntype == "Concept":
            # Match against transformation type, catalyst class, application summary
            texts = [
                data.get("transformation", ""),
                data.get("catalyst_type", ""),
                data.get("application", ""),
                data.get("label", ""),
            ]
            if any(query_lower in str(t).lower() for t in texts if t):
                concept_matches.append(node)

        if len(matched) >= k:
            break

    # ── Path 3: Concept node boost — seed linked Reaction nodes ──────────────
    # A Concept match means the query is thematically aligned with the paper's
    # transformation class; pull in the reactions under that concept directly.
    for concept_node in concept_matches:
        for _, nbr, ed in G.out_edges(concept_node, data=True):
            if G.nodes[nbr].get("node_type") == "Reaction" and nbr not in matched:
                matched.append(nbr)
                if len(matched) >= k * 2:
                    break

    # ── Path 4: Multi-hop expansion via structural similarity edges ───────────
    # Follow SIMILAR_TO and SUBSTRUCTURE_OF from matched Compound nodes so that
    # structurally related compounds (not explicitly named) are also seeded.
    if len(matched) < k:
        extra: set[str] = set()
        _structural_edge_types = {"SIMILAR_TO", "SUBSTRUCTURE_OF"}
        for node in list(matched):
            if G.nodes[node].get("node_type") != "Compound":
                continue
            for _, nbr, ed in G.out_edges(node, data=True):
                if ed.get("edge_type") in _structural_edge_types and nbr not in matched:
                    extra.add(nbr)
                    if len(matched) + len(extra) >= k:
                        break
        matched.extend(list(extra)[: k - len(matched)])

    return matched
