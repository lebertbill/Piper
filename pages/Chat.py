import asyncio
import os
import sys
import json
from pathlib import Path
import streamlit as st
import pandas as pd
from PIL import Image

sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

st.set_page_config(
    page_title="Chat",
    page_icon=Image.open("ico/ico.png"),
    layout="wide",
)

def _get_or_create_eventloop():
    try:
        return asyncio.get_running_loop()
    except RuntimeError:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        return loop


def _is_structural_query(q: str) -> bool:
    """True when q looks like a SMILES, SMARTS, or reaction SMILES rather than prose."""
    import re
    q = q.strip()
    if ">>" in q:
        return True
    # Single token composed only of chemistry characters (no spaces)
    return bool(re.match(r'^[A-Za-z0-9\[\]\(\)=#@+\-\\/.%:*]+$', q) and len(q) < 200)


async def _run_graph_query(query: str, synthesis_model_override: str = "google/gemini-2.5-flash") -> tuple[str, list[str]]:
    """Structural query path: SMARTS/SMILES → hybrid search, reaction>> → PPR + similarity."""
    from models import generate_final_answer

    G = _load_saved_graph()
    if G is None:
        return "No knowledge graph found. Click **▶ Build / Rebuild Graph** in the sidebar first.", []
    idx = _load_saved_index()

    lines: list[str] = []

    if ">>" in query:
        lines.append(f"**Reaction SMILES query:** `{query}`\n")
        if idx is not None:
            try:
                results = idx.search(query, k=10)
                if results:
                    lines.append(f"**{len(results)} similar reactions found:**\n")
                    lines.append("| Score | Paper | Table | Entry | Yield |")
                    lines.append("|---|---|---|---|---|")
                    for r in results:
                        lines.append(
                            f"| {r['score']:.3f} | {r['paper'][:45]} | "
                            f"{r['table_name']} | {r['entry_id']} | "
                            f"{r.get('yield', '—')}% |"
                        )
                    lines.append("")
            except Exception as e:
                lines.append(f"*Reaction similarity search unavailable: {e}*\n")
        try:
            from rag.hipporag.retriever import HippoRetriever
            ctx = HippoRetriever(G, reaction_index=idx).retrieve(query)
            if ctx.context_text:
                lines.append("**Graph context (PPR-ranked nodes):**\n")
                lines.append(ctx.context_text)
        except Exception as e:
            lines.append(f"*Graph PPR unavailable: {e}*\n")

    else:
        lines.append(f"**Substructure search:** `{query}`\n")
        try:
            _model = json.loads((PROJECT_ROOT / "context.json").read_text())["model"]["rag_model_name"]
            from rag.hybrid_search.query_engine import HybridQueryEngine
            engine = HybridQueryEngine(G=G, reaction_index=idx, model=_model)
            result = engine.search(smarts_query=query, reaction_smiles=None, synthesise_answer=False)
            results = result.get("results", [])
            if not results:
                return f"No reactions found containing substructure `{query}`.", []
            lines.append(f"**{len(results)} reactions** contain this substructure:\n")
            lines.append("| Score | Paper | Table | Entry | Yield | Catalyst |")
            lines.append("|---|---|---|---|---|---|")
            for r in results[:20]:
                cats = ", ".join(r.catalyst_labels[:2]) if r.catalyst_labels else "—"
                lines.append(
                    f"| {r.final_score:.3f} | {r.paper[:40]} | {r.table} | "
                    f"{r.entry_id} | {r.yield_val if r.yield_val is not None else '—'}% | {cats} |"
                )
        except Exception as e:
            lines.append(f"*Substructure search failed: {e} — falling back to graph PPR.*\n")
            try:
                from rag.hipporag.retriever import HippoRetriever
                ctx = HippoRetriever(G, reaction_index=idx).retrieve(query)
                lines.append(ctx.context_text)
            except Exception:
                pass

    answer = await generate_final_answer(
        query, ["\n".join(lines)], model_name_override=synthesis_model_override
    )
    return answer, []


def _collect_images(docs, limit: int = 4, seen: set | None = None) -> list[dict]:
    """Extract valid image paths from a list of retrieved Documents.
    Returns list of {path, caption} dicts — caption uses figure_id/figure_caption when available.
    `seen` may be passed in (and is mutated) to dedup across multiple calls/sources."""
    items = []
    seen = seen if seen is not None else set()
    for doc in docs:
        p = doc.metadata.get("image_path")
        if p and p not in seen and Path(p).exists():
            fig_id = doc.metadata.get("figure_id", "")
            fig_cap = doc.metadata.get("figure_caption", "")
            # Prefer the short figure label ("Fig. 1", "Scheme 2") over figure_caption —
            # for some chunks figure_caption ends up holding the surrounding body
            # paragraph rather than a real caption, which reads badly under an image.
            if fig_id:
                caption = fig_id
            elif fig_cap:
                caption = fig_cap[:80] + ("…" if len(fig_cap) > 80 else "")
            else:
                # Fall back: hash-named images (image_000001_abc...) → show paper folder name
                # Path layout: .../extracted_data/PAPER/RUN/processed/PAPER_artifacts/image_*.png
                stem = Path(p).stem
                if stem.startswith("image_0"):
                    caption = Path(p).parents[3].name[:60]  # PAPER folder, 4 levels up
                else:
                    caption = stem.replace("_", " ")
            items.append({"path": p, "caption": caption})
            seen.add(p)
        if len(items) >= limit:
            break
    return items


