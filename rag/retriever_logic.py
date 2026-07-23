from .embeddings import EmbeddingRetriever
from models import summarize_text, generate_final_answer, generate_recommendation_answer, _invoke_llm
from context import load_config, get_parameters
from .fusion import reciprocal_rank_fusion
import asyncio
from pathlib import Path
from typing import List, Dict
from langchain_core.documents import Document

# ── Adaptive RRF weights per query type ──────────────────────────────────────
# FAISS is strong for conceptual/mechanistic questions (broad text coverage).
# Graph is strong for precise condition lookups (yield, catalyst, entry data).
_RRF_WEIGHTS: dict[str, tuple[float, float]] = {
    "general_query":        (0.8, 0.2),
    "filtered_query":       (0.6, 0.4),
    "recommendation_query": (0.5, 0.5),
    "list_query":           (0.9, 0.1),
}
_RRF_DEFAULT = (0.6, 0.4)


_SCOPE_DEFAULTS = {
    "single_paper":   0.4,
    "single_subject": 0.4,
    "moderate":       0.5,
    "broad":          0.7,
}


async def _plan_retrieval(question: str, query_type: str, model: str) -> dict:
    """
    Agent that classifies retrieval scope and returns a plan dict:
      {scope, threshold (cosine sim), paper_hint}

    Scope -> strategy:
      single_paper   — fetch all chunks, filter to paper_hint match
      single_subject — fetch pool, keep cosine_sim >= 0.4
      moderate       — fetch pool, keep cosine_sim >= 0.5
      broad          — fetch pool, keep cosine_sim >= 0.7
    """
    import json as _json
    from models import load_prompt
    base = load_prompt("retrieval_plan_prompt.txt")
    prompt = base.format(query_type=query_type, question=question)
    raw = (await _invoke_llm(prompt, model) or "").strip()
    try:
        # strip markdown fences if present
        clean = raw.replace("```json", "").replace("```", "").strip()
        plan = _json.loads(clean)
        scope = plan.get("scope", "moderate")
        threshold = float(plan.get("threshold", _SCOPE_DEFAULTS.get(scope, 0.5)))
        threshold = max(0.1, min(0.95, threshold))
        paper_hint = plan.get("paper_hint") or None
        print(f"[RetrievalPlan] scope={scope!r} threshold={threshold} paper_hint={paper_hint!r}")
        return {"scope": scope, "threshold": threshold, "paper_hint": paper_hint}
    except Exception:
        print(f"[RetrievalPlan] Could not parse plan from {raw!r}, using moderate defaults")
        return {"scope": "moderate", "threshold": 0.5, "paper_hint": None}


async def _identify_missing_context(question: str, answer: str, model: str) -> str | None:
    """
    Ask the LLM what key information is absent from the answer.
    Returns a focused sub-query string, or None if the answer is already sufficient.
    """
    prompt = (
        f"A researcher asked: {question!r}\n\n"
        f"The following answer was generated from retrieved literature:\n{answer}\n\n"
        "Identify ONE specific piece of information that is missing or uncertain in this answer "
        "and would significantly improve it (e.g. a specific yield value, a missing catalyst, "
        "a solvent comparison, a temperature). "
        "Respond with ONLY a short search query (under 12 words) for that missing information, "
        "or respond with exactly 'SUFFICIENT' if the answer is already complete."
    )
    raw = (await _invoke_llm(prompt, model) or "").strip()
    if not raw or raw.upper() == "SUFFICIENT":
        return None
    return raw


def dynamic_k_retrieval(docs_with_scores: List[tuple[Document, float]], total_candidates: int) -> List[Document]:
    """
    Analyzes similarity scores to find the optimal number of documents to return.
    """
    if not docs_with_scores:
        return []

    # The scores from ChromaDB are distances (lower is better). We convert them to similarities.
    # A simple way is to invert them, but let's just look at the relative drop-off.
    scores = [score for _, score in docs_with_scores]

    # Keep the first document, as it's the most relevant
    final_docs = [docs_with_scores[0][0]]

    # Define a threshold for the "drop-off"
    # A 25% increase in distance (decrease in similarity) is a significant drop. (This can be tuned according to the need, consider this moving to config later)
    DROP_OFF_THRESHOLD = 1.25

    for i in range(1, len(docs_with_scores)):
        # If the score of the current doc is significantly worse than the previous one, stop.
        if scores[i] > scores[i - 1] * DROP_OFF_THRESHOLD:
            print(f"[INFO] Significant score drop-off detected after {i} documents. Stopping retrieval.")
            break
        final_docs.append(docs_with_scores[i][0])

    print(f"[INFO] Dynamically selected top {len(final_docs)} out of {total_candidates} retrieved documents.")

    return final_docs


