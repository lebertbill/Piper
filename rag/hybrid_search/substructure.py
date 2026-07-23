"""
SubstructureMatcher: SMARTS / SMILES substructure search over all Compound nodes in the graph.

Pre-compiles RDKit mol objects at construction so repeated searches are fast.
SMARTS auto-detection: strings containing '[' are treated as SMARTS; otherwise
parsed as SMILES and converted to a query mol via MolToSmarts.
"""
from __future__ import annotations

from dataclasses import dataclass

import networkx as nx
from rdkit import Chem
from rdkit.Chem import Descriptors


@dataclass
class SubstructureMatch:
    node_key: str        # InChIKey (or smiles_canonical as fallback)
    smiles: str
    label: str
    match_type: str      # "smarts" | "smiles_as_smarts"
    mw: float            # molecular weight, for result sorting


class SubstructureMatcher:
    def __init__(self, G: nx.MultiDiGraph):
        self._G = G
        # {node_key: (mol, smiles, label, mw)}
        self._compound_mols: dict[str, tuple[Chem.Mol, str, str, float]] = {}
        self._precompile_mols()

    def _precompile_mols(self) -> None:
        for node, data in self._G.nodes(data=True):
            if data.get("node_type") != "Compound":
                continue
            smiles = data.get("smiles_canonical", "")
            if not smiles:
                continue
            mol = Chem.MolFromSmiles(smiles)
            if mol is None:
                continue
            label = data.get("label_text") or smiles[:20]
            mw = Descriptors.MolWt(mol)
            self._compound_mols[node] = (mol, smiles, label, mw)

    @staticmethod
    def _parse_query(query: str) -> tuple[Chem.Mol | None, str]:
        """
        Returns (query_mol, match_type).
        match_type is "smarts" or "smiles_as_smarts".
        """
        query = query.strip()
        if "[" in query:
            qmol = Chem.MolFromSmarts(query)
            return qmol, "smarts"
        # Try SMILES first, convert to SMARTS query mol
        mol = Chem.MolFromSmiles(query)
        if mol is not None:
            smarts = Chem.MolToSmarts(mol)
            qmol = Chem.MolFromSmarts(smarts)
            return qmol, "smiles_as_smarts"
        # Last resort: try raw as SMARTS
        qmol = Chem.MolFromSmarts(query)
        return qmol, "smarts"

    def match(self, query: str, max_matches: int = 100) -> list[SubstructureMatch]:
        """
        Returns all Compound nodes whose SMILES contains `query` as a substructure.
        Results sorted by molecular weight descending (largest scaffold-containing first).
        """
        qmol, match_type = self._parse_query(query)
        if qmol is None:
            return []

        results: list[SubstructureMatch] = []
        for node_key, (mol, smiles, label, mw) in self._compound_mols.items():
            if mol.HasSubstructMatch(qmol):
                results.append(SubstructureMatch(
                    node_key=node_key,
                    smiles=smiles,
                    label=label,
                    match_type=match_type,
                    mw=mw,
                ))
            if len(results) >= max_matches:
                break

        results.sort(key=lambda r: r.mw, reverse=True)
        return results