async def _run_rag_query(
    db_path: str,
    question: str,
    conversation_history: list | None = None,
    model_classify: str = "openai/gpt-4o",
    model_entity: str = "openai/gpt-4o",
    model_summarize: str = "openai/gpt-4o",
    model_synthesis: str = "google/gemini-2.5-flash",
) -> tuple[str, list[str]]:
    """
    Route queries through the fusion pipeline (Gemini FAISS + GraphRAG PPR, RRF-fused).
    Structural queries (SMARTS/SMILES/reaction>>) always use the graph path.
    """
    if _is_structural_query(question):
        return await _run_graph_query(question, synthesis_model_override=model_synthesis)

    from rag.embeddings import EmbeddingRetriever
    from rag.query_router import extract_entities, _classify_query_with_fallback
    from rag.retriever_logic import handle_hybrid_graphrag_query, handle_list_query, handle_recommendation_query

    retriever = EmbeddingRetriever(db_path=db_path, model_name="gemini")

    # Source 1: dedicated search over the image-bearing (multimodal) chunk subset only.
    # A plain combined-pool top-k search almost never surfaces figures — they're a small
    # minority of chunks and rarely share as much literal vocabulary with a query as prose
    # text does, so they lose ranking even when they're the actually-relevant content.
    seen_image_paths: set = set()
    mm_chunks = retriever.retrieve_multimodal_with_scores(question, top_k=4)
    image_paths = _collect_images([d for d, _ in mm_chunks], limit=4, seen=seen_image_paths)

    retrieved_docs: list = []
    query_type = await _classify_query_with_fallback(question, model_override=model_classify)
    if query_type in ("general_query", "filtered_query"):
        entities = await extract_entities(question, model_override=model_entity) if query_type == "filtered_query" else None
        answer, retrieved_docs = await handle_hybrid_graphrag_query(retriever, question, entities,
                                                     query_type=query_type,
                                                     model_override=model_summarize,
                                                     synthesis_model=model_synthesis,
                                                     conversation_history=conversation_history)
    elif query_type == "list_query":
        entities = await extract_entities(question, model_override=model_entity)
        answer = await handle_list_query(retriever, entities, question=question,
                                         model_override=model_summarize)
    elif query_type == "recommendation_query":
        answer, retrieved_docs = await handle_recommendation_query(retriever, question,
                                                    model_override=model_summarize,
                                                    synthesis_model=model_synthesis,
                                                    conversation_history=conversation_history)
    else:
        answer, retrieved_docs = await handle_hybrid_graphrag_query(retriever, question,
                                                     query_type=query_type,
                                                     model_override=model_summarize,
                                                     synthesis_model=model_synthesis,
                                                     conversation_history=conversation_history)

    # Source 2: the docs actually retrieved/summarized for the answer (fusion + graph +
    # refinement round) — this is where multimodal figure chunks are most likely to show up,
    # since the top-4 plain search above rarely surfaces them (images are a small minority
    # of chunks per paper).
    if retrieved_docs:
        image_paths += _collect_images(retrieved_docs, limit=6, seen=seen_image_paths)

    return answer, image_paths

PROJECT_ROOT = Path(__file__).parent.parent
KG_DIR = PROJECT_ROOT / "kg"
DEFAULT_DATA_ROOT = PROJECT_ROOT / "extracted_data"
SESSIONS_DIR = PROJECT_ROOT / "chat_sessions"

from rag.chat_history import new_session_id, list_sessions, load_session, save_session, delete_session

# ── Cached loaders ────────────────────────────────────────────────────────────

@st.cache_data(show_spinner=False)
def _get_available_runs(data_root: str) -> dict:
    from rag.graph.loader import get_available_runs
    return get_available_runs(data_root)


@st.cache_resource(show_spinner=False)
def _build_graph(data_root: str, tanimoto: float, preferred_run: str = "", per_paper_runs_json: str = "{}"):
    from rag.graph.loader import load_all
    from rag.graph.builder import build_graph
    import json
    per_paper = json.loads(per_paper_runs_json) if per_paper_runs_json else {}
    records = load_all(data_root, preferred_run=preferred_run or None, per_paper_runs=per_paper)
    return build_graph(records, tanimoto_threshold=tanimoto), records


@st.cache_resource(show_spinner=False)
def _build_embedding_index(data_root: str, preferred_run: str = "", per_paper_runs_json: str = "{}"):
    from rag.graph.loader import load_all
    from rag.reaction_embeddings.reaction_smiles import build_reaction_smiles_corpus
    from rag.reaction_embeddings.embedder import ReactionEmbedder
    from rag.reaction_embeddings.index import ReactionIndex
    import json as _j
    per_paper = _j.loads(per_paper_runs_json) if per_paper_runs_json else {}
    records = load_all(data_root, preferred_run=preferred_run or None, per_paper_runs=per_paper)
    corpus = build_reaction_smiles_corpus(records)
    embedder = ReactionEmbedder(use_rxnfp=True)
    idx = ReactionIndex(embedder=embedder)
    idx.build(corpus)
    return idx