async def handle_general_query(retriever: EmbeddingRetriever, question: str, model_override: str = None, synthesis_model: str = None):
    """Handles a general semantic search query using adaptive retrieval."""
    print("\n▶️ Executing General Query Path...")
    config = load_config()
    retrieval_config = config.get("retrieval", {})
    top_k_general = retrieval_config.get("top_k_general", 100)
    # Step 1: Retrieve a large batch of candidates with their scores
    candidate_docs_with_scores = retriever.retrieve_with_scores(question, top_k=top_k_general)
    initial_count = len(candidate_docs_with_scores)
    print(f"📚 Retrieved {initial_count} initial candidate chunks for ranking.")
    # Step 2: Use the dynamic_k function to select the best ones
    top_docs = dynamic_k_retrieval(candidate_docs_with_scores, total_candidates=initial_count)

    top_texts = [doc.page_content for doc in top_docs]
    top_meta = [doc.metadata for doc in top_docs]
    if not top_texts:
        return "Could not find any relevant documents for your query."

    tasks = [summarize_text(question, chunk, metadata, model_name_override=model_override)
             for chunk, metadata in zip(top_texts, top_meta)]
    summaries = [s for s in await asyncio.gather(*tasks) if s and s.strip()]
    if not summaries:
        return "The retrieved documents did not yield usable summaries for your query."
    final_answer = await generate_final_answer(question, summaries, model_name_override=synthesis_model or model_override)
    return final_answer or "No answer could be generated from the retrieved documents."


async def handle_list_query(retriever: EmbeddingRetriever, entities: Dict, question: str = "",
                           model_override: str = None):
    """Handles a query that asks for a list of articles based on metadata."""
    print("\n▶️ Executing List Query Path...")
    metadata_to_filter = {k: v for k, v in entities.items() if k != 'topic' and v is not None}
    topic = entities.get("topic", "")
    if not metadata_to_filter:
        print("No metadata filters — using LLM-based content scan.")
        return await handle_comprehensive_list_query(retriever, question or topic,
                                                     model_override=model_override)


