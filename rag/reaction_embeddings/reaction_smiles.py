"""
Converts extraction entries into reaction SMILES strings: "A.B>>C.D"
"""
from __future__ import annotations


def entry_to_reaction_smiles(entry: dict) -> str | None:
    """
    Builds a reaction SMILES from one extraction entry.
    Returns None if any reactant or product SMILES is invalid/missing.
    """
    reactant_smiles = []
    for r in entry.get("reactants") or []:
        if not r.get("smiles_valid", True):
            return None
        smiles = r.get("smiles_canonical") or r.get("smiles")
        if not smiles:
            return None
        reactant_smiles.append(smiles)

    product_smiles = []
    for p in entry.get("products") or []:
        if not p.get("smiles_valid", True):
            return None
        smiles = p.get("smiles_canonical") or p.get("smiles")
        if not smiles:
            return None
        product_smiles.append(smiles)

    if not reactant_smiles or not product_smiles:
        return None

    return ".".join(reactant_smiles) + ">>" + ".".join(product_smiles)


def build_reaction_smiles_corpus(records: list[dict]) -> list[dict]:
    """
    Iterates all records/entries and returns a flat list of:
      {reaction_smiles, paper, table_name, entry_id, yield}
    Skips entries where a valid reaction SMILES cannot be constructed.
    """
    corpus = []
    for rec in records:
        paper = rec["paper"]
        table = rec["table_name"]
        for entry in rec.get("entries") or []:
            rxn_smiles = entry_to_reaction_smiles(entry)
            if rxn_smiles is None:
                continue
            yld = None
            for c in entry.get("conditions") or []:
                if c.get("role", "").lower() == "yield":
                    try:
                        yld = float(c.get("quantity") or c.get("text", "").replace("%", ""))
                    except (TypeError, ValueError):
                        pass
            corpus.append(
                {
                    "reaction_smiles": rxn_smiles,
                    "paper": paper,
                    "table_name": table,
                    "entry_id": str(entry.get("entry_id", "")),
                    "yield": yld,
                }
            )
    return corpus