@st.cache_resource(show_spinner=False)
def _load_saved_index():
    index_path = KG_DIR / "embeddings" / "reaction_index.faiss"
    meta_path = KG_DIR / "embeddings" / "reaction_index.meta.json"
    if not index_path.exists():
        return None
    from rag.reaction_embeddings.embedder import ReactionEmbedder
    from rag.reaction_embeddings.index import ReactionIndex
    embedder = ReactionEmbedder(use_rxnfp=True)
    idx = ReactionIndex(embedder=embedder)
    idx.load(index_path, meta_path)
    return idx


@st.cache_resource(show_spinner=False)
def _load_saved_graph():
    path = KG_DIR / "graph" / "reaction_network.json"
    if not path.exists():
        return None
    from rag.graph.builder import load_graph
    return load_graph(str(path))


# ── HippoRAG + Hybrid Search cached engines ──────────────────────────────────

@st.cache_resource(show_spinner=False)
def _get_hippo_engine(data_root: str, tanimoto: float, preferred_run: str = "", per_paper_runs_json: str = "{}"):
    G, _ = _build_graph(data_root, tanimoto, preferred_run, per_paper_runs_json)
    idx = _load_saved_index()
    model = json.loads((PROJECT_ROOT / "context.json").read_text())["model"]["rag_model_name"]
    from rag.hipporag.query_engine import HippoQueryEngine
    return HippoQueryEngine(G=G, reaction_index=idx, model=model)


@st.cache_resource(show_spinner=False)
def _get_hybrid_engine(data_root: str, tanimoto: float, preferred_run: str = "", per_paper_runs_json: str = "{}"):
    G, _ = _build_graph(data_root, tanimoto, preferred_run, per_paper_runs_json)
    idx = _load_saved_index()
    model = json.loads((PROJECT_ROOT / "context.json").read_text())["model"]["rag_model_name"]
    from rag.hybrid_search.query_engine import HybridQueryEngine
    return HybridQueryEngine(G=G, reaction_index=idx, model=model)


# ── Helpers ───────────────────────────────────────────────────────────────────

def _embed_html(html_path: Path, height: int = 700):
    if html_path.exists():
        st.components.v1.html(html_path.read_text(encoding="utf-8"), height=height, scrolling=True)
    else:
        st.info("File not found. Run the pipeline first.")


def _graph_stats(G) -> dict:
    counts: dict[str, int] = {}
    for _, d in G.nodes(data=True):
        nt = d.get("node_type", "unknown")
        counts[nt] = counts.get(nt, 0) + 1
    edge_types: dict[str, int] = {}
    for _, _, d in G.edges(data=True):
        et = d.get("edge_type", "unknown")
        edge_types[et] = edge_types.get(et, 0) + 1
    return {
        "compounds": counts.get("Compound", 0),
        "reactions": counts.get("Reaction", 0),
        "temperatures": counts.get("Temperature", 0),
        "times": counts.get("Time", 0),
        "yield_ranges": counts.get("Yield", 0),
        "articles": counts.get("Article", 0),
        "tables": counts.get("Table", 0),
        "concepts": counts.get("Concept", 0),
        "quantities": counts.get("Quantity", 0),
        "total_edges": G.number_of_edges(),
        "edge_types": edge_types,
        "papers": len({d.get("paper", "") for _, d in G.nodes(data=True)
                       if d.get("node_type") == "Reaction"}),
    }


# ── Sidebar ───────────────────────────────────────────────────────────────────

