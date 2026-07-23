"""
Builds a NetworkX MultiDiGraph from loaded extraction records.

Node types:
  Compound     — key = InChIKey (or smiles_canonical as fallback)
                 attrs: smiles_canonical, inchikey, label_text, aliases, papers, roles
  Reaction     — key = "{paper}__{table}__{entry_id}"
                 attrs: paper, table, entry_id, yield, temperature, time, solvent_smiles
  Temperature  — key = "TEMP__{value}" e.g. "TEMP__80.0"
                 attrs: value, unit, label  (e.g. "80 °C")
  Time         — key = "TIME__{rxn_key}"  (per-reaction, not merged)
                 attrs: value (hours), label (e.g. "12 h")
  Yield        — key = "YIELD__{rxn_key}"
                 attrs: value (float|None), label (e.g. "8%", "<10%", "15–20%", "trace")
  Article      — key = "ARTICLE__{paper}"
                 attrs: node_type="Article", title=paper, label=paper[:60]
  Table        — key = "TABLE__{paper}__{table}"
                 attrs: node_type="Table", paper=paper, table_name=table, label=table
  Quantity     — key = "QTY__{rxn_key}__{role[:3]}__{ckey[:12]}"
                 attrs: node_type="Quantity", value=float, unit=str, role=str, label="{value} {unit}"
  Condition    — key = "COND__{rxn_key}__{role}"
                 attrs: node_type="Condition", role, text, label

Edge types:
  HAS_REACTANT, HAS_PRODUCT, USES_CATALYST, USES_REAGENT, CONDUCTED_IN
  OCCURS_AT       Reaction → Temperature
  HAS_DURATION    Reaction → Time
  HAS_YIELD       Product → YieldRange  (yield of product formation, falls back to Reaction if no product SMILES)
  SIMILAR_TO      (Tanimoto ≥ threshold, added post-dedup)
  SUBSTRUCTURE_OF (RDKit substructure, added post-dedup)
  SIMILAR_REACTION (rxnfp cosine, added by embeddings module)
  HAS_TABLE       Article → Table
  HAS_ENTRY       Table → Reaction
  HAS_LOADING     Compound → Quantity  (loading/amount of that compound in this reaction)
  USES_CONDITION  Reaction → Condition (light source, atmosphere, etc.)
"""
from __future__ import annotations

import networkx as nx
from rdkit import Chem
from rdkit.Chem import AllChem, DataStructs

from .deduplicator import smiles_to_inchikey, deduplicate_compounds

_CATALYST_ROLES = {
    "photocatalyst", "catalyst", "metal catalyst", "co-catalyst", "cocatalyst",
    "metal_salt", "metal salt", "nickel catalyst", "palladium catalyst",
    "transition metal", "lewis acid",
}
_REAGENT_ROLES = {
    "reagent", "base", "oxidant", "reductant", "additive",
    "ligand", "chiral ligand", "co-ligand",
}
_YIELD_ROLES = {"yield"}
_TEMP_ROLES = {"temperature", "temp"}
_TIME_ROLES = {"time"}
_SOLVENT_ROLES = {"solvent"}
_CONDITION_ROLES = {
    "light", "light source", "atmosphere", "air", "nitrogen", "argon", "inert",
    "degas", "degassed", "inert atmosphere", "n2", "ar",
}


def _compound_key(smiles: str) -> str:
    ik = smiles_to_inchikey(smiles)
    return ik if ik else smiles


def _add_compound_node(G: nx.MultiDiGraph, smiles: str, label_text: str = "", paper: str = "") -> str:
    key = _compound_key(smiles)
    if key not in G:
        G.add_node(
            key,
            node_type="Compound",
            smiles_canonical=smiles,
            inchikey=smiles_to_inchikey(smiles),
            label_text=label_text,
            aliases=[label_text] if label_text else [],
            papers=[paper] if paper else [],
        )
    else:
        # Accumulate provenance
        if label_text and label_text not in G.nodes[key].get("aliases", []):
            G.nodes[key].setdefault("aliases", []).append(label_text)
        if paper and paper not in G.nodes[key].get("papers", []):
            G.nodes[key].setdefault("papers", []).append(paper)
    return key