async def handle_comprehensive_list_query(retriever: EmbeddingRetriever, topic: str,
                                          model_override: str = None):
    """
    Content-based listing:
    1. Retrieve top chunks per paper via semantic search.
    2. Ask the LLM for each paper: "Does this paper use/report X?" — YES/NO.
    3. Return only papers where the LLM says YES, with a one-sentence justification.

    Parallelised: all LLM calls run concurrently.
    """
    print("\n▶️ Executing Comprehensive List Query Path (LLM filtering)...")

    from models import _short_citation, _invoke_llm

    # ── Semantic retrieval — get all chunks ranked by relevance ─────────────
    total = retriever.count()
    pool_size = min(total, 50)
    all_results = retriever.retrieve_with_scores(topic, top_k=pool_size)

    # ── Group chunks by paper, keep top-3 per paper for LLM context ─────────
    paper_chunks: Dict[str, list] = {}
    for doc, score in all_results:
        key = doc.metadata.get("paper") or doc.metadata.get("title") or "Unknown"
        paper_chunks.setdefault(key, []).append((doc, score))

    # ── LLM filter: ask YES/NO for each paper ────────────────────────────────
    async def _llm_filter(paper_key: str, chunks_list: list) -> tuple[bool, str, float, object]:
        """Returns (matches, justification_snippet, best_score, best_doc)."""
        # Use the 3 most relevant chunks as context
        top3 = sorted(chunks_list, key=lambda x: x[1])[:3]
        best_doc = top3[0][0]
        best_score = top3[0][1]
        context = "\n---\n".join(c.page_content[:400] for c, _ in top3)

        prompt = (
            f"You are reviewing excerpts from a chemistry paper to answer a single question.\n\n"
            f"Question: Does this paper use or report **{topic}**?\n\n"
            f"Paper excerpts:\n{context}\n\n"
            f"Answer with YES or NO on the first line, then one sentence explaining why."
        )
        response = await _invoke_llm(prompt, model_override)
        first_line = response.strip().splitlines()[0].strip().upper() if response else "NO"
        matches = first_line.startswith("YES")
        # Extract the justification (second line onwards)
        lines = response.strip().splitlines()
        justification = " ".join(lines[1:]).strip() if len(lines) > 1 else ""
        return matches, justification, best_score, best_doc

    print(f"🤖 Asking LLM to filter {len(paper_chunks)} papers for: '{topic[:60]}'...")
    tasks = {
        paper_key: _llm_filter(paper_key, chunks_list)
        for paper_key, chunks_list in paper_chunks.items()
    }
    results = await asyncio.gather(*tasks.values())
    paper_results = list(zip(tasks.keys(), results))

    matched = [
        (pk, justification, best_score, best_doc)
        for (pk, (matches, justification, best_score, best_doc)) in paper_results
        if matches
    ]
    matched.sort(key=lambda x: x[2])  # sort by semantic relevance

    print(f"📚 LLM confirmed {len(matched)} papers out of {len(paper_chunks)} candidates.")

    if not matched:
        return f"No papers found that match: **{topic}**"

    lines = [f"Found **{len(matched)}** articles:\n"]
    for paper_key, justification, _, doc in matched:
        cite = _short_citation(doc.metadata)
        note = justification[:160] if justification else ""
        lines.append(f"- **{cite}** — {note}…" if note else f"- **{cite}**")

    return "\n".join(lines)

    # --- HYBRID FILTERING APPROACH ---
    # Step 1: Build a 'where' clause for operators ChromaDB *does* support (e.g., exact match on year).
    where_clause = {}
    client_side_filters = {}

    for key, value in metadata_to_filter.items():
        if key == 'authors' and isinstance(value, list):
            client_side_filters[key] = value
        elif key == 'year':
             where_clause[key] = str(value) # Assuming year is stored as string
        else:
            where_clause[key] = value

    # Step 2: Fetch documents using the DB-side filter. If no DB filter, fetch all.
    if where_clause:
        candidate_docs = retriever.query_with_filter(where_clause)
    else:
        candidate_docs = retriever.get_all_documents()

    # Step 3: Apply the remaining client-side filters (like author substring search).
    final_results = []
    if client_side_filters:
        for doc in candidate_docs:
            metadata = doc.get("metadata", {})
            def _author_in_doc_list(filter_name: str, meta: dict) -> bool:
                name_lower = filter_name.lower()
                if meta.get("authors"):
                    return any(name_lower in a.lower() for a in meta["authors"].split(","))
                paper_str = (meta.get("paper") or meta.get("title") or "").lower()
                return name_lower in paper_str
            authors_match = all(
                _author_in_doc_list(f, metadata)
                for f in client_side_filters.get("authors", [])
            )
            if authors_match:
                final_results.append(doc)
    else:
        final_results = candidate_docs

    seen_titles = {}
    for doc in final_results:
        metadata = doc.get("metadata", {})
        title = metadata.get("title")
        if title and title not in seen_titles:
            seen_titles[title] = metadata
    
    response_lines = [f"Found {len(seen_titles)} unique articles matching your criteria:"]
    for title, metadata in seen_titles.items():
        response_lines.append(f"- **Title:** {title}\n  - **Authors:** {metadata.get('authors')}\n  - **Year:** {metadata.get('year')}")
    return "\n".join(response_lines)