with st.sidebar:
    st.header("Knowledge Graph")
    st.markdown("---")

    data_root = st.text_input(
        "Data root",
        value=str(DEFAULT_DATA_ROOT),
        help="Path to the folder containing paper subfolders",
    )
    tanimoto = st.slider("Tanimoto threshold (SIMILAR_TO edges)", 0.5, 1.0, 0.7, 0.05)
    rxn_sim = st.slider("Reaction similarity threshold (SIMILAR_REACTION edges)", 0.6, 1.0, 0.85, 0.05)
    top_n_graph = st.number_input("Max nodes in graph viz", min_value=50, max_value=500, value=200, step=50)

    st.markdown("---")
    st.caption("Run selection")

    # Collect all distinct run names across all papers
    _avail_runs = _get_available_runs(data_root)
    _all_run_names = sorted({r for runs in _avail_runs.values() for r in runs})
    preferred_run = st.selectbox(
        "Preferred run (global)",
        options=["(first available)"] + _all_run_names,
        help="Applied to every paper. Override per paper in the Overview tab.",
    )
    preferred_run = "" if preferred_run == "(first available)" else preferred_run

    # Store per-paper overrides in session state
    if "per_paper_runs" not in st.session_state:
        st.session_state["per_paper_runs"] = {}

    import json as _json
    per_paper_runs_json = _json.dumps(st.session_state["per_paper_runs"])

    st.markdown("---")
    st.caption("Steps")
    run_pipeline = st.button("▶ Build / Rebuild Graph", type="primary", use_container_width=True)
    run_enrich = st.button("▶ Enrich KG", use_container_width=True)
    run_embeddings = False

    st.markdown("---")
    st.caption("RAG Index")
    from context import load_config
    _cfg = load_config()
    _default_data_root_rag = str(PROJECT_ROOT / "extracted_data")
    _rag_data_root = st.text_input(
        "Extracted Data Folder",
        value=_cfg.get("extracted_data_root", _default_data_root_rag),
        key="rag_data_root_apps",
        help="Path to extracted_data/ used for RAG indexing.",
    )
    if os.path.isdir(_rag_data_root):
        from rag.graph.loader import get_available_runs as _avail_fn, _pick_run
        _rag_avail = _avail_fn(_rag_data_root)
        _rag_run_names = sorted({r for runs in _rag_avail.values() for r in runs})
        _rag_pref = st.selectbox(
            "Preferred run (RAG)",
            options=["(first available)"] + _rag_run_names,
            key="rag_pref_run_apps",
        )
        _rag_pref = "" if _rag_pref == "(first available)" else _rag_pref
        if "rag_per_paper_runs_apps" not in st.session_state:
            st.session_state["rag_per_paper_runs_apps"] = {}
        _rag_papers_multi = {p: r for p, r in _rag_avail.items() if len(r) > 1}
        if _rag_papers_multi:
            with st.expander(f"Per-paper overrides ({len(_rag_papers_multi)} papers)"):
                for _p, _r in sorted(_rag_papers_multi.items()):
                    _cur = st.session_state["rag_per_paper_runs_apps"].get(_p, "")
                    _eff = _cur or _pick_run(_r, _rag_pref)
                    _ch = st.selectbox(_p[:55], options=_r, index=_r.index(_eff) if _eff in _r else 0,
                                       key=f"rag_run_apps_{_p}", label_visibility="visible")
                    st.session_state["rag_per_paper_runs_apps"][_p] = _ch
    else:
        _rag_avail = {}
        _rag_pref = ""
    _faiss_dir = str(PROJECT_ROOT / _cfg.get("embedding", {}).get("faiss_index_path", "papers_faiss"))
    _force_reingest = st.checkbox("Force Re-ingest", value=False, key="rag_force_apps")
    build_index_btn = st.button("▶ Build / Rebuild RAG Index", use_container_width=True, key="rag_build_apps")

    st.markdown("---")
    st.caption("RAG Models")
    _m = _cfg.get("model", {})
    _emb = _cfg.get("embedding", {})

    def _model_box(label, key, default, help_text):
        return st.text_input(label, value=default, key=key, help=help_text)

    _model_embedding = _model_box("Embedding Model",         "rag_model_embedding",
                                   _emb.get("embedding_model", "google/gemini-embedding-2-preview"),
                                   "Model used to embed paper chunks (and images) for vector search. "
                                   "Changing this does not re-embed existing chunks — use Force Re-ingest.")
    _model_classify  = _model_box("Query Classification",   "rag_model_classify",
                                   _m.get("query_classification_model", _m.get("model_name", "openai/gpt-4o")),
                                   "Classifies query into general / filtered / list / recommendation.")
    _model_entity    = _model_box("Entity Extraction",       "rag_model_entity",
                                   _m.get("entity_extraction_model", _m.get("model_name", "openai/gpt-4o")),
                                   "Extracts author, year, catalyst etc. from the query.")
    _model_summarize = _model_box("Chunk Summarization",     "rag_model_summarize",
                                   _m.get("chunk_summarization_model", "openai/gpt-4o"),
                                   "Summarizes each retrieved chunk in parallel (highest cost step).")
    _model_synthesis = _model_box("Final Answer Synthesis",  "rag_model_synthesis",
                                   _m.get("hybrid_synthesis_model", "google/gemini-2.5-flash"),
                                   "Synthesizes all summaries into the final answer.")
    _model_rag_name  = _model_box("RAG Model (Graph / Hybrid Search / KG Enrich)", "rag_model_name_field",
                                   _m.get("rag_model_name", "openai/gpt-4o"),
                                   "Used by structural (SMARTS/SMILES) search, HippoRAG, hybrid search, "
                                   "and the Enrich KG step — separate from chunk summarization above.")

    if st.button("💾 Save RAG Model Settings", use_container_width=True, key="rag_models_save_btn"):
        from context import CONTEXT_FILE
        _cfg.setdefault("model", {})
        _cfg.setdefault("embedding", {})
        _cfg["embedding"]["embedding_model"] = _model_embedding
        _cfg["model"]["query_classification_model"] = _model_classify
        _cfg["model"]["entity_extraction_model"] = _model_entity
        _cfg["model"]["chunk_summarization_model"] = _model_summarize
        _cfg["model"]["hybrid_synthesis_model"] = _model_synthesis
        _cfg["model"]["rag_model_name"] = _model_rag_name
        with open(CONTEXT_FILE, "w") as _f:
            json.dump(_cfg, _f, indent=4)
        st.success("RAG model settings saved to context.json")

    st.markdown("---")
    st.caption("Chat History")
    if st.button("＋ New Conversation", use_container_width=True, key="new_chat_btn"):
        st.session_state["rag_messages"] = []
        st.session_state["rag_session_id"] = new_session_id()
        st.session_state["rag_session_title"] = ""
        st.rerun()
    _sessions = list_sessions(SESSIONS_DIR)
    if not _sessions:
        st.caption("No saved conversations yet.")
    for _s in _sessions[:20]:
        _date = _s["updated_at"][:10] if _s["updated_at"] else ""
        _c1, _c2 = st.columns([5, 1])
        if _c1.button(f"{_s['title'][:38]}\n{_date}", key=f"load_{_s['id']}", use_container_width=True):
            _loaded = load_session(_s["id"], SESSIONS_DIR)
            if _loaded:
                st.session_state["rag_messages"] = _loaded["messages"]
                st.session_state["rag_session_id"] = _s["id"]
                st.session_state["rag_session_title"] = _s["title"]
                st.rerun()
        if _c2.button("🗑", key=f"del_{_s['id']}"):
            delete_session(_s["id"], SESSIONS_DIR)
            if st.session_state.get("rag_session_id") == _s["id"]:
                st.session_state["rag_messages"] = []
                st.session_state["rag_session_id"] = new_session_id()
                st.session_state["rag_session_title"] = ""
            st.rerun()

