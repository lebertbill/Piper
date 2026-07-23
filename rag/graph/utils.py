"""
Shared utilities for compound display naming.

Paper-internal labels (e.g. "1a", "2b", "PC1") are meaningless outside the
paper they came from. display_name() filters those out and returns the best
available human-readable name, falling back to canonical SMILES.
"""
from __future__ import annotations

import re

# Matches paper-internal compound labels:
#   "1a", "2b", "3aa", "1'", "2" — digit(s) + lowercase letters / apostrophes only
#   "PC1", "PC2", "PC3t"         — PC + digit(s) + optional letters
#   "A1", "B2"                   — single capital + digit(s)
_PAPER_LABEL_RE = re.compile(
    r"^("
    r"\d+[a-z']*"           # 1a, 2b, 3aa, 1'
    r"|PC\w*\d+\w*"         # PC1, PC2, PC3t
    r"|[A-Z]\d+[a-zA-Z']*"  # A1, B2
    r")$"
)

def _is_paper_label(text: str) -> bool:
    t = text.strip()
    if not t:
        return True
    if len(t) <= 2:
        return True
    if _PAPER_LABEL_RE.match(t):
        return True
    # Catch repeated labels: "PC1 PC1", "4b-13b", "10 ( B )"
    parts = re.split(r"[\s\-()]+", t)
    non_empty = [p for p in parts if p]
    if non_empty and all(_PAPER_LABEL_RE.match(p) or not p or len(p) <= 2 for p in non_empty):
        return True
    return False


def _molecular_formula(smiles: str) -> str | None:
    """Compute molecular formula from SMILES via RDKit. Returns None on failure."""
    try:
        from rdkit import Chem
        from rdkit.Chem import rdMolDescriptors
        mol = Chem.MolFromSmiles(smiles)
        if mol:
            return rdMolDescriptors.CalcMolFormula(mol)
    except Exception:
        pass
    return None


def display_name(data: dict, max_len: int = 35) -> str:
    """
    Returns the best human-readable name for any graph node.

    Temperature  → "80 °C"
    YieldRange   → "High (≥80%)"
    Compound     → longest meaningful alias → molecular formula → SMILES
    Reaction     → "Paper | Entry X"
    """
    ntype = data.get("node_type", "")

    if ntype == "Temperature":
        return data.get("label", f"{data.get('value', '?')} °C")

    if ntype == "Time":
        return data.get("label", f"{data.get('value', '?')} h")

    if ntype in ("YieldRange", "Yield"):
        return data.get("label", "?")

    if ntype == "Reaction":
        paper = data.get("paper", "")[:30]
        entry = data.get("entry_id", "?")
        return f"{paper} | Entry {entry}"

    if ntype == "Condition":
        return data.get("label", data.get("text", data.get("role", "?")))

    # Compound: pubchem_name > context_resolved_name > meaningful alias > molecular formula > SMILES
    for _name_key in ("pubchem_name", "context_resolved_name"):
        _name = data.get(_name_key, "")
        if _name and not _is_paper_label(_name.strip()):
            n = _name.strip()
            return n[:max_len] + ("…" if len(n) > max_len else "")

    candidates = list(data.get("aliases", []))
    label = data.get("label_text", "")
    if label and label not in candidates:
        candidates.append(label)

    meaningful = [
        str(c).strip()
        for c in candidates
        if c and not _is_paper_label(str(c).strip())
    ]

    if meaningful:
        best = max(meaningful, key=len)
        return best[:max_len] + ("…" if len(best) > max_len else "")

    smiles = data.get("smiles_canonical", "")
    if smiles:
        formula = _molecular_formula(smiles)
        if formula:
            return formula
        return smiles[:max_len] + ("…" if len(smiles) > max_len else "")

    return label or "?"