async def handle_filtered_query(retriever: EmbeddingRetriever, question: str, entities: Dict, model_override: str = None, synthesis_model: str = None):
    """Handles a query that combines a topic search with metadata filters."""
    print("\n▶️ Executing Filtered Query Path...")
    topic = entities.pop('topic', question)
    metadata_to_filter = {k: v for k, v in entities.items() if v is not None}

    # --- HYBRID APPROACH FOR FILTERED QUERIES ---
    # Step 1: Pre-filter documents based on metadata, using the same robust logic as handle_list_query.
    where_clause = {}
    client_side_filters = {}
    if metadata_to_filter:
        for key, value in metadata_to_filter.items():
            if key == 'authors' and isinstance(value, list):
                client_side_filters[key] = value
            elif key == 'year':
                where_clause[key] = str(value)
            else:
                where_clause[key] = value

    if where_clause:
        candidate_docs = retriever.query_with_filter(where_clause)
    else:
        candidate_docs = retriever.get_all_documents()

    pre_filtered_docs = []
    if client_side_filters:
        for doc in candidate_docs:
            metadata = doc.get("metadata", {})
            # Check authors field first; fall back to paper/title (folder name) when authors is absent.
            # FAISS chunks store author info only in the 'paper'/'title' folder-name field.
            def _author_in_doc(filter_name: str, meta: dict) -> bool:
                name_lower = filter_name.lower()
                if meta.get("authors"):
                    return any(name_lower in a.lower() for a in meta["authors"].split(","))
                # Folder-name fallback: "Boselli et al. - 2023 - ..."
                paper_str = (meta.get("paper") or meta.get("title") or "").lower()
                return name_lower in paper_str

            authors_match = all(
                _author_in_doc(f, metadata)
                for f in client_side_filters.get("authors", [])
            )
            if authors_match:
                pre_filtered_docs.append(doc)
    else:
        pre_filtered_docs = candidate_docs

    if not pre_filtered_docs:
        # Filter found nothing — fall back to unfiltered semantic search so the user gets an answer
        print("⚠️ No documents matched metadata filters — falling back to general semantic search.")
        return await handle_general_query(retriever, question, model_override=model_override, synthesis_model=synthesis_model)

    # Step 2: Perform a semantic search using the existing index + native filtering
    # This avoids the high-latency 'TempChroma.from_texts' (re-embedding) step.
    config = load_config()
    retrieval_config = config.get("retrieval", {})
    top_k_filtered = retrieval_config.get("top_k_filtered", 10)

    # Use native Chroma filter if possible
    # We use 'content_hash' as a proxy to filter for only the docs that passed our hybrid check
    doc_hashes = [doc['metadata'].get('content_hash') for doc in pre_filtered_docs if doc['metadata'].get('content_hash')]
    
    if doc_hashes:
        # If we have a limited set of documents, use an $in filter on content_hash
        # Note: Chroma collections have limits on $in list size, but for typical research folders this is fine.
        chroma_filter = {"content_hash": {"$in": list(set(doc_hashes))}}
        print(f"🔍 Performing semantic search on {len(doc_hashes)} filtered hashes...")
    else:
        # Fallback to the where_clause if hashes aren't available
        chroma_filter = where_clause or None
        print(f"🔍 Performing semantic search with basic filter: {chroma_filter}")

    candidate_docs_with_scores = retriever.index.similarity_search_with_score(topic, k=top_k_filtered, filter=chroma_filter)
    initial_count = len(candidate_docs_with_scores)
    print(f"📚 Retrieved {initial_count} candidate chunks.")
    top_docs = dynamic_k_retrieval(candidate_docs_with_scores, total_candidates=initial_count)

    top_texts = [doc.page_content for doc in top_docs]
    top_meta = [doc.metadata for doc in top_docs]
    if not top_texts:
        return "Could not find any relevant documents for your query."

    tasks = [summarize_text(question, chunk, metadata, model_name_override=model_override)
             for chunk, metadata in zip(top_texts, top_meta)]
    summaries = [s for s in await asyncio.gather(*tasks) if s and s.strip()]
    if not summaries:
        return "The retrieved documents did not yield usable summaries for your query."
    final_answer = await generate_final_answer(question, summaries, model_name_override=synthesis_model or model_override)
    return final_answer or "No answer could be generated from the retrieved documents."