def _extract_yield(conditions: list[dict]) -> float | None:
    for c in conditions:
        if c.get("role", "").lower() in _YIELD_ROLES:
            q = c.get("quantity")
            if q is not None:
                try:
                    return float(q)
                except (TypeError, ValueError):
                    pass
            # fallback: parse text
            text = str(c.get("text", "")).replace("%", "").strip()
            try:
                return float(text)
            except ValueError:
                pass
    return None


_RT_TEXTS = {"rt", "r.t.", "room temperature", "room temp", "ambient", "ambient temperature"}

def _extract_scalar(conditions: list[dict], roles: set[str]) -> float | None:
    for c in conditions:
        if c.get("role", "").lower() in roles:
            q = c.get("quantity")
            if q is not None:
                try:
                    return float(q)
                except (TypeError, ValueError):
                    pass
    return None


def _extract_temp_info(conditions: list[dict]) -> tuple[float | None, str | None]:
    """Return (numeric_value, display_label) for temperature.
    Handles text-only entries like 'rt', 'room temperature' → (25.0, 'rt').
    """
    for c in conditions:
        if c.get("role", "").lower() not in _TEMP_ROLES:
            continue
        q = c.get("quantity")
        text = (c.get("text") or "").strip()

        # Numeric quantity available
        if q is not None:
            try:
                v = float(q)
                lbl = f"{v:g} °C"
                if text:
                    t = text.lower().rstrip("°c ").strip()
                    if t in _RT_TEXTS or t.startswith("room"):
                        lbl = "rt"
                return v, lbl
            except (TypeError, ValueError):
                pass

        # Text-only fallback
        if text:
            tl = text.lower().strip()
            if tl in _RT_TEXTS or tl.startswith("room"):
                return 25.0, "rt"
            # Try to parse "80 °C", "80°C", "80"
            cleaned = tl.rstrip("°c ").replace("°", "").strip()
            try:
                v = float(cleaned)
                return v, f"{v:g} °C"
            except ValueError:
                pass
            # Return text as label with no numeric value
            return None, text

    return None, None


def _extract_time_info(conditions: list[dict]) -> tuple[float | None, str | None]:
    """Return (numeric_value_in_hours, display_label) for reaction time."""
    for c in conditions:
        if c.get("role", "").lower() not in _TIME_ROLES:
            continue
        q = c.get("quantity")
        text = (c.get("text") or "").strip()
        unit = (c.get("unit") or "").lower().strip()

        if q is not None:
            try:
                v = float(q)
                display_unit = unit or "h"
                lbl = text if text else f"{v:g} {display_unit}"
                return v, lbl
            except (TypeError, ValueError):
                pass

        if text:
            # "90 min" → 1.5 h
            m = _re.match(r"^(\d+(?:\.\d+)?)\s*(min|h|hour|hours|minutes?)\b", text.lower())
            if m:
                val, u = float(m.group(1)), m.group(2)
                v = val / 60 if "min" in u else val
                return v, text
            return None, text

    return None, None


def _extract_solvent_smiles(conditions: list[dict]) -> str | None:
    for c in conditions:
        if c.get("role", "").lower() in _SOLVENT_ROLES:
            return c.get("resolved_smiles") or c.get("smiles")
    return None


import re as _re


def _yield_label(conditions: list[dict]) -> str | None:
    """Return a human-readable yield label from the conditions list.

    Handles: plain numeric (8%), ranges (15–20%), operators (<10%, >80%), trace.
    """
    for c in conditions:
        if c.get("role", "").lower() not in _YIELD_ROLES:
            continue
        text = str(c.get("text", "")).replace("%", "").strip()
        qty  = c.get("quantity")

        if text:
            t = text.strip()
            # Trace / n.d.
            if t.lower() in ("trace", "traces", "nd", "n.d.", "-", "—"):
                return "trace"
            # Range  "15-20" or "15–20"
            if _re.match(r"^\d+[\-–]\d+$", t):
                return f"{t.replace('-', '–')}%"
            # Operator  "<10", ">80", "≤5", "≥90"
            if _re.match(r"^[<>≤≥]=?\d+", t):
                return f"{t}%"
            # Plain number in text
            try:
                return f"{float(t):g}%"
            except ValueError:
                pass

        if qty is not None:
            try:
                return f"{float(qty):g}%"
            except (TypeError, ValueError):
                pass

    return None


