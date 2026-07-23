"""
HybridRetriever: three-channel retrieval fusion.

Channel 1 — Symbolic:  SMARTS substructure match → matched Compound nodes
Channel 2 — Graph:     BFS from matched compounds → connected Reaction nodes (hop 1 & 2)
Channel 3 — Vector:    ReactionIndex cosine search → re-rank candidates (optional)

Fusion: final_score = w_graph * graph_score + w_vector * vector_score
        (w_vector = 0 when no reaction_smiles provided)
"""
from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field

import networkx as nx

from .substructure import SubstructureMatcher, SubstructureMatch
from ..graph.utils import display_name

_REACTION_EDGE_TYPES = {"HAS_REACTANT", "HAS_PRODUCT", "USES_CATALYST", "USES_REAGENT"}


@dataclass
class FusedResult:
    reaction_key: str
    paper: str
    table: str
    entry_id: str
    yield_val: float | None
    reaction_smiles: str | None
    matched_compounds: list[str]        # InChIKeys that led to this reaction
    hop_distance: int                   # 1 = direct, 2 = two-hop
    vector_score: float | None          # cosine similarity from FAISS, or None
    graph_score: float                  # 1.0 (hop=1) or 0.5 (hop=2)
    final_score: float
    catalyst_labels: list[str] = field(default_factory=list)
    solvent_smiles: str | None = None
    temperature: float | None = None