async def _run_fusion_retrieval(
    retriever: EmbeddingRetriever,
    question: str,
    query_type: str = "general_query",
    top_k: int = 6,
    retrieval_plan: dict | None = None,
) -> tuple[list[Document], str]:
    """
    Core fusion retrieval: FAISS + GraphRAG PPR → adaptive RRF.

    Retrieval strategy (when retrieval_plan provided):
      single_paper   → retrieve_by_paper(paper_hint) — all chunks of that paper, no similarity search
      single_subject → retrieve_by_threshold(sim >= 0.4) — FAISS range_search
      moderate       → retrieve_by_threshold(sim >= 0.5) — FAISS range_search
      broad          → retrieve_by_threshold(sim >= 0.7) — FAISS range_search
    Falls back to top_k if no plan or range_search returns nothing.

    Returns (fused_docs, graph_context_text).
    Docs from FAISS carry metadata["_source"] = "faiss".
    graph_context_text lines are prefixed with [Graph] for synthesis transparency.
    """
    # ── Channel 1: FAISS — scope-aware retrieval ──────────────────────────────
    if retrieval_plan:
        scope = retrieval_plan.get("scope", "moderate")
        threshold = retrieval_plan.get("threshold", 0.5)
        paper_hint = retrieval_plan.get("paper_hint") or ""

        if scope == "single_paper" and paper_hint:
            gemini_results = retriever.retrieve_by_paper(paper_hint)
            if not gemini_results:
                # paper not found by metadata — fall back to similarity search
                gemini_results = retriever.retrieve_by_threshold(question, min_similarity=threshold)
        else:
            gemini_results = retriever.retrieve_by_threshold(question, min_similarity=threshold)
            if not gemini_results:
                # threshold too strict — fall back to top_k
                gemini_results = retriever.retrieve_with_scores(question, top_k=top_k)
    else:
        gemini_results = retriever.retrieve_with_scores(question, top_k=top_k)

    # retrieve_by_threshold/retrieve_by_paper are uncapped (can return dozens-hundreds of chunks) — every doc here fires a separate downstream LLM summarization call,
    # so enforce top_k as a hard cost cap regardless of retrieval strategy.
    if len(gemini_results) > top_k:
        gemini_results = sorted(gemini_results, key=lambda x: x[1])[:top_k]

    gemini_ranked = [(doc.metadata.get("id", doc.page_content[:30]), score)
                     for doc, score in gemini_results]
    gemini_doc_map: dict[str, Document] = {}
    for doc, _ in gemini_results:
        doc_id = doc.metadata.get("id", doc.page_content[:30])
        doc.metadata["_source"] = "faiss"
        gemini_doc_map[doc_id] = doc

    # ── Channel 2: GraphRAG (PPR) ─────────────────────────────────────────────
    graph_context_text = ""
    graph_ranked: list[tuple[str, float]] = []
    try:
        kg_dir = Path(__file__).parent / "kg"
        graph_path = kg_dir / "graph" / "reaction_network.json"
        index_path = kg_dir / "embeddings" / "reaction_index.faiss"

        if graph_path.exists():
            from .graph.builder import load_graph
            from .hipporag.retriever import HippoRetriever

            G = load_graph(str(graph_path))
            reaction_index = None
            if index_path.exists():
                from .reaction_embeddings.embedder import ReactionEmbedder
                from .reaction_embeddings.index import ReactionIndex
                meta_path = index_path.with_suffix(".meta.json")
                emb = ReactionEmbedder(use_rxnfp=False)
                reaction_index = ReactionIndex(embedder=emb)
                reaction_index.load(index_path, meta_path)

            ctx = HippoRetriever(G, reaction_index, top_n_context=15).retrieve(question)
            # Tag each graph line so synthesis can attribute the source
            tagged_lines = []
            for line in ctx.context_text.splitlines():
                tagged_lines.append(f"[Graph] {line}" if line.strip() else line)
            graph_context_text = "\n".join(tagged_lines)
            graph_ranked = list(ctx.ranked_nodes)
        else:
            print("[HybridRAG] Graph not found — running FAISS-only retrieval.")
    except Exception as e:
        print(f"[HybridRAG] GraphRAG failed, using FAISS only: {e}")

    # ── Adaptive RRF Fusion ───────────────────────────────────────────────────
    w_faiss, w_graph = _RRF_WEIGHTS.get(query_type, _RRF_DEFAULT)
    print(f"[HybridRAG] RRF weights — FAISS: {w_faiss}, Graph: {w_graph} (query_type={query_type})")

    if graph_ranked:
        fused = reciprocal_rank_fusion(
            [gemini_ranked, graph_ranked],
            weights=[w_faiss, w_graph],
        )
        fused_docs: list[Document] = []
        seen: set[str] = set()
        for doc_id, _ in fused:
            if doc_id in gemini_doc_map and doc_id not in seen:
                fused_docs.append(gemini_doc_map[doc_id])
                seen.add(doc_id)
        for doc_id, doc in gemini_doc_map.items():
            if doc_id not in seen:
                fused_docs.append(doc)
    else:
        fused_docs = [doc for doc, _ in gemini_results]

    return fused_docs, graph_context_text