# ── Title ─────────────────────────────────────────────────────────────────────

import base64 as _b64
with open("ico/ico.png", "rb") as _f:
    _ico = _b64.b64encode(_f.read()).decode()
st.markdown(
    f'<div style="display:flex;align-items:center;gap:14px;margin-bottom:8px">'
    f'<img src="data:image/png;base64,{_ico}" width="48"/>'
    f'<span style="font-size:2rem;font-weight:700;color:#1f2937">Piper</span>'
    f'</div>',
    unsafe_allow_html=True
)

tab_overview, tab_graph_viz, tab_rag = st.tabs([
    "Overview",
    "Graph Visualization",
    "Chat",
])

# ── Pipeline trigger ──────────────────────────────────────────────────────────

if run_pipeline:
    with st.spinner("Building reaction network graph…"):
        try:
            _build_graph.clear()
            G, records = _build_graph(data_root, tanimoto, preferred_run, per_paper_runs_json)
            stats = _graph_stats(G)
            st.session_state["_graph_built"] = True

            # Save graph
            from rag.graph.builder import save_graph
            graph_file = KG_DIR / "graph" / "reaction_network.json"
            graph_file.parent.mkdir(parents=True, exist_ok=True)
            save_graph(G, str(graph_file))
            # Invalidate disk-load caches so tabs see the new graph immediately
            _load_saved_graph.clear()
            _get_hippo_engine.clear()
            _get_hybrid_engine.clear()

            # Analysis CSVs
            from rag.graph.analysis import compound_centrality, yield_by_catalyst, paper_overlap
            compound_centrality(G).to_csv(KG_DIR / "graph" / "compound_centrality.csv", index=False)
            yield_by_catalyst(G).to_csv(KG_DIR / "graph" / "yield_by_catalyst.csv", index=False)
            paper_overlap(G).to_csv(KG_DIR / "graph" / "paper_overlap.csv", index=False)

            # Visualization
            from rag.graph.visualizer import visualize_graph
            viz_path = KG_DIR / "visualizations" / "reaction_network.html"
            viz_path.parent.mkdir(parents=True, exist_ok=True)
            visualize_graph(G, viz_path, top_n=top_n_graph)

            st.success(
                f"Graph built: **{stats['compounds']}** compounds · **{stats['reactions']}** reactions · "
                f"**{stats['total_edges']}** edges across **{stats['papers']}** papers"
            )
        except Exception as e:
            st.error(f"Graph build failed: {e}")
            st.exception(e)

if run_embeddings:
    with st.spinner("Embedding reactions with rxnfp…"):
        try:
            _build_embedding_index.clear()
            _load_saved_index.clear()
            idx = _build_embedding_index(data_root, preferred_run, per_paper_runs_json)

            index_path = KG_DIR / "embeddings" / "reaction_index.faiss"
            meta_path = KG_DIR / "embeddings" / "reaction_index.meta.json"
            index_path.parent.mkdir(parents=True, exist_ok=True)
            idx.save(index_path, meta_path)

            from rag.reaction_embeddings.visualizer import visualize_embedding_space
            umap_path = KG_DIR / "visualizations" / "reaction_embedding_space.html"
            umap_path.parent.mkdir(parents=True, exist_ok=True)
            visualize_embedding_space(idx, umap_path)

            # Enrich graph if it exists
            _load_saved_graph.clear()
            G = _load_saved_graph()
            if G is not None:
                idx.add_similar_reaction_edges(G, threshold=rxn_sim)
                from rag.graph.builder import save_graph
                save_graph(G, str(KG_DIR / "graph" / "reaction_network.json"))
                _load_saved_graph.clear()
                _get_hippo_engine.clear()
                _get_hybrid_engine.clear()

            st.success(f"Embedded **{len(idx._meta)}** reactions. UMAP saved.")
        except Exception as e:
            st.error(f"Embedding failed: {e}")
            st.exception(e)

if run_enrich:
    _G_enrich = _load_saved_graph()
    if _G_enrich is None:
        st.error("No saved graph found. Run '▶ Build / Rebuild Graph' first.")
    else:
        with st.spinner("Enriching knowledge graph…"):
            try:
                import asyncio as _asyncio
                from rag.graph.enrichment_agent import KGEnrichmentAgent
                from context import load_config as _lc
                _ecfg = _lc()
                _enrich_model = _ecfg.get("model", {}).get("rag_model_name", "openai/gpt-4o-mini")
                _enrich_log = st.empty()
                _enrich_messages: list[str] = []

                def _enrich_cb(msg: str) -> None:
                    _enrich_messages.append(msg)
                    _enrich_log.write("\n".join(_enrich_messages[-10:]))

                _agent = KGEnrichmentAgent(
                    G=_G_enrich,
                    data_root=data_root,
                    model_name=_enrich_model,
                    run_preference=preferred_run,
                )
                _stats = _asyncio.run(_agent.enrich(progress_callback=_enrich_cb))
                # Invalidate saved-graph cache so other tabs pick up enriched nodes
                _load_saved_graph.clear()
                _get_hippo_engine.clear()
                _get_hybrid_engine.clear()
                st.success(
                    f"Enrichment complete — "
                    f"**{_stats['n_articles']}** articles · "
                    f"**{_stats['n_names_filled']}** names filled · "
                    f"**{_stats['n_entries_verified']}** entries verified · "
                    f"**{_stats['n_concepts_added']}** concepts added · "
                    f"**{_stats.get('n_tables_summarised', 0)}** tables summarised"
                )
            except Exception as _e:
                st.error(f"Enrichment failed: {_e}")
                st.exception(_e)

