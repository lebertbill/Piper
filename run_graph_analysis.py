"""
End-to-end entry point for the knowledge graph pipeline.

Stages:
  1. Load all extraction_results.json files
  2. Build reaction network graph (with compound dedup + similarity edges)
  3. Save graph + render interactive HTML
  4. Print network analysis summaries
  5. Build reaction SMILES corpus + rxnfp embeddings
  6. Build FAISS index + render UMAP scatter
  7. Enrich graph with SIMILAR_REACTION edges from embedding similarity

Usage:
  python run_graph_analysis.py [data_root] [--top-n 200]

  data_root defaults to "extracted_data_ copy"
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).parent
DATA_ROOT_DEFAULT = ROOT / "extracted_data"
KG_DIR = ROOT / "kg"


def main():
    parser = argparse.ArgumentParser(description="Piper Knowledge Graph Pipeline")
    parser.add_argument("data_root", nargs="?", default=str(DATA_ROOT_DEFAULT))
    parser.add_argument("--top-n", type=int, default=200, help="Max nodes to show in graph visualization")
    parser.add_argument("--tanimoto", type=float, default=0.7, help="Tanimoto threshold for SIMILAR_TO edges")
    parser.add_argument("--rxn-sim", type=float, default=0.85, help="Cosine threshold for SIMILAR_REACTION edges")
    args = parser.parse_args()

    data_root = Path(args.data_root)
    if not data_root.exists():
        print(f"ERROR: data_root not found: {data_root}", file=sys.stderr)
        sys.exit(1)

    # ── 1. Load ──────────────────────────────────────────────────────────────
    print("\n[1/7] Loading extraction results…")
    from rag.graph.loader import load_all
    records = load_all(data_root)
    print(f"  Loaded {len(records)} tables from {len({r['paper'] for r in records})} papers")

    # ── 2. Build reaction network ─────────────────────────────────────────────
    print("\n[2/7] Building reaction network graph…")
    from rag.graph.builder import build_graph, save_graph
    G = build_graph(records, tanimoto_threshold=args.tanimoto)
    n_compounds = sum(1 for _, d in G.nodes(data=True) if d.get("node_type") == "Compound")
    n_reactions = sum(1 for _, d in G.nodes(data=True) if d.get("node_type") == "Reaction")
    print(f"  Nodes: {n_compounds} compounds, {n_reactions} reactions | Edges: {G.number_of_edges()}")

    # ── 3. Save graph ─────────────────────────────────────────────────────────
    print("\n[3/7] Saving graph…")
    graph_path = KG_DIR / "graph" / "reaction_network.json"
    save_graph(G, str(graph_path))

    from rag.graph.visualizer import visualize_graph
    viz_path = KG_DIR / "visualizations" / "reaction_network.html"
    visualize_graph(G, viz_path, top_n=args.top_n)

    # ── 4. Network analysis ───────────────────────────────────────────────────
    print("\n[4/7] Running network analysis…")
    from rag.graph.analysis import compound_centrality, yield_by_catalyst, paper_overlap, reagent_cooccurrence

    centrality_df = compound_centrality(G)
    print(f"\n  Top-10 compounds by PageRank:\n{centrality_df[['label','smiles','pagerank']].head(10).to_string(index=False)}")

    yield_df = yield_by_catalyst(G)
    if not yield_df.empty:
        print(f"\n  Yield by catalyst (top-5):\n{yield_df[['label','n_reactions','mean_yield','max_yield']].head(5).to_string(index=False)}")

    overlap_df = paper_overlap(G)
    if not overlap_df.empty:
        print(f"\n  Cross-paper compounds:\n{overlap_df[['label','n_papers']].head(10).to_string(index=False)}")

    # Save analysis CSVs
    centrality_df.to_csv(KG_DIR / "graph" / "compound_centrality.csv", index=False)
    yield_df.to_csv(KG_DIR / "graph" / "yield_by_catalyst.csv", index=False)
    overlap_df.to_csv(KG_DIR / "graph" / "paper_overlap.csv", index=False)

    # ── 5. Reaction SMILES corpus + embeddings ────────────────────────────────
    print("\n[5/7] Building reaction SMILES corpus and embeddings…")
    from rag.reaction_embeddings.reaction_smiles import build_reaction_smiles_corpus
    from rag.reaction_embeddings.embedder import ReactionEmbedder
    from rag.reaction_embeddings.index import ReactionIndex

    corpus = build_reaction_smiles_corpus(records)
    print(f"  Corpus: {len(corpus)} valid reaction SMILES")

    embedder = ReactionEmbedder(use_rxnfp=True)
    rxn_index = ReactionIndex(embedder=embedder)
    rxn_index.build(corpus)

    index_path = KG_DIR / "embeddings" / "reaction_index.faiss"
    meta_path = KG_DIR / "embeddings" / "reaction_index.meta.json"
    rxn_index.save(index_path, meta_path)

    # ── 6. UMAP visualization ─────────────────────────────────────────────────
    print("\n[6/7] Rendering UMAP embedding space…")
    from rag.reaction_embeddings.visualizer import visualize_embedding_space
    umap_path = KG_DIR / "visualizations" / "reaction_embedding_space.html"
    visualize_embedding_space(rxn_index, umap_path)

    # ── 7. Enrich graph with SIMILAR_REACTION edges ───────────────────────────
    print("\n[7/7] Adding SIMILAR_REACTION edges to graph…")
    rxn_index.add_similar_reaction_edges(G, threshold=args.rxn_sim)
    save_graph(G, str(graph_path))
    print(f"  Graph updated: {G.number_of_edges()} total edges")

    print("\nDone. Outputs in:", KG_DIR)


if __name__ == "__main__":
    main()
