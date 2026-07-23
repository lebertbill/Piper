"""
HippoRetriever: query → seed → PPR → ranked context assembly.
"""
from __future__ import annotations

from dataclasses import dataclass, field

import networkx as nx

from .ppr import personalized_pagerank, seed_nodes_from_query
from ..graph.utils import display_name

_REACTION_EDGE_TYPES = {"HAS_REACTANT", "HAS_PRODUCT", "USES_CATALYST", "USES_REAGENT", "CONDUCTED_IN", "USES_CONDITION"}


@dataclass
class RetrievedContext:
    query: str
    seed_nodes: list[str]
    ranked_nodes: list[tuple[str, float]]   # (node_key, ppr_score)
    context_text: str
    node_details: list[dict]


class HippoRetriever:
    NAME = "PPR Retriever"
    DESCRIPTION = "Finds seed nodes from the query then runs Personalized PageRank to rank all connected compounds and reactions"

    def __init__(
        self,
        G: nx.MultiDiGraph,
        reaction_index=None,
        alpha: float = 0.85,
        top_k_seeds: int = 5,
        top_n_context: int = 20,
    ):
        self._G = G
        self._reaction_index = reaction_index
        self._alpha = alpha
        self._top_k_seeds = top_k_seeds
        self._top_n_context = top_n_context

    def retrieve(self, query: str) -> RetrievedContext:
        seeds = seed_nodes_from_query(
            self._G, query, self._reaction_index, k=self._top_k_seeds
        )
        ppr_scores = personalized_pagerank(
            self._G, seeds, alpha=self._alpha, max_nodes=self._top_n_context * 3
        )
        ranked = list(ppr_scores.items())[: self._top_n_context]
        context_text, node_details = self._nodes_to_context(ranked)
        return RetrievedContext(
            query=query,
            seed_nodes=seeds,
            ranked_nodes=ranked,
            context_text=context_text,
            node_details=node_details,
        )

    def _nodes_to_context(
        self, ranked_nodes: list[tuple[str, float]]
    ) -> tuple[str, list[dict]]:
        G = self._G
        lines: list[str] = []
        details: list[dict] = []

        for rank, (node_key, score) in enumerate(ranked_nodes, 1):
            if node_key not in G:
                continue
            data = G.nodes[node_key]
            ntype = data.get("node_type", "")

            if ntype == "Compound":
                name = display_name(data)
                papers = ", ".join(data.get("papers", [])[:3])
                roles = ", ".join(data.get("roles", []))
                line = (
                    f"[{rank}] COMPOUND {name}"
                    + (f" | Role: {roles}" if roles else "")
                    + f" | SMILES: {data.get('smiles_canonical', '')}"
                    + (f" | Papers: {papers}" if papers else "")
                )
                details.append({"rank": rank, "type": "Compound", "key": node_key,
                                 "label": name, "smiles": data.get("smiles_canonical", ""),
                                 "ppr_score": score})

            elif ntype == "Reaction":
                yld = data.get("yield")
                temp = data.get("temperature")
                time_ = data.get("time")
                # Gather all participants with human-readable names
                reactants, products, catalysts, reagents, solvents, conditions = [], [], [], [], [], []
                for _, nbr, ed in G.out_edges(node_key, data=True):
                    etype = ed.get("edge_type", "")
                    nbr_label = display_name(G.nodes[nbr], max_len=30)
                    role = ed.get("specific_role", "")
                    if etype == "HAS_REACTANT":
                        reactants.append(nbr_label)
                    elif etype == "HAS_PRODUCT":
                        products.append(nbr_label)
                    elif etype == "USES_CATALYST":
                        catalysts.append(f"{nbr_label} ({role})" if role else nbr_label)
                    elif etype == "USES_REAGENT":
                        reagents.append(f"{nbr_label} ({role})" if role else nbr_label)
                    elif etype == "CONDUCTED_IN":
                        solvents.append(nbr_label)
                    elif etype == "USES_CONDITION":
                        conditions.append(nbr_label)
                # Also resolve yield from Yield node if not on reaction
                if yld is None:
                    for _, nbr, ed in G.out_edges(node_key, data=True):
                        if ed.get("edge_type") == "HAS_YIELD":
                            yld = G.nodes[nbr].get("value")
                            break
                line = (
                    f"[{rank}] REACTION {data.get('paper', '')[:35]} | "
                    f"Table {data.get('table', '')} Entry {data.get('entry_id', '')}"
                    + (f" | Yield: {yld}%" if yld is not None else "")
                    + (f" | Temp: {temp}°C" if temp is not None else "")
                    + (f" | Time: {time_}h" if time_ is not None else "")
                    + (f" | Solvent: {', '.join(solvents)}" if solvents else "")
                    + (f" | Reagents: {', '.join(reagents)}" if reagents else "")
                    + (f" | Reactants: {', '.join(reactants)}" if reactants else "")
                    + (f" → Products: {', '.join(products)}" if products else "")
                    + (f" | Catalyst: {', '.join(catalysts)}" if catalysts else "")
                    + (f" | Conditions: {', '.join(conditions)}" if conditions else "")
                )
                details.append({"rank": rank, "type": "Reaction", "key": node_key,
                                 "paper": data.get("paper", ""), "yield": yld,
                                 "ppr_score": score})
            else:
                continue

            lines.append(line)

        return "\n".join(lines), details