# ── Tab: Overview ─────────────────────────────────────────────────────────────

with tab_overview:
    G = _load_saved_graph()
    if G is None:
        st.info("No graph built yet. Click **Build / Rebuild Graph** in the sidebar.")
    else:
        stats = _graph_stats(G)
        col1, col2, col3, col4, col5, col6 = st.columns(6)
        col1.metric("Compounds", stats["compounds"])
        col2.metric("Reactions", stats["reactions"])
        col3.metric("Temperatures", stats["temperatures"])
        col4.metric("Yield Ranges", stats["yield_ranges"])
        col5.metric("Total Edges", stats["total_edges"])
        col6.metric("Papers", stats["papers"])

        st.markdown("#### Edge type breakdown")
        et_df = pd.DataFrame(
            [{"Edge Type": k, "Count": v} for k, v in sorted(stats["edge_types"].items(), key=lambda x: -x[1])]
        )
        st.dataframe(et_df, use_container_width=True, hide_index=True)


# ── Tab: Graph Visualization ──────────────────────────────────────────────────

with tab_graph_viz:
    st.markdown("### Interactive Reaction Network")
    st.caption(
        "Blue = Compound · Red = Reaction · Node size = degree centrality. "
        "Hover nodes for details, drag to explore."
    )

    # ── Hierarchical filter: Article → Table → Entry ─────────────────────────
    _G_for_filter = _load_saved_graph()

    _col_art, _col_tbl, _col_ent, _col_n, _col_render = st.columns([2, 2, 1, 2, 1])

    with _col_art:
        _article_options = ["(all)"]
        if _G_for_filter is not None:
            _article_options += sorted({
                d["title"]
                for _, d in _G_for_filter.nodes(data=True)
                if d.get("node_type") == "Article"
            })
        _sel_article = st.selectbox("Article", _article_options, key="graph_viz_article")

    with _col_tbl:
        _table_options = ["(all)"]
        if _G_for_filter is not None and _sel_article != "(all)":
            _table_options += sorted({
                d["table_name"]
                for _, d in _G_for_filter.nodes(data=True)
                if d.get("node_type") == "Table" and d.get("paper") == _sel_article
            })
        _sel_table = st.selectbox("Table", _table_options, key="graph_viz_table")

    with _col_ent:
        _entry_options = ["(all)"]
        if _G_for_filter is not None:
            _art_f  = _sel_article if _sel_article != "(all)" else ""
            _tbl_f  = _sel_table   if _sel_table   != "(all)" else ""
            _entry_options += sorted({
                str(d.get("entry_id", ""))
                for _, d in _G_for_filter.nodes(data=True)
                if d.get("node_type") == "Reaction"
                and (not _art_f or d.get("paper") == _art_f)
                and (not _tbl_f or d.get("table") == _tbl_f)
                and d.get("entry_id") is not None
            }, key=lambda x: (len(x), x))
        _entry_sel = st.selectbox("Entry", _entry_options, key="graph_viz_entry")
        _entry_filter = "" if _entry_sel == "(all)" else _entry_sel

    with _col_n:
        _preview_n = st.number_input(
            "Max nodes (full view)",
            min_value=2, max_value=2000, value=top_n_graph, step=1,
            key="graph_viz_preview_n",
        )

    with _col_render:
        st.markdown("<br>", unsafe_allow_html=True)
        _rerender = st.button("Re-render", use_container_width=True, key="graph_viz_rerender")

    if _rerender:
        _G_saved = _load_saved_graph()
        if _G_saved is None:
            st.warning("No graph built yet.")
        else:
            with st.spinner("Rendering…"):
                try:
                    from rag.graph.visualizer import visualize_graph
                    import networkx as _nx

                    _viz_path = KG_DIR / "visualizations" / "reaction_network.html"
                    _viz_path.parent.mkdir(parents=True, exist_ok=True)
                    _HIDE_GLOBAL = {"SUBSTRUCTURE_OF", "SIMILAR_TO", "SIMILAR_REACTION"}

                    def _strip_global_edges(subg):
                        """Remove global cross-compound edges — irrelevant in focused views."""
                        _f = _nx.MultiDiGraph()
                        _f.add_nodes_from(subg.nodes(data=True))
                        _f.add_edges_from(
                            (u, v, d) for u, v, d in subg.edges(data=True)
                            if d.get("edge_type") not in _HIDE_GLOBAL
                        )
                        return _f

                    def _expand_to_quantities(subg_nodes, rxn_keys):
                        """Add 2-hop Quantity/Yield nodes and direct Time/Temp nodes for specific reactions."""
                        extras = set()
                        # Compound → Quantity/Yield (filtered by reaction)
                        for _n in subg_nodes:
                            if _G_saved.nodes[_n].get("node_type") != "Compound":
                                continue
                            for _s in _G_saved.successors(_n):
                                if _G_saved.nodes[_s].get("node_type") not in ("Quantity", "Yield"):
                                    continue
                                _b = _G_saved.get_edge_data(_n, _s) or {}
                                for _, _ed in _b.items():
                                    if _ed.get("reaction") in rxn_keys:
                                        extras.add(_s)
                                        break
                        # Reaction → Temperature / Time / Condition / Yield (direct successors)
                        for _rxn in rxn_keys:
                            if _rxn not in _G_saved:
                                continue
                            for _s in _G_saved.successors(_rxn):
                                nt = _G_saved.nodes[_s].get("node_type", "")
                                if nt in ("Temperature", "Time", "Condition", "Yield"):
                                    extras.add(_s)
                        return extras

                    _art = _sel_article if _sel_article != "(all)" else ""
                    _tbl = _sel_table  if _sel_table  != "(all)" else ""
                    _eid = _entry_filter.strip()

                    if _eid:
                        # ── Entry level ────────────────────────────────────────
                        _seed_nodes = [
                            n for n, d in _G_saved.nodes(data=True)
                            if d.get("node_type") == "Reaction"
                            and str(d.get("entry_id", "")) == _eid
                            and (not _art or d.get("paper") == _art)
                            and (not _tbl or d.get("table") == _tbl)
                        ]
                        if not _seed_nodes:
                            st.warning(f"No reaction found for entry '{_eid}'" +
                                       (f" in {_art}" if _art else "") +
                                       (f" / {_tbl}" if _tbl else ""))
                        else:
                            _nbr = set(_seed_nodes)
                            for _sn in _seed_nodes:
                                _nbr.update(_G_saved.successors(_sn))
                                _nbr.update(_G_saved.predecessors(_sn))
                            _nbr |= _expand_to_quantities(_nbr, set(_seed_nodes))
                            visualize_graph(_strip_global_edges(_G_saved.subgraph(_nbr)),
                                            _viz_path, top_n=len(_nbr))
                            st.success(f"Entry {_eid}: {len(_nbr)} nodes")

                    elif _tbl:
                        # ── Table level ────────────────────────────────────────
                        _rxns = [
                            n for n, d in _G_saved.nodes(data=True)
                            if d.get("node_type") == "Reaction"
                            and d.get("table") == _tbl
                            and (not _art or d.get("paper") == _art)
                        ]
                        if not _rxns:
                            st.warning(f"No reactions found in table '{_tbl}'")
                        else:
                            _tbl_key = next(
                                (n for n, d in _G_saved.nodes(data=True)
                                 if d.get("node_type") == "Table" and d.get("table_name") == _tbl),
                                None
                            )
                            _nbr = set(_rxns)
                            if _tbl_key:
                                _nbr.add(_tbl_key)
                                _nbr.update(_G_saved.predecessors(_tbl_key))
                            for _sn in _rxns:
                                _nbr.update(_G_saved.successors(_sn))
                                _nbr.update(_G_saved.predecessors(_sn))
                            _nbr |= _expand_to_quantities(_nbr, set(_rxns))
                            visualize_graph(_strip_global_edges(_G_saved.subgraph(_nbr)),
                                            _viz_path, top_n=len(_nbr))
                            st.success(f"Table '{_tbl}': {len(_rxns)} reactions, {len(_nbr)} nodes")

                    elif _art:
                        # ── Article level ──────────────────────────────────────
                        _art_key = f"ARTICLE__{_art}"
                        _rxns = [
                            n for n, d in _G_saved.nodes(data=True)
                            if d.get("node_type") == "Reaction" and d.get("paper") == _art
                        ]
                        _nbr = {_art_key} if _art_key in _G_saved else set()
                        for _sn in _rxns:
                            _nbr.add(_sn)
                            _nbr.update(_G_saved.successors(_sn))
                            _nbr.update(_G_saved.predecessors(_sn))
                        _nbr |= _expand_to_quantities(_nbr, set(_rxns))
                        visualize_graph(_strip_global_edges(_G_saved.subgraph(_nbr)),
                                        _viz_path, top_n=len(_nbr))
                        st.success(f"Article '{_art[:50]}': {len(_rxns)} reactions, {len(_nbr)} nodes")

                    else:
                        # ── Full graph (top-N) ─────────────────────────────────
                        visualize_graph(_G_saved, _viz_path, top_n=_preview_n)
                        st.success(f"Full graph: top {_preview_n} nodes")
                except Exception as _e:
                    st.error(f"Render failed: {_e}")
                    st.exception(_e)

    _embed_html(KG_DIR / "visualizations" / "reaction_network.html", height=750)