def _temp_node_key(temp: float) -> str:
    return f"TEMP__{temp}"


def _add_quantity_node(G: nx.MultiDiGraph, rxn_key: str, ckey: str, role: str, condition: dict) -> None:
    """Add a Quantity node for a condition that carries a numeric quantity."""
    qty = condition.get("quantity")
    if qty is None:
        return
    try:
        value = float(qty)
    except (TypeError, ValueError):
        return
    unit = condition.get("unit") or ""
    entry_id = rxn_key.split("__")[-1] if "__" in rxn_key else ""
    qty_key = f"QTY__{rxn_key}__{role[:3]}__{ckey[:12]}"
    label = f"{value:g} {unit} [E{entry_id}]".strip()
    if qty_key not in G:
        G.add_node(qty_key, node_type="Quantity", value=value, unit=unit, role=role, label=label)
    # Compound → HAS_LOADING → Quantity  (matches mental model: the compound has a loading)
    G.add_edge(ckey, qty_key, edge_type="HAS_LOADING", reaction=rxn_key)


def _normalize_name(name: str) -> str:
    """Lowercase, strip spaces/punctuation for use as dict key."""
    import re
    return re.sub(r"[^a-z0-9]", "", name.lower().strip())


def _add_text_compound_node(G, name: str, role: str, paper: str) -> str:
    """Create a Compound node for a named compound with no SMILES yet.
    Key is NAMED__{normalized_name} so same compound across reactions is merged."""
    key = f"NAMED__{_normalize_name(name)}"
    if key not in G:
        G.add_node(key, node_type="Compound", smiles_canonical=None,
                   inchikey=None, label_text=name, aliases=[name],
                   papers=[paper], no_smiles=True)
    else:
        data = G.nodes[key]
        if name not in data.get("aliases", []):
            data.setdefault("aliases", []).append(name)
        if paper not in data.get("papers", []):
            data.setdefault("papers", []).append(paper)
    return key


def _add_condition_node(G, name: str, role: str, rxn_key: str) -> str:
    """Create a Condition node for non-compound conditions (light, atmosphere, etc.)."""
    key = f"COND__{rxn_key}__{role}"
    label = name.strip() if name.strip() else role.replace("_", " ").title()
    if key not in G:
        G.add_node(key, node_type="Condition", role=role, text=name, label=label)
    return key