async def handle_hybrid_graphrag_query(
    retriever: EmbeddingRetriever,
    question: str,
    entities: Dict | None = None,
    query_type: str = "general_query",
    model_override: str = None,
    synthesis_model: str = None,
    conversation_history: list | None = None,
) -> tuple[str, list[Document]]:
    """
    Hybrid RAG: Gemini FAISS + GraphRAG PPR fused via adaptive RRF.

    Improvements over basic fusion:
      1. Adaptive weights — FAISS/Graph balance shifts per query type.
      2. Source tagging — [Graph] prefix on graph lines lets the LLM attribute sources.
      3. Multi-hop expansion — PPR seeds follow SIMILAR_TO/SUBSTRUCTURE_OF edges.
      4. Concept node boosting — Concept node matches seed linked Reaction nodes.
      5. Iterative refinement — after first answer, identifies gaps and fires a
         targeted second retrieval round to fill them.

    Returns (answer, retrieved_docs) — retrieved_docs is every doc actually used for
    synthesis (round 1 + any refinement round), so callers can pull image_path from
    the exact chunks the answer was built from, not a separate disconnected search.
    """
    print("\n▶️ Executing Hybrid GraphRAG Query Path…")
    config = load_config()
    synthesis_model = synthesis_model or model_override or config.get("model", {}).get("hybrid_synthesis_model", "google/gemini-2.5-flash")

    # ── Retrieval planning agent ──────────────────────────────────────────────
    plan = await _plan_retrieval(question, query_type, synthesis_model)

    # ── Round 1: retrieval + synthesis ───────────────────────────────────────
    fused_docs, graph_context_text = await _run_fusion_retrieval(
        retriever, question, query_type=query_type, retrieval_plan=plan
    )
    top_docs = fused_docs

    summaries: list[str] = []
    if top_docs:
        tasks = [
            summarize_text(question, doc.page_content, doc.metadata, model_name_override=synthesis_model)
            for doc in top_docs
        ]
        summaries = [s for s in await asyncio.gather(*tasks) if s and s.strip()]
    if graph_context_text:
        summaries.append(f"[Reaction Graph Context]\n{graph_context_text}")

    if not summaries:
        return "Could not find relevant information for your query in either the article index or reaction graph.", []

    first_answer = await generate_final_answer(
        question, summaries, model_name_override=synthesis_model,
        conversation_history=conversation_history,
    )

    # ── Round 2: iterative refinement ────────────────────────────────────────
    # Ask the LLM what is missing, fire a targeted sub-query, merge new context.
    if first_answer:
        gap_query = await _identify_missing_context(question, first_answer, synthesis_model)
        if gap_query:
            print(f"[HybridRAG] Refinement round — sub-query: {gap_query!r}")
            try:
                extra_docs, extra_graph = await _run_fusion_retrieval(
                    retriever, gap_query, query_type=query_type, retrieval_plan=plan
                )
                seen_ids = {d.metadata.get("id", d.page_content[:30]) for d in top_docs}
                extra_top = [d for d in extra_docs if d.metadata.get("id", d.page_content[:30]) not in seen_ids]
                extra_tasks = [
                    summarize_text(question, doc.page_content, doc.metadata, model_name_override=synthesis_model)
                    for doc in extra_top
                ]
                extra_summaries = [s for s in await asyncio.gather(*extra_tasks) if s and s.strip()]
                if extra_graph:
                    extra_summaries.append(f"[Reaction Graph Context — refinement]\n{extra_graph}")
                if extra_summaries:
                    all_summaries = summaries + extra_summaries
                    final_answer = await generate_final_answer(
                        question, all_summaries, model_name_override=synthesis_model,
                        conversation_history=conversation_history,
                    )
                    return final_answer or first_answer, top_docs + extra_top
            except Exception as e:
                print(f"[HybridRAG] Refinement failed, keeping first answer: {e}")

    return first_answer or "No answer could be generated from the retrieved documents.", top_docs