# ── Build RAG Index (sidebar button handler) ──────────────────────────────────

if build_index_btn:
    if not os.path.isdir(_rag_data_root):
        st.error("Please set a valid Extracted Data Folder path.")
    else:
        with st.status("Embedding papers with Gemini Embedding 2…", expanded=True) as _status:
            try:
                from context import get_parameters, CONTEXT_FILE
                _save_cfg = load_config()
                _save_cfg["extracted_data_root"] = _rag_data_root
                with open(CONTEXT_FILE, "w") as _f:
                    json.dump(_save_cfg, _f, indent=4)

                from rag.ingestion_extracted import ingest_extracted_data
                from rag.embeddings import EmbeddingRetriever
                chunk_size, chunk_overlap, _, _ = get_parameters()
                from rag.graph.loader import get_available_runs as _avail_fn2, _pick_run as _pick2
                _avail2 = _avail_fn2(_rag_data_root)
                _pp = st.session_state.get("rag_per_paper_runs_apps", {})
                run_sel = {
                    paper: _pp.get(paper) or _pick2(runs, _rag_pref)
                    for paper, runs in _avail2.items()
                }
                retriever = EmbeddingRetriever(db_path=_faiss_dir, model_name="gemini")
                summary = ingest_extracted_data(
                    data_root=_rag_data_root,
                    run_selection=run_sel,
                    retriever=retriever,
                    chunk_words=chunk_size,
                    chunk_overlap=chunk_overlap,
                    force_recreate=_force_reingest,
                )
                st.success(
                    f"Indexed **{summary['n_papers']}** papers — "
                    f"{summary['n_text_chunks']} text + {summary['n_image_chunks']} multimodal "
                    f"({summary['n_total']} total chunks)"
                )
                _status.update(label="Indexing complete!", state="complete", expanded=False)
                # Clear cached retriever so Chat uses the new index
                st.cache_resource.clear()
            except Exception as e:
                st.error(f"Ingestion failed: {e}")
                st.exception(e)
                _status.update(label="Ingestion failed!", state="error", expanded=True)

