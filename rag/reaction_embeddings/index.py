"""
FAISS-based nearest-neighbor index over reaction embeddings.
Stores metadata (paper, table, entry_id, yield, reaction_smiles) alongside each vector.
"""
from __future__ import annotations

import json
from pathlib import Path

import numpy as np


class ReactionIndex:
    def __init__(self, embedder=None):
        self._embedder = embedder
        self._index = None
        self._meta: list[dict] = []
        self._dim: int = 0

    def build(self, corpus: list[dict]) -> None:
        """
        Embeds all items in corpus and builds FAISS IndexFlatIP (inner product / cosine after L2 norm).

        Args:
            corpus: list of {reaction_smiles, paper, table_name, entry_id, yield}
        """
        try:
            import faiss
        except ImportError:
            raise ImportError("Install faiss: pip install faiss-cpu")

        assert self._embedder is not None, "Pass an embedder to ReactionIndex()"

        smiles_list = [item["reaction_smiles"] for item in corpus]
        raw_vecs = self._embedder.embed_batch(smiles_list)

        valid_vecs = []
        valid_meta = []
        for vec, item in zip(raw_vecs, corpus):
            if vec is not None:
                valid_vecs.append(vec)
                valid_meta.append(item)

        if not valid_vecs:
            raise ValueError("No valid embeddings produced — check reaction SMILES validity.")

        matrix = np.stack(valid_vecs).astype(np.float32)
        # L2-normalise for cosine similarity via inner product
        norms = np.linalg.norm(matrix, axis=1, keepdims=True)
        norms = np.where(norms == 0, 1, norms)
        matrix /= norms

        self._dim = matrix.shape[1]
        self._index = faiss.IndexFlatIP(self._dim)
        self._index.add(matrix)
        self._meta = valid_meta
        print(f"ReactionIndex: built with {len(valid_meta)} reactions (dim={self._dim})")

    def search(self, reaction_smiles: str, k: int = 10) -> list[dict]:
        """
        Returns top-k most similar reactions to the query.
        Each result dict includes all metadata + 'score' (cosine similarity).
        """
        import faiss  # noqa: F401

        assert self._index is not None, "Call build() first"
        vec = self._embedder.embed(reaction_smiles)
        if vec is None:
            return []
        vec = vec.astype(np.float32).reshape(1, -1)
        norm = np.linalg.norm(vec)
        if norm > 0:
            vec /= norm

        scores, indices = self._index.search(vec, min(k, len(self._meta)))
        results = []
        for score, idx in zip(scores[0], indices[0]):
            if idx < 0:
                continue
            item = dict(self._meta[idx])
            item["score"] = round(float(score), 4)
            results.append(item)
        return results

    def save(self, index_path: str | Path, meta_path: str | Path | None = None) -> None:
        try:
            import faiss
        except ImportError:
            raise ImportError("Install faiss: pip install faiss-cpu")
        index_path = Path(index_path)
        index_path.parent.mkdir(parents=True, exist_ok=True)
        faiss.write_index(self._index, str(index_path))
        meta_path = meta_path or index_path.with_suffix(".meta.json")
        Path(meta_path).write_text(json.dumps(self._meta, indent=2))
        print(f"Index saved → {index_path}")

    def load(self, index_path: str | Path, meta_path: str | Path | None = None) -> None:
        try:
            import faiss
        except ImportError:
            raise ImportError("Install faiss: pip install faiss-cpu")
        index_path = Path(index_path)
        self._index = faiss.read_index(str(index_path))
        self._dim = self._index.d
        meta_path = meta_path or index_path.with_suffix(".meta.json")
        self._meta = json.loads(Path(meta_path).read_text())

    def add_similar_reaction_edges(self, G, threshold: float = 0.85) -> None:
        """
        Enriches a NetworkX reaction graph with SIMILAR_REACTION edges
        between reaction nodes whose rxnfp cosine similarity >= threshold.
        """
        import faiss  # noqa: F401

        assert self._index is not None, "Call build() or load() first"
        n = len(self._meta)
        # Retrieve top-20 neighbours for every reaction in the index
        matrix = self._index.reconstruct_n(0, n)
        scores_mat, idx_mat = self._index.search(matrix, min(21, n))

        for i, (scores, idxs) in enumerate(zip(scores_mat, idx_mat)):
            meta_i = self._meta[i]
            node_i = f"{meta_i['paper']}__{meta_i['table_name']}__{meta_i['entry_id']}"
            if node_i not in G:
                continue
            for score, j in zip(scores, idxs):
                if j <= i or j < 0 or score < threshold:
                    continue
                meta_j = self._meta[j]
                node_j = f"{meta_j['paper']}__{meta_j['table_name']}__{meta_j['entry_id']}"
                if node_j not in G:
                    continue
                G.add_edge(node_i, node_j, edge_type="SIMILAR_REACTION", cosine=round(float(score), 4))
                G.add_edge(node_j, node_i, edge_type="SIMILAR_REACTION", cosine=round(float(score), 4))
