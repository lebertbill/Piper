"""
HybridQueryEngine: wraps HybridRetriever with optional LLM synthesis.
"""
from __future__ import annotations

import networkx as nx
from miner.agents.base_agent import BaseAgent
from .retriever import HybridRetriever, FusedResult

_SYSTEM_PROMPT = """\
You are a chemistry research assistant specialising in synthetic organic chemistry.

You are given a list of reactions from a knowledge graph that structurally match a
query scaffold (SMARTS/SMILES substructure). Summarise the reaction landscape:
recurring catalysts, conditions that correlate with high yield, and structural trends.

Rules:
- Only reference reactions in the provided list.
- Cite paper names and entry IDs (format: "Paper: X, Entry: Y").
- Report numerical values exactly — do not average or round.
- If fewer than 3 reactions are provided, note the limited evidence.\
"""


class HybridQueryEngine(BaseAgent):
    """
    Substructure-aware three-channel retrieval + optional LLM synthesis.
    Inherits _call_llm and _assemble_prompt from BaseAgent.
    """

    NAME = "Substructure Search Agent"
    DESCRIPTION = "Matches SMARTS/SMILES patterns against compound nodes, traverses reaction edges, and optionally re-ranks by reaction vector similarity"

    def __init__(
        self,
        G: nx.MultiDiGraph,
        reaction_index=None,
        model: str = "openai/gpt-4o",
        hop_depth: int = 2,
        w_graph: float = 0.4,
        w_vector: float = 0.6,
        max_results: int = 50,
    ):
        super().__init__(model=model)
        self.retriever = HybridRetriever(
            G=G,
            reaction_index=reaction_index,
            hop_depth=hop_depth,
            w_graph=w_graph,
            w_vector=w_vector,
            max_results=max_results,
        )

    def search(
        self,
        smarts_query: str,
        reaction_smiles: str | None = None,
        synthesise_answer: bool = False,
    ) -> dict:
        """
        Returns:
            {results, answer, n_compound_matches, n_reaction_candidates}
        """
        compound_matches = self.retriever._channel_symbolic(smarts_query)
        graph_candidates = self.retriever._channel_graph(compound_matches) if compound_matches else {}

        vector_scores: dict[str, float] = {}
        has_vector = False
        if reaction_smiles and self.retriever._reaction_index is not None:
            vector_scores = self.retriever._channel_vector(
                list(graph_candidates.keys()), reaction_smiles
            )
            has_vector = bool(vector_scores)

        results = self.retriever._fuse(graph_candidates, vector_scores, has_vector)

        answer = None
        if synthesise_answer and results:
            messages = self._build_synthesis_messages(smarts_query, results)
            response = self._call_llm(messages)
            answer = response.choices[0].message.content.strip()

        return {
            "results": results,
            "answer": answer,
            "n_compound_matches": len(compound_matches),
            "n_reaction_candidates": len(graph_candidates),
        }

    def run(self, input_data: dict) -> dict:
        return self.search(
            smarts_query=input_data["smarts_query"],
            reaction_smiles=input_data.get("reaction_smiles"),
            synthesise_answer=input_data.get("synthesise_answer", False),
        )

    def _build_synthesis_messages(
        self, smarts_query: str, results: list[FusedResult]
    ) -> list[dict]:
        rows = ["| Rank | Paper | Entry | Yield | Temp | Score |",
                "|------|-------|-------|-------|------|-------|"]
        for i, r in enumerate(results[:30], 1):
            rows.append(
                f"| {i} | {r.paper[:40]} | {r.entry_id} | "
                f"{r.yield_val or '—'}% | {r.temperature or '—'}°C | {r.final_score:.3f} |"
            )

        user_content = (
            f"Query scaffold: `{smarts_query}`\n\n"
            f"Matched reactions ({len(results)} total, top 30 shown):\n\n"
            + "\n".join(rows)
            + "\n\nSummarise the key patterns across these reactions."
        )
        return self._assemble_prompt(_SYSTEM_PROMPT, user_content)