# ── Tab: Chat ─────────────────────────────────────────────────────────────

with tab_rag:
    st.markdown("### Chat")
    _faiss_index_file = os.path.join(_faiss_dir, "papers.faiss")
    _graph_available = (PROJECT_ROOT / "kg" / "graph" / "reaction_network.json").exists()

    if not os.path.exists(_faiss_index_file) and not _graph_available:
        st.info(
            "No index found. Build the **RAG Index** (sidebar) for text queries, "
            "or build the **Graph** for structural/reaction queries."
        )
    else:

        if "rag_messages" not in st.session_state:
            st.session_state["rag_messages"] = []
        if "rag_session_id" not in st.session_state:
            st.session_state["rag_session_id"] = new_session_id()
        if "rag_session_title" not in st.session_state:
            st.session_state["rag_session_title"] = ""

        # ── Session title + clear button ──────────────────────────────────────
        _title_col, _clear_col = st.columns([4, 1])
        with _title_col:
            if st.session_state["rag_session_title"]:
                st.caption(f"Session: **{st.session_state['rag_session_title'][:70]}**")
        with _clear_col:
            if st.session_state["rag_messages"]:
                if st.button("🗑 Clear", key="rag_clear", help="Clear conversation history"):
                    st.session_state["rag_messages"] = []
                    st.session_state["rag_session_id"] = new_session_id()
                    st.session_state["rag_session_title"] = ""
                    st.rerun()

        # ── Scrollable message area ───────────────────────────────────────────
        msg_area = st.container(height=560)
        with msg_area:
            for msg in st.session_state["rag_messages"]:
                with st.chat_message(msg["role"]):
                    st.markdown(msg["content"])
                    imgs = msg.get("images", [])
                    if imgs:
                        with st.expander(f"Referenced images ({len(imgs)})", expanded=False):
                            cols = st.columns(min(len(imgs), 2))
                            for i, img_item in enumerate(imgs):
                                if isinstance(img_item, dict):
                                    img_path = img_item["path"]
                                    caption = img_item.get("caption", Path(img_path).stem[:60])
                                else:
                                    img_path = img_item
                                    caption = Path(img_path).stem[:60]
                                if Path(img_path).exists():
                                    cols[i % 2].image(
                                        img_path,
                                        caption=caption,
                                        use_container_width=True,
                                    )

        # ── Chat input ────────────────────────────────────────────────────────
        if rag_prompt := st.chat_input(
            "Ask anything — prose, SMILES, SMARTS, or reaction>> …",
            key="rag_chat_input",
        ):
            if ">>" in rag_prompt:
                _spinner_label = "PPR Retriever + Reaction Similarity Agent…"
            elif _is_structural_query(rag_prompt):
                _spinner_label = "Substructure Search Agent + Hybrid Retriever…"
            else:
                _spinner_label = "GeminiRetriever + HippoRAG Agent (RRF Fusion)…"
            # History = all messages before the current question (last 3 exchanges = 6 msgs)
            _history = st.session_state["rag_messages"][-6:] if st.session_state["rag_messages"] else None
            st.session_state["rag_messages"].append({"role": "user", "content": rag_prompt})
            with st.spinner(_spinner_label):
                _loop = _get_or_create_eventloop()
                _response, _images = _loop.run_until_complete(
                    _run_rag_query(
                        _faiss_dir, rag_prompt,
                        conversation_history=_history,
                        model_classify=_model_classify,
                        model_entity=_model_entity,
                        model_summarize=_model_summarize,
                        model_synthesis=_model_synthesis,
                    )
                )
            st.session_state["rag_messages"].append({
                "role": "assistant",
                "content": _response or "No answer could be generated. Check the terminal for errors.",
                "images": _images,
            })
            # ── Auto-save session ─────────────────────────────────────────────
            if not st.session_state.get("rag_session_title"):
                _first_user = next(
                    (m["content"] for m in st.session_state["rag_messages"] if m["role"] == "user"),
                    "Untitled",
                )
                st.session_state["rag_session_title"] = _first_user[:60]
            save_session(
                st.session_state["rag_session_id"],
                st.session_state["rag_session_title"],
                st.session_state["rag_messages"],
                SESSIONS_DIR,
            )
            st.rerun()