def build_graph(records: list[dict], tanimoto_threshold: float = 0.7) -> nx.MultiDiGraph:
    """
    Builds and returns a deduplicated reaction network graph.

    Args:
        records: output of graph.loader.load_all()
        tanimoto_threshold: cutoff for SIMILAR_TO edges (0–1)
    """
    G = nx.MultiDiGraph()

    for rec in records:
        paper = rec["paper"]
        table = rec["table_name"]
        entries = rec["entries"]

        # ── Article node ──────────────────────────────────────────────────────
        art_key = f"ARTICLE__{paper}"
        if art_key not in G:
            G.add_node(art_key, node_type="Article", title=paper, label=paper[:60])

        # ── Table node ────────────────────────────────────────────────────────
        tbl_key = f"TABLE__{paper}__{table}"
        if tbl_key not in G:
            G.add_node(tbl_key, node_type="Table", paper=paper, table_name=table, label=table)
        if not G.has_edge(art_key, tbl_key):
            G.add_edge(art_key, tbl_key, edge_type="HAS_TABLE")

        for entry in entries:
            entry_id = str(entry.get("entry_id", "?"))
            conditions = entry.get("conditions") or []

            yld        = _extract_yield(conditions)
            yld_label  = _yield_label(conditions)
            temp, temp_label = _extract_temp_info(conditions)
            time_val, time_label = _extract_time_info(conditions)

            rxn_key = f"{paper}__{table}__{entry_id}"
            G.add_node(
                rxn_key,
                node_type="Reaction",
                paper=paper,
                table=table,
                entry_id=entry_id,
                **{"yield": yld},
                temperature=temp,
                time=time_val,
                solvent_smiles=_extract_solvent_smiles(conditions),
            )

            # ── Table → Reaction edge ──────────────────────────────────────────
            G.add_edge(tbl_key, rxn_key, edge_type="HAS_ENTRY")

            # ── Reactant edges ────────────────────────────────────────────────
            for r in entry.get("reactants") or []:
                smiles = r.get("smiles_canonical") or r.get("smiles")
                if smiles and r.get("smiles_valid", True):
                    ckey = _add_compound_node(G, smiles, r.get("text", ""), paper)
                    G.add_edge(rxn_key, ckey, edge_type="HAS_REACTANT")

            # ── Product edges ─────────────────────────────────────────────────
            _product_keys: list[str] = []
            for p in entry.get("products") or []:
                smiles = p.get("smiles_canonical") or p.get("smiles")
                if smiles and p.get("smiles_valid", True):
                    ckey = _add_compound_node(G, smiles, p.get("text", ""), paper)
                    G.add_edge(rxn_key, ckey, edge_type="HAS_PRODUCT")
                    _product_keys.append(ckey)

            # ── Condition edges ───────────────────────────────────────────────
            for c in conditions:
                role = c.get("role", "").lower()
                smiles = c.get("resolved_smiles") or c.get("smiles")
                name = (c.get("resolved_name") or c.get("text", "")).strip()

                if role in _CATALYST_ROLES:
                    if smiles:
                        ckey = _add_compound_node(G, smiles, c.get("text", ""), paper)
                    elif name:
                        ckey = _add_text_compound_node(G, name, role, paper)
                    else:
                        continue
                    G.add_edge(rxn_key, ckey, edge_type="USES_CATALYST",
                               specific_role=role, quantity=c.get("quantity"), unit=c.get("unit"))
                    _add_quantity_node(G, rxn_key, ckey, role, c)

                elif role in _REAGENT_ROLES:
                    if smiles:
                        ckey = _add_compound_node(G, smiles, c.get("text", ""), paper)
                    elif name:
                        ckey = _add_text_compound_node(G, name, role, paper)
                    else:
                        continue
                    G.add_edge(rxn_key, ckey, edge_type="USES_REAGENT",
                               specific_role=role, quantity=c.get("quantity"), unit=c.get("unit"))
                    _add_quantity_node(G, rxn_key, ckey, role, c)

                elif role in _SOLVENT_ROLES:
                    if smiles:
                        ckey = _add_compound_node(G, smiles, c.get("text", ""), paper)
                    elif name:
                        ckey = _add_text_compound_node(G, name, role, paper)
                    else:
                        continue
                    G.add_edge(rxn_key, ckey, edge_type="CONDUCTED_IN", specific_role="solvent")
                    _add_quantity_node(G, rxn_key, ckey, role, c)

                elif role in _CONDITION_ROLES and name:
                    ck = _add_condition_node(G, name, role, rxn_key)
                    G.add_edge(rxn_key, ck, edge_type="USES_CONDITION", specific_role=role)

            # ── Temperature node + OCCURS_AT edge ────────────────────────────
            if temp_label:
                tkey = _temp_node_key(temp if temp is not None else temp_label)
                if tkey not in G:
                    G.add_node(tkey, node_type="Temperature",
                               value=temp, unit="°C", label=temp_label)
                G.add_edge(rxn_key, tkey, edge_type="OCCURS_AT")

            # ── Time node + HAS_DURATION edge ─────────────────────────────────
            if time_label:
                tk = f"TIME__{rxn_key}"
                if tk not in G:
                    G.add_node(tk, node_type="Time",
                               value=time_val, unit="h", label=time_label)
                G.add_edge(rxn_key, tk, edge_type="HAS_DURATION")

            # ── Yield: Product → HAS_YIELD → Yield (actual value) ────────────
            if yld_label:
                yk = f"YIELD__{rxn_key}"
                G.add_node(yk, node_type="Yield", value=yld, label=yld_label)
                if _product_keys:
                    for pk in _product_keys:
                        G.add_edge(pk, yk, edge_type="HAS_YIELD",
                                   yield_value=yld, reaction=rxn_key)
                else:
                    G.add_edge(rxn_key, yk, edge_type="HAS_YIELD",
                               yield_value=yld, reaction=rxn_key)

    # Deduplicate compound nodes by InChIKey
    G = deduplicate_compounds(G)

    # Enrich Compound nodes with aggregated roles
    _enrich_compound_roles(G)

    # Derived similarity + substructure edges
    _add_tanimoto_edges(G, tanimoto_threshold)
    _add_substructure_edges(G)

    return G