class HybridRetriever:
    NAME = "Hybrid Retriever"
    DESCRIPTION = "Three-channel fusion: symbolic SMARTS match → graph BFS traversal → vector re-ranking via RRF"

    def __init__(
        self,
        G: nx.MultiDiGraph,
        reaction_index=None,
        hop_depth: int = 2,
        w_graph: float = 0.4,
        w_vector: float = 0.6,
        max_results: int = 50,
    ):
        self._G = G
        self._reaction_index = reaction_index
        self._matcher = SubstructureMatcher(G)
        self._hop_depth = hop_depth
        self._w_graph = w_graph
        self._w_vector = w_vector
        self._max_results = max_results

    def retrieve(
        self,
        smarts_query: str,
        reaction_smiles: str | None = None,
    ) -> list[FusedResult]:
        compound_matches = self._channel_symbolic(smarts_query)
        if not compound_matches:
            return []

        graph_candidates = self._channel_graph(compound_matches)
        if not graph_candidates:
            return []

        vector_scores: dict[str, float] = {}
        has_vector = False
        if reaction_smiles and self._reaction_index is not None:
            vector_scores = self._channel_vector(
                list(graph_candidates.keys()), reaction_smiles
            )
            has_vector = bool(vector_scores)

        return self._fuse(graph_candidates, vector_scores, has_vector)

    # ── Channel 1: Symbolic ───────────────────────────────────────────────────

    def _channel_symbolic(self, smarts_query: str) -> list[SubstructureMatch]:
        return self._matcher.match(smarts_query)

    # ── Channel 2: Graph BFS ──────────────────────────────────────────────────

    def _channel_graph(
        self, matched_compounds: list[SubstructureMatch]
    ) -> dict[str, tuple[int, list[str]]]:
        """
        BFS from matched compound nodes outward through the reaction graph.
        Returns {reaction_key: (hop_distance, [source_compound_keys])}.

        Hop 1: edges pointing INTO the compound node (reaction→compound direction)
               filtered by HAS_REACTANT / HAS_PRODUCT / USES_CATALYST / USES_REAGENT.
        Hop 2: from hop-1 reaction neighbours, walk to their OTHER compound
               neighbours, then extend via SIMILAR_TO / SUBSTRUCTURE_OF edges,
               then repeat hop-1 traversal from those new compounds.
        """
        G = self._G
        seed_keys = {m.node_key for m in matched_compounds}
        results: dict[str, tuple[int, list[str]]] = {}

        def reactions_from_compound(compound_key: str) -> list[str]:
            rxns = []
            for src, _, ed in G.in_edges(compound_key, data=True):
                if ed.get("edge_type") in _REACTION_EDGE_TYPES:
                    if G.nodes[src].get("node_type") == "Reaction":
                        rxns.append(src)
            return rxns

        # Hop 1
        hop1_reactions: set[str] = set()
        for ck in seed_keys:
            for rxn_key in reactions_from_compound(ck):
                if rxn_key not in results:
                    results[rxn_key] = (1, [ck])
                else:
                    results[rxn_key][1].append(ck)
                hop1_reactions.add(rxn_key)

        if self._hop_depth < 2:
            return results

        # Hop 2: from hop-1 reactions → other compound neighbours → extend via SIMILAR_TO
        hop2_compounds: set[str] = set()
        for rxn_key in hop1_reactions:
            for _, nbr, ed in G.out_edges(rxn_key, data=True):
                if ed.get("edge_type") in _REACTION_EDGE_TYPES and nbr not in seed_keys:
                    hop2_compounds.add(nbr)

        # Also include SIMILAR_TO / SUBSTRUCTURE_OF neighbours of seed compounds
        for ck in seed_keys:
            for _, nbr, ed in G.out_edges(ck, data=True):
                if ed.get("edge_type") in {"SIMILAR_TO", "SUBSTRUCTURE_OF"}:
                    hop2_compounds.add(nbr)

        for ck in hop2_compounds:
            for rxn_key in reactions_from_compound(ck):
                if rxn_key not in results:
                    results[rxn_key] = (2, [ck])

        return results

    # ── Channel 3: Vector ─────────────────────────────────────────────────────

    def _channel_vector(
        self, reaction_keys: list[str], reaction_smiles: str
    ) -> dict[str, float]:
        k = min(len(reaction_keys) + 20, len(self._reaction_index._meta))
        try:
            search_results = self._reaction_index.search(reaction_smiles, k=k)
        except Exception:
            return {}

        scores: dict[str, float] = {}
        for r in search_results:
            # Reconstruct node key using table_name (from corpus meta)
            node_key = f"{r['paper']}__{r['table_name']}__{r['entry_id']}"
            if node_key in reaction_keys:
                scores[node_key] = r["score"]
        return scores

    # ── Fusion ────────────────────────────────────────────────────────────────

    def _fuse(
        self,
        graph_candidates: dict[str, tuple[int, list[str]]],
        vector_scores: dict[str, float],
        has_vector: bool,
    ) -> list[FusedResult]:
        w_g = self._w_graph if has_vector else 1.0
        w_v = self._w_vector if has_vector else 0.0

        fused: dict[str, FusedResult] = {}
        for rxn_key, (hop, compounds) in graph_candidates.items():
            g_score = 1.0 if hop == 1 else 0.5
            v_score = vector_scores.get(rxn_key)
            final = w_g * g_score + w_v * (v_score or 0.0)

            if rxn_key in fused and fused[rxn_key].hop_distance <= hop:
                continue  # keep the better (lower hop) entry

            result = self._enrich_result(rxn_key, hop, compounds)
            result.vector_score = v_score
            result.graph_score = g_score
            result.final_score = round(final, 4)
            fused[rxn_key] = result

        ranked = sorted(fused.values(), key=lambda r: r.final_score, reverse=True)
        return ranked[: self._max_results]

    def _enrich_result(
        self, reaction_key: str, hop: int, compounds: list[str]
    ) -> FusedResult:
        G = self._G
        data = G.nodes.get(reaction_key, {})

        # Gather reaction SMILES from FAISS meta if available
        rxn_smiles = None
        if self._reaction_index is not None:
            parts = reaction_key.split("__", 2)
            if len(parts) == 3:
                paper, table, entry_id = parts
                for m in self._reaction_index._meta:
                    if (
                        m["paper"] == paper
                        and m["table_name"] == table
                        and str(m["entry_id"]) == str(entry_id)
                    ):
                        rxn_smiles = m.get("reaction_smiles")
                        break

        # Gather catalyst labels
        catalysts = []
        for _, nbr, ed in G.out_edges(reaction_key, data=True):
            if ed.get("edge_type") == "USES_CATALYST":
                catalysts.append(display_name(G.nodes[nbr], max_len=30))

        return FusedResult(
            reaction_key=reaction_key,
            paper=data.get("paper", ""),
            table=data.get("table", ""),
            entry_id=str(data.get("entry_id", "")),
            yield_val=data.get("yield"),
            reaction_smiles=rxn_smiles,
            matched_compounds=compounds,
            hop_distance=hop,
            vector_score=None,
            graph_score=0.0,
            final_score=0.0,
            catalyst_labels=catalysts,
            solvent_smiles=data.get("solvent_smiles"),
            temperature=data.get("temperature"),
        )