async def handle_recommendation_query(
    retriever: EmbeddingRetriever,
    question: str,
    model_override: str = None,
    synthesis_model: str = None,
    conversation_history: list | None = None,
) -> tuple[str, list[Document]]:
    """
    Recommendation RAG: adaptive fusion retrieval with recommendation-structured synthesis.
    Uses 50/50 FAISS/Graph weights, source tagging, multi-hop expansion, and iterative
    refinement — same machinery as handle_hybrid_graphrag_query.

    Returns (answer, retrieved_docs) — see handle_hybrid_graphrag_query docstring.
    """
    print("\n▶️ Executing Recommendation Query Path…")
    config = load_config()
    synthesis_model = synthesis_model or model_override or config.get("model", {}).get("hybrid_synthesis_model", "google/gemini-2.5-flash")

    plan = await _plan_retrieval(question, "recommendation_query", synthesis_model)

    fused_docs, graph_context_text = await _run_fusion_retrieval(
        retriever, question, query_type="recommendation_query", retrieval_plan=plan
    )
    top_docs = fused_docs

    summaries: list[str] = []
    if top_docs:
        tasks = [
            summarize_text(question, doc.page_content, doc.metadata, model_name_override=synthesis_model)
            for doc in top_docs
        ]
        summaries = [s for s in await asyncio.gather(*tasks) if s and s.strip()]
    if graph_context_text:
        summaries.append(f"[Reaction Graph Context]\n{graph_context_text}")

    if not summaries:
        return "Could not find relevant information to make a recommendation. Try rephrasing or asking a more specific question.", []

    first_answer = await generate_recommendation_answer(
        question, summaries, model_name_override=synthesis_model,
        conversation_history=conversation_history,
    )

    # ── Iterative refinement ──────────────────────────────────────────────────
    if first_answer:
        gap_query = await _identify_missing_context(question, first_answer, synthesis_model)
        if gap_query:
            print(f"[RecommendationRAG] Refinement round — sub-query: {gap_query!r}")
            try:
                extra_docs, extra_graph = await _run_fusion_retrieval(
                    retriever, gap_query, query_type="recommendation_query", retrieval_plan=plan
                )
                seen_ids = {d.metadata.get("id", d.page_content[:30]) for d in top_docs}
                extra_top = [d for d in extra_docs
                             if d.metadata.get("id", d.page_content[:30]) not in seen_ids]
                extra_tasks = [
                    summarize_text(question, doc.page_content, doc.metadata, model_name_override=synthesis_model)
                    for doc in extra_top
                ]
                extra_summaries = [s for s in await asyncio.gather(*extra_tasks) if s and s.strip()]
                if extra_graph:
                    extra_summaries.append(f"[Reaction Graph Context — refinement]\n{extra_graph}")
                if extra_summaries:
                    final_answer = await generate_recommendation_answer(
                        question, summaries + extra_summaries, model_name_override=synthesis_model,
                        conversation_history=conversation_history,
                    )
                    return final_answer or first_answer, top_docs + extra_top
            except Exception as e:
                print(f"[RecommendationRAG] Refinement failed, keeping first answer: {e}")

    return first_answer or "Could not generate a recommendation from the retrieved documents.", top_docs