def _enrich_compound_roles(G: nx.MultiDiGraph) -> None:
    """Aggregate all roles a compound plays across reactions into node.roles."""
    _edge_to_role = {
        "HAS_REACTANT": "reactant",
        "HAS_PRODUCT": "product",
        "USES_CATALYST": "catalyst",
        "USES_REAGENT": "reagent",
        "CONDUCTED_IN": "solvent",
    }
    for node, data in G.nodes(data=True):
        if data.get("node_type") != "Compound":
            continue
        roles: set[str] = set()
        for _, _, ed in G.in_edges(node, data=True):
            r = _edge_to_role.get(ed.get("edge_type", ""))
            if r:
                roles.add(r)
        G.nodes[node]["roles"] = sorted(roles)


def _add_tanimoto_edges(G: nx.MultiDiGraph, threshold: float) -> None:
    compound_nodes = [(n, d) for n, d in G.nodes(data=True) if d.get("node_type") == "Compound"]
    fps = []
    valid_nodes = []
    for node, data in compound_nodes:
        smiles = data.get("smiles_canonical")
        if not smiles:
            continue
        mol = Chem.MolFromSmiles(smiles)
        if mol is None:
            continue
        fp = AllChem.GetMorganFingerprintAsBitVect(mol, radius=2, nBits=2048)
        fps.append(fp)
        valid_nodes.append(node)

    for i, (fp_a, node_a) in enumerate(zip(fps, valid_nodes)):
        sims = DataStructs.BulkTanimotoSimilarity(fp_a, fps[i + 1:])
        for j, sim in enumerate(sims):
            if sim >= threshold:
                node_b = valid_nodes[i + 1 + j]
                G.add_edge(node_a, node_b, edge_type="SIMILAR_TO", tanimoto=round(sim, 3))
                G.add_edge(node_b, node_a, edge_type="SIMILAR_TO", tanimoto=round(sim, 3))


def _add_substructure_edges(G: nx.MultiDiGraph) -> None:
    compound_nodes = [(n, d) for n, d in G.nodes(data=True) if d.get("node_type") == "Compound"]
    mols = {}
    for node, data in compound_nodes:
        smiles = data.get("smiles_canonical")
        if smiles:
            mol = Chem.MolFromSmiles(smiles)
            if mol:
                mols[node] = mol

    node_list = list(mols.keys())
    for i, node_a in enumerate(node_list):
        mol_a = mols[node_a]
        mw_a = mol_a.GetNumHeavyAtoms()
        for node_b in node_list[i + 1:]:
            mol_b = mols[node_b]
            mw_b = mol_b.GetNumHeavyAtoms()
            if abs(mw_a - mw_b) < 3:
                continue  # too similar in size — skip reflexive matches
            # Check if smaller is substructure of larger
            if mw_a > mw_b and mol_a.HasSubstructMatch(mol_b):
                G.add_edge(node_b, node_a, edge_type="SUBSTRUCTURE_OF")
            elif mw_b > mw_a and mol_b.HasSubstructMatch(mol_a):
                G.add_edge(node_a, node_b, edge_type="SUBSTRUCTURE_OF")


def save_graph(G: nx.MultiDiGraph, path: str) -> None:
    import json
    from networkx.readwrite import json_graph
    data = json_graph.node_link_data(G, edges="links")
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f)


def load_graph(path: str) -> nx.MultiDiGraph:
    import json
    from networkx.readwrite import json_graph
    with open(path, encoding="utf-8") as f:
        data = json.load(f)
    return json_graph.node_link_graph(data, directed=True, multigraph=True, edges="links")
