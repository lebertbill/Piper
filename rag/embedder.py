"""
GeminiEmbedder — calls OpenRouter's google/gemini-embedding-2-preview endpoint.

Uses raw httpx calls (same pattern as llm_utils.py) rather than the openai SDK,
which rejects the OpenRouter response format with "No embedding data received".

Embedding: 3072-dim, natively multimodal.
gemini-embedding-2-preview accepts image_url content on OpenRouter's embeddings
endpoint via the structured `{"content": [{"type": "text"...}, {"type":
"image_url"...}]}` input format (in addition to plain string input for
text-only items).

For chunks that have an image_path (multimodal chunks), the image is base64
inlined alongside its caption/description text in a single embedding call, so
the stored vector reflects both the figure content and its text — this is the
true multimodal embedding, computed once at indexing time. At synthesis time,
handle_hybrid_graphrag_query additionally passes the image to gemini-2.5-flash
for generative visual reasoning over the answer.
"""
from __future__ import annotations

import base64
import mimetypes
import os
from pathlib import Path

import httpx
import numpy as np

_API_URL = "https://openrouter.ai/api/v1/embeddings"
_MODEL = "google/gemini-embedding-2-preview"
_DIM = 3072
_TIMEOUT = 60


def _image_data_uri(image_path: str) -> str | None:
    """Read an image file and return a base64 data URI, or None on failure."""
    try:
        mime, _ = mimetypes.guess_type(image_path)
        mime = mime or "image/png"
        data = Path(image_path).read_bytes()
        b64 = base64.b64encode(data).decode("ascii")
        return f"data:{mime};base64,{b64}"
    except Exception as e:
        print(f"[GeminiEmbedder] failed to read image {image_path}: {e}")
        return None


def _normalise(vec: list[float]) -> np.ndarray:
    arr = np.array(vec, dtype=np.float32)
    norm = np.linalg.norm(arr)
    return arr / norm if norm > 0 else arr


class GeminiEmbedder:
    """
    Embeds text (and optionally images) via OpenRouter's embeddings endpoint.
    Model is read from context.json's embedding.embedding_model, falling back to
    google/gemini-embedding-2-preview if unset. Uses the existing
    OPENROUTER_API_KEY — no new credentials needed.
    """

    DIM = _DIM

    def __init__(self, api_key: str | None = None, model: str | None = None):
        self._api_key = api_key or os.getenv("OPENROUTER_API_KEY") or os.getenv("API_KEY")
        if not self._api_key:
            raise ValueError("OPENROUTER_API_KEY not set in environment.")

        if model is not None:
            self.MODEL = model
        else:
            try:
                from context import load_config
                self.MODEL = load_config().get("embedding", {}).get("embedding_model") or _MODEL
            except Exception:
                self.MODEL = _MODEL

        self._headers = {
            "Authorization": f"Bearer {self._api_key}",
            "Content-Type": "application/json",
            "HTTP-Referer": "http://localhost:3000",
            "X-Title": "Piper-RAG",
        }

    def _post(self, input_data) -> list[list[float]]:
        """POST to the embeddings endpoint, return list of embedding vectors."""
        resp = httpx.post(
            _API_URL,
            headers=self._headers,
            json={"model": self.MODEL, "input": input_data},
            timeout=_TIMEOUT,
        )
        resp.raise_for_status()
        data = resp.json().get("data", [])
        if not data:
            raise ValueError(f"Empty embedding response for input: {str(input_data)[:60]}")
        return [item["embedding"] for item in data]

    def embed(self, text: str) -> np.ndarray | None:
        """Embed a single text. Returns L2-normalised float32 array, or None on failure."""
        try:
            vecs = self._post(text)
            return _normalise(vecs[0])
        except Exception as e:
            print(f"[GeminiEmbedder] embed failed: {e}")
            return None

    def embed_multimodal(self, text: str, image_path: str) -> np.ndarray | None:
        """
        Embed text + image together as one multimodal input, so the returned
        vector reflects the figure's visual content, not just its caption.
        Falls back to text-only embedding if the image can't be read or the
        request fails.
        """
        data_uri = _image_data_uri(image_path)
        if data_uri is None:
            return self.embed(text)

        content = []
        if text:
            content.append({"type": "text", "text": text})
        content.append({"type": "image_url", "image_url": {"url": data_uri}})

        try:
            vecs = self._post([{"content": content}])
            return _normalise(vecs[0])
        except Exception as e:
            print(f"[GeminiEmbedder] multimodal embed failed for {image_path}: {e}")
            return self.embed(text)

    def embed_batch(
        self,
        items: list[dict],
        batch_size: int = 20,
    ) -> list[np.ndarray | None]:
        """
        Embed a batch of items.
        Each item: {"text": str, "image_path": str | None}

        Items with an image_path are embedded multimodally (image + text in a
        single request) via embed_multimodal. Text-only items are still
        batched together for efficiency.

        Returns list of L2-normalised float32 arrays (None for failures).
        """
        results: list[np.ndarray | None] = [None] * len(items)

        text_indices = [i for i, item in enumerate(items) if not item.get("image_path")]
        image_indices = [i for i, item in enumerate(items) if item.get("image_path")]

        texts = [items[i]["text"] for i in text_indices]
        for start in range(0, len(texts), batch_size):
            batch_texts = texts[start: start + batch_size]
            batch_indices = text_indices[start: start + batch_size]
            try:
                vecs = self._post(batch_texts)
                for j, vec in enumerate(vecs):
                    results[batch_indices[j]] = _normalise(vec)
            except Exception as e:
                print(f"[GeminiEmbedder] batch embed failed: {e}")
                # Retry individually
                for idx, text in zip(batch_indices, batch_texts):
                    results[idx] = self.embed(text)

        for idx in image_indices:
            item = items[idx]
            results[idx] = self.embed_multimodal(item["text"], item["image_path"])

        return results
