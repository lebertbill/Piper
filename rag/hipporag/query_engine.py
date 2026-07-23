"""
HippoQueryEngine: wraps HippoRetriever with LLM synthesis via BaseAgent.
"""
from __future__ import annotations

import networkx as nx
from miner.agents.base_agent import BaseAgent
from .retriever import HippoRetriever, RetrievedContext

_SYSTEM_PROMPT = """\
You are a chemistry research assistant specialising in synthetic organic chemistry,
catalysis, and reaction optimisation.

You answer questions using ONLY the reaction network context provided below — a
graph-retrieved subgraph of compounds and reactions from published chemistry papers.

Rules:
- Ground every claim in the provided context. Cite paper names and entry IDs when
  referencing specific reactions (format: "Paper: X, Entry: Y").
- Report yields, temperatures, and times exactly as given — do not average or round.
- If the context is insufficient to answer confidently, say so clearly.
- When comparing reactions, use a concise bullet list or table.
- Do not invent SMILES strings or conditions not present in the context.\
"""


class HippoQueryEngine(BaseAgent):
    """
    Personalized PageRank retrieval + LLM synthesis over the reaction graph.
    Inherits _call_llm, _assemble_prompt, and model config from BaseAgent.
    """

    NAME = "HippoRAG Agent"
    DESCRIPTION = "Seeds Personalized PageRank from query-matching graph nodes and diffuses relevance across the reaction network"

    def __init__(
        self,
        G: nx.MultiDiGraph,
        reaction_index=None,
        model: str = "openai/gpt-4o",
        alpha: float = 0.85,
        top_k_seeds: int = 5,
        top_n_context: int = 20,
    ):
        super().__init__(model=model)
        self.retriever = HippoRetriever(
            G=G,
            reaction_index=reaction_index,
            alpha=alpha,
            top_k_seeds=top_k_seeds,
            top_n_context=top_n_context,
        )

    def ask(self, query: str) -> dict:
        """
        Full pipeline: retrieve context via PPR → synthesise answer with LLM.

        Returns:
            {answer, seed_nodes, ranked_nodes, context_text, node_details}
        """
        ctx: RetrievedContext = self.retriever.retrieve(query)
        messages = self._build_messages(query, ctx.context_text)
        response = self._call_llm(messages)
        answer = response.choices[0].message.content.strip()
        return {
            "answer": answer,
            "seed_nodes": ctx.seed_nodes,
            "ranked_nodes": ctx.ranked_nodes,
            "context_text": ctx.context_text,
            "node_details": ctx.node_details,
        }

    def run(self, input_data: dict) -> dict:
        return self.ask(input_data["query"])

    def _build_messages(self, query: str, context_text: str) -> list[dict]:
        user_content = (
            f"Question: {query}\n\n"
            f"Retrieved context (graph-diffused neighbourhood, ranked by PPR score):\n"
            f"---\n{context_text}\n---\n\n"
            f"Answer the question based solely on the context above."
        )
        return self._assemble_prompt(_SYSTEM_PROMPT, user_content)
