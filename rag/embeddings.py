"""
EmbeddingRetriever — drop-in replacement for the old ChromaDB/BAAI implementation.

Internals: Gemini Embedding 2 (google/gemini-embedding-2-preview via OpenRouter) + FAISS.
Public interface is identical so ingestion.py, retriever_logic.py, and app.py need no changes.

Storage layout (under db_path/):
  papers.faiss        — IndexFlatIP, L2-normalised vectors (3072-dim)
  papers_meta.json    — list[dict] parallel to FAISS rows
    each dict: {id, page_content, title, authors, year, journal,
                DOI, content_hash, chunk_index, item_type, image_path?}

Score convention: retrieve_with_scores returns (doc, 1.0 - cosine_sim) so that
"lower = better" holds, keeping dynamic_k_retrieval() in retriever_logic.py unchanged.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Dict, List, Tuple

import faiss
import numpy as np
from langchain_core.documents import Document

from context import load_config
from .embedder import GeminiEmbedder

_INDEX_FILE = "papers.faiss"
_META_FILE = "papers_meta.json"


class EmbeddingRetriever:
    def __init__(self, db_path: str, model_name: str):
        # model_name kept for interface compatibility; Gemini model is used internally
        print(f"Initializing EmbeddingRetriever with Gemini Embedding 2 (path: {db_path})")
        config = load_config()

        self.db_path = Path(db_path)
        self.db_path.mkdir(parents=True, exist_ok=True)

        api_key = os.getenv("OPENROUTER_API_KEY") or os.getenv("API_KEY")
        self._embedder = GeminiEmbedder(api_key=api_key)
        self._dim = GeminiEmbedder.DIM

        self._index_path = self.db_path / _INDEX_FILE
        self._meta_path = self.db_path / _META_FILE

        # Load or create FAISS index
        if self._index_path.exists() and self._meta_path.exists():
            try:
                self._index = faiss.read_index(str(self._index_path))
                self._meta: list[dict] = json.loads(self._meta_path.read_text())
                # Validate index/metadata are in sync
                if self._index.ntotal != len(self._meta):
                    print(f"WARNING: FAISS index has {self._index.ntotal} vectors but metadata has "
                          f"{len(self._meta)} entries — rebuilding fresh index.")
                    self._index = faiss.IndexFlatIP(self._dim)
                    self._meta = []
                else:
                    print(f"Loaded FAISS index: {len(self._meta)} documents from '{self.db_path}'")
            except Exception as e:
                print(f"WARNING: Failed to load existing index ({e}) — starting fresh.")
                self._index = faiss.IndexFlatIP(self._dim)
                self._meta = []
        else:
            self._index = faiss.IndexFlatIP(self._dim)
            self._meta = []
            print(f"Created new FAISS index (dim={self._dim}) at '{self.db_path}'")

    def _save(self) -> None:
        # Write to temp files first, then rename atomically to avoid corruption
        tmp_index = self._index_path.with_suffix(".faiss.tmp")
        tmp_meta  = self._meta_path.with_suffix(".json.tmp")
        faiss.write_index(self._index, str(tmp_index))
        tmp_meta.write_text(json.dumps(self._meta, indent=2))
        tmp_index.replace(self._index_path)
        tmp_meta.replace(self._meta_path)

    def create_or_load_index(self, chunks: List, force_recreate: bool = False) -> None:
        """Add new documents to the FAISS index (upsert by content_hash+chunk_index)."""
        if not chunks:
            print("No chunks provided, skipping index update.")
            return

        if force_recreate:
            print("Forcing recreation of FAISS index…")
            self._index = faiss.IndexFlatIP(self._dim)
            self._meta = []

        # Deduplicate by ID (content_hash_chunk_index)
        existing_ids = {m.get("id") for m in self._meta}

        items: list[dict] = []
        new_meta: list[dict] = []

        for chunk in chunks:
            meta = dict(chunk.metadata)
            if "authors" in meta and isinstance(meta["authors"], list):
                meta["authors"] = ", ".join(meta["authors"])
            doc_id = f"{meta.get('content_hash')}_chunk_{meta.get('chunk_index')}"
            if doc_id in existing_ids:
                continue  # already indexed
            meta["id"] = doc_id
            meta["page_content"] = chunk.text
            image_path = meta.get("image_path")
            items.append({"text": chunk.text, "image_path": image_path})
            new_meta.append(meta)

        if not items:
            print("All chunks already indexed — nothing new to add.")
            return

        print(f"Embedding {len(items)} new chunks with Gemini Embedding 2…")
        vecs = self._embedder.embed_batch(items)

        valid_vecs, valid_meta = [], []
        for vec, m in zip(vecs, new_meta):
            if vec is not None:
                valid_vecs.append(vec)
                valid_meta.append(m)

        if not valid_vecs:
            print("No valid embeddings returned — index not updated.")
            return

        matrix = np.stack(valid_vecs).astype(np.float32)
        self._index.add(matrix)
        self._meta.extend(valid_meta)
        self._save()
        print(f"Indexed {len(valid_vecs)} chunks. Total: {len(self._meta)}")

    def retrieve(self, query: str, top_k: int = 3) -> List[Document]:
        """Semantic search, returns Documents."""
        return [doc for doc, _ in self.retrieve_with_scores(query, top_k=top_k)]

    def retrieve_with_scores(self, query: str, top_k: int = 10) -> List[Tuple[Document, float]]:
        """
        Semantic search with distance scores.
        Returns (Document, distance) where distance = 1.0 - cosine_sim
        so that 'lower = more similar' — compatible with dynamic_k_retrieval().
        """
        print(f"Semantic search (Gemini): '{query[:60]}'")
        if self._index.ntotal == 0:
            return []
        vec = self._embedder.embed(query)
        if vec is None:
            return []

        k = min(top_k, self._index.ntotal)
        scores, indices = self._index.search(vec.reshape(1, -1).astype(np.float32), k)

        results = []
        for sim, idx in zip(scores[0], indices[0]):
            if idx < 0 or idx >= len(self._meta):
                continue
            m = self._meta[idx]
            doc = Document(
                page_content=m.get("page_content", ""),
                metadata={k: v for k, v in m.items() if k != "page_content"},
            )
            distance = float(1.0 - sim)   # convert cosine similarity → distance
            results.append((doc, distance))
        return results

    def retrieve_by_threshold(self, query: str, min_similarity: float = 0.4) -> List[Tuple[Document, float]]:
        """
        Returns all chunks whose cosine similarity to the query is >= min_similarity.
        Uses FAISS range_search (IndexFlatIP) — no pool size needed.
        Results sorted by similarity descending.
        """
        if self._index.ntotal == 0:
            return []
        vec = self._embedder.embed(query)
        if vec is None:
            return []
        # range_search on IndexFlatIP: returns all vectors with inner_product >= min_similarity
        lims, distances, indices = self._index.range_search(
            vec.reshape(1, -1).astype(np.float32), min_similarity
        )
        results = []
        for sim, idx in zip(distances, indices):
            if idx < 0 or idx >= len(self._meta):
                continue
            m = self._meta[idx]
            doc = Document(
                page_content=m.get("page_content", ""),
                metadata={k: v for k, v in m.items() if k != "page_content"},
            )
            results.append((doc, float(1.0 - sim)))
        results.sort(key=lambda x: x[1])  # sort by distance ascending (most similar first)
        print(f"[ThresholdSearch] {len(results)} chunks with cosine_sim >= {min_similarity}")
        return results

    def retrieve_multimodal_with_scores(self, query: str, top_k: int = 4) -> List[Tuple[Document, float]]:
        """
        Search only among indexed image-bearing (multimodal) chunks.
        Figure/table chunks are a small minority of the index and rarely share enough
        literal vocabulary with a query to outrank the much larger pool of prose text
        chunks in a combined search — so this searches the image-only subset directly
        instead of requiring them to win a combined ranking.
        """
        mm_indices = [i for i, m in enumerate(self._meta)
                      if m.get("item_type") == "multimodal" and m.get("image_path")]
        if not mm_indices or self._index.ntotal == 0:
            return []
        vec = self._embedder.embed(query)
        if vec is None:
            return []

        all_vecs = self._index.reconstruct_n(0, self._index.ntotal)
        mm_vecs = all_vecs[mm_indices]
        sims = mm_vecs @ vec.astype(np.float32)
        k = min(top_k, len(mm_indices))
        order = np.argsort(-sims)[:k]

        results = []
        for rank in order:
            idx = mm_indices[rank]
            m = self._meta[idx]
            doc = Document(
                page_content=m.get("page_content", ""),
                metadata={k: v for k, v in m.items() if k != "page_content"},
            )
            results.append((doc, float(1.0 - sims[rank])))
        return results

    def retrieve_by_paper(self, paper_hint: str) -> List[Tuple[Document, float]]:
        """
        Returns all chunks belonging to a specific paper matched by hint (author/title fragment).
        Searches title, authors, and paper metadata fields. Score is set to 0.0 (perfect match intent).
        """
        hint = paper_hint.lower()
        results = []
        for m in self._meta:
            fields = [
                str(m.get("title", "")),
                str(m.get("authors", "")),
                str(m.get("paper", "")),
            ]
            if any(hint in f.lower() for f in fields):
                doc = Document(
                    page_content=m.get("page_content", ""),
                    metadata={k: v for k, v in m.items() if k != "page_content"},
                )
                results.append((doc, 0.0))
        print(f"[PaperSearch] {len(results)} chunks matched paper_hint={paper_hint!r}")
        return results

    def query_with_filter(self, where_filter: Dict[str, Any]) -> List[Dict[str, Any]]:
        """
        Metadata-only filter (no vector search).
        Supports exact match and {"$in": [...]} syntax (same as ChromaDB callers use).
        """
        results = []
        for m in self._meta:
            if _matches_filter(m, where_filter):
                results.append({
                    "page_content": m.get("page_content", ""),
                    "metadata": {k: v for k, v in m.items() if k != "page_content"},
                })
        return results

    def get_all_documents(self) -> List[Dict[str, Any]]:
        """Return all indexed documents."""
        return [
            {
                "page_content": m.get("page_content", ""),
                "metadata": {k: v for k, v in m.items() if k != "page_content"},
            }
            for m in self._meta
        ]

    def count(self) -> int:
        return len(self._meta)

    # Compatibility shim: retriever_logic.py accesses retriever.collection.count()
    # and retriever.index.similarity_search_with_score() in some paths.
    # These proxy objects cover those call sites.

    @property
    def collection(self):
        return _CollectionProxy(self)

    @property
    def index(self):
        return _IndexProxy(self)


class _CollectionProxy:
    """Mimics the chromadb Collection.count() call used in retriever_logic.py."""
    def __init__(self, retriever: EmbeddingRetriever):
        self._r = retriever

    def count(self) -> int:
        return self._r.count()


class _IndexProxy:
    """Mimics the LangChain Chroma.similarity_search_with_score() call used in handle_filtered_query."""
    def __init__(self, retriever: EmbeddingRetriever):
        self._r = retriever

    def similarity_search_with_score(
        self, query: str, k: int = 10, filter: Dict | None = None
    ) -> List[Tuple[Document, float]]:
        results = self._r.retrieve_with_scores(query, top_k=k * 3)
        if filter:
            results = [
                (doc, score)
                for doc, score in results
                if _matches_filter(doc.metadata, filter)
            ]
        return results[:k]


def _matches_filter(metadata: dict, where_filter: dict) -> bool:
    """
    Evaluate a metadata dict against a ChromaDB-style where_filter.
    Supports: {"key": value}  and  {"key": {"$in": [...]}}
    """
    for key, condition in where_filter.items():
        val = metadata.get(key)
        if isinstance(condition, dict):
            op = list(condition.keys())[0]
            if op == "$in":
                if val not in condition["$in"]:
                    return False
            elif op == "$eq":
                if val != condition["$eq"]:
                    return False
        else:
            if val != condition:
                return False
    return True
