"""
Compound deduplication by InChIKey.
Merges graph nodes that represent the same molecule across papers.
"""
from __future__ import annotations

import networkx as nx
from rdkit import Chem
from rdkit.Chem.inchi import MolToInchi, InchiToInchiKey


def smiles_to_inchikey(smiles: str) -> str | None:
    try:
        mol = Chem.MolFromSmiles(smiles)
        if mol is None:
            return None
        inchi = MolToInchi(mol)
        if inchi is None:
            return None
        return InchiToInchiKey(inchi)
    except Exception:
        return None


def deduplicate_compounds(G: nx.MultiDiGraph) -> nx.MultiDiGraph:
    """
    Merges all Compound nodes that share the same InChIKey into one canonical node.
    Connectivity is re-wired to the surviving node; aliases are accumulated.
    Returns a new graph.
    """
    compound_nodes = [n for n, d in G.nodes(data=True) if d.get("node_type") == "Compound"]

    # Group nodes by InChIKey
    inchikey_groups: dict[str, list[str]] = {}
    no_key: list[str] = []
    for node in compound_nodes:
        ik = G.nodes[node].get("inchikey")
        if ik:
            inchikey_groups.setdefault(ik, []).append(node)
        else:
            no_key.append(node)

    # Build merge map: old_node → canonical_node
    merge_map: dict[str, str] = {}
    for ik, nodes in inchikey_groups.items():
        if len(nodes) <= 1:
            continue
        # Pick the node with the most complete data as canonical
        canonical = max(nodes, key=lambda n: len(G.nodes[n].get("smiles_canonical", "")))
        for n in nodes:
            if n != canonical:
                merge_map[n] = canonical
                # Accumulate aliases onto canonical
                alias_set = set(G.nodes[canonical].get("aliases", []))
                alias_set.update(G.nodes[n].get("aliases", []))
                alias_set.add(G.nodes[n].get("label_text", ""))
                G.nodes[canonical]["aliases"] = list(filter(None, alias_set))

    if not merge_map:
        return G

    # Relabel nodes (networkx handles edge rewiring)
    return nx.relabel_nodes(G, merge_map, copy=True)
