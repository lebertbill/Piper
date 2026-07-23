
import logging
import os
import json
import re
import time
from typing import Dict, Any, List
from miner.agents.reaction_agent import ReactionAgent
from miner.agents.r_group_agent import RGroupAgent
from miner.agents.molecular_agent import MolecularAgent
from miner.agents.reaction_table_detector_agent import ReactionTableDetectorAgent
from miner.agents.label_resolver_agent import get_label_resolver_agent, LabelResolverAgent
from miner.diagnostic_logger import diagnostic_logger as _diag

_log = logging.getLogger(__name__)

import re as _re_module


def _pick_visualheist_for_item(item_id: str, output_dir: str) -> str:
    """
    When force_enable_visualheist_fallback is active, try to find a visualheist
    PNG for the given item_id (e.g. "Scheme 2", "Figure 3").

    Glob pattern : visualheist_page_NN_<type>_<num>.png
    Sort by page number ascending so the main article page is preferred over
    supplementary info pages that tend to appear later and be larger.

    Returns an absolute path string, or "" if nothing found.
    """
    import glob as _glob
    import re as _re_vh

    if not output_dir:
        return ""

    match = _re_vh.match(r"^(Table|Scheme|Figure)\s+(\d+)", item_id, _re_vh.IGNORECASE)
    if not match:
        return ""

    num = match.group(2)
    base_type = match.group(1).lower()
    processed_dir = os.path.join(output_dir, "processed")
    search_dir = processed_dir if os.path.isdir(processed_dir) else output_dir

    for vh_type in (base_type, "figure", "scheme", "table"):
        hits = _glob.glob(os.path.join(search_dir, f"visualheist_*_{vh_type}_{num}.png"))
        if hits:
            # Sort by page number (ascending) — lowest page = main article
            def _page_key(p):
                m = _re_vh.search(r"visualheist_page_(\d+)_", os.path.basename(p))
                return int(m.group(1)) if m else 9999
            hits.sort(key=_page_key)
            return os.path.abspath(hits[0])

    return ""


def _classify_participant_roles(participants: list) -> tuple:
    """
    Split a list of participant labels into (reactants, products).

    Two-phase role classification based on the article format:

    Phase 1 — same prefix families (e.g. ["4b", "4c"]):
        Within a family the alphabetically first label is the reactant, the rest
        are products.  This handles single-substrate optimisation tables.

    Phase 2 — singleton families with different suffix lengths (e.g. ["1a","2a","3aa"]):
        When all groups have exactly one member, labels with the SHORTEST suffix
        length are reactants; the label(s) with a LONGER suffix are products.
        This covers multi-component reactions where the product label encodes its
        precursors (e.g. 3aa = product of 1a + 2a).
        If all singleton suffixes are the same length the roles are ambiguous —
        return ([], []) so the LLM extraction stands unchanged.

    Pure-numeric labels ("1", "2") are reagents and excluded from both lists.

    Examples:
        ["4b", "4c"]          → (["4b"],        ["4c"])
        ["8b", "8d", "2"]     → (["8b"],        ["8d"])   (2 = reagent, dropped)
        ["1a", "2a", "3aa"]   → (["1a", "2a"],  ["3aa"])  (suffix len 1 < 2)
        ["1a", "2b", "3c"]    → ([],            [])       (all suffix len 1, ambiguous)
    """
    groups: dict = {}
    for lbl in participants:
        # Accept primed/double-primed labels which is very common in articles: "3aa'", "3aa''", "3a'" etc.
        m = _re_module.match(r"^(\d+)([a-zA-Z][a-zA-Z0-9'\"]*?)$", lbl)
        if m and m.group(2):
            groups.setdefault(m.group(1), []).append(lbl)
        # pure numbers → reagent (silently dropped)

    def _base_suffix(lbl: str) -> str:
        """Letter suffix with trailing primes stripped, for length comparison."""
        mm = _re_module.match(r"^(\d+)([a-zA-Z][a-zA-Z0-9]*)", lbl)
        return mm.group(2) if mm else ""

    reactants: list = []
    products: list = []
    singletons: dict = {}

    # Phase 1: classify multi member families
    for num, lbls in groups.items():
        if len(lbls) >= 2:
            # Check if this is a "prime famiy": all members share the same base suffix and differ only in trailing prime count (e.g. "3aa" and "3aa'").
            # Prime families represent stereoisomers — ALL members are products.
            # They must NOT steal a "reactant" slot that belongs to a simpler label.
            bases = {_base_suffix(l) for l in lbls}
            if len(bases) == 1:
                # All are the same compound in different isomeric forms → all products
                products.extend(sorted(lbls, key=lambda x: (x.count("'"), x)))
            else:
                # True variant family (e.g. "4b", "4c"): first alphabetically = reactant
                lbls_sorted = sorted(lbls)
                reactants.append(lbls_sorted[0])
                products.extend(lbls_sorted[1:])
        else:
            singletons[num] = lbls[0]

    # Phase 2: classify singletons by base-suffix length
    if singletons:
        suffix_len = {lbl: len(_base_suffix(lbl)) for lbl in singletons.values()}
        max_s = max(suffix_len.values())
        min_s = min(suffix_len.values())
        if max_s > min_s:
            for lbl, slen in suffix_len.items():
                (products if slen == max_s else reactants).append(lbl)
        elif products:
            # Products already identified from Phase 1 (prime family).
            # All remaining singletons with the same suffix length are reactants.
            reactants.extend(singletons.values())
        else:
            # All singletons have the same suffix length — standard heuristic failed.
            # Phase 3 fallback: in multi-component reactions papers number reactants sequentially (1a, 2a, 3a …) and give the product the next higher number 4a
            # When 3+ singletons all share the same suffix AND the highest numeric prefix is strictly above all others by exactly 1 step, treat the highest as the product and the rest as reactants.
            if len(singletons) >= 3:
                nums = sorted(int(n) for n in singletons)
                highest = max(nums)
                # Only apply if the rest form a contiguous sequence ending just below the highest (e.g. 1,2,3 -> highest 4, or 1,2 -> highest 3)
                rest = [n for n in nums if n != highest]
                if rest and highest == max(rest) + 1:
                    highest_lbl = singletons[str(highest)]
                    products.append(highest_lbl)
                    reactants.extend(v for k, v in singletons.items() if k != str(highest))

    return reactants, products


def _make_participant_mol(label: str, mapping: dict) -> dict:
    """Build a reactant/product mol dict from a participant_smiles_map entry."""
    mol = {
        "text": label,
        "smiles": mapping.get("smiles", ""),
        "smiles_source": mapping.get("smiles_source", "ocsr_coref"),
        "resolved_from": mapping.get("found_in", "unknown"),
    }
    if mapping.get("smiles_ocsr_raw"):
        mol["smiles_ocsr_raw"] = mapping["smiles_ocsr_raw"]
    if mapping.get("smiles_ocsr_graph"):
        mol["smiles_ocsr_graph"] = mapping["smiles_ocsr_graph"]
    return mol


class Orchestrator:
    """
    The central controller for the piper's multi-agent system.
    Coordinates the flow of data between specialized agents.
    """
    def __init__(self):
        from context import load_config
        config = load_config()
        agents_config = config.get("model", {}).get("agents", {})
        
        self.reaction_agent = ReactionAgent(model=agents_config.get("reaction_model"))
        self.r_group_agent = RGroupAgent(model=agents_config.get("r_group_model"))
        self.molecular_agent = MolecularAgent(model=agents_config.get("molecular_model"))
        self.table_detector = ReactionTableDetectorAgent()
        self.label_resolver_agent = get_label_resolver_agent()

        from miner.agents.text_reaction_agent import TextReactionAgent
        self.text_reaction_agent = TextReactionAgent(model=agents_config.get("text_reaction_model"))

        from miner.agents.fixed_condition_extractor_agent import FixedConditionExtractorAgent
        self.fixed_condition_extractor = FixedConditionExtractorAgent()

    def _save_intelligence_raw(self, data: Any, output_dir: str, agent_name: str):
        """Saves raw Intelligence output for debugging."""
        if not output_dir:
            return
        
        log_dir = os.path.join(output_dir, "intelligence_raw_logs")
        os.makedirs(log_dir, exist_ok=True)
        
        timestamp = int(time.time())
        filename = f"raw_{agent_name}_{timestamp}.json"
        filepath = os.path.join(log_dir, filename)
        
        try:
            with open(filepath, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2, ensure_ascii=False)
            _log.info(f"Saved raw Piper Intelligence output to: {filepath}")
        except Exception as e:
            _log.error(f"Failed to save raw Piper Intelligence log: {e}")

    def _clean_component(self, comp_list: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """Cleans chemical components (reactants/products) for Piper compatibility."""
        cleaned = []
        if not isinstance(comp_list, list):
            return []
            
        for item in comp_list:
            if not isinstance(item, dict):
                continue
            # Parse quantity: accept numeric or string like "0.1 mmol", "2.0 equiv"
            _qty_raw = item.get("quantity")
            _unit_raw = item.get("unit")
            # If quantity is a string like "0.1 mmol", split it
            if isinstance(_qty_raw, str) and _qty_raw.strip():
                import re as _re_cmp
                _qm = _re_cmp.match(r'([\d\.]+)\s*(.*)', _qty_raw.strip())
                if _qm:
                    try:
                        _qty_raw = float(_qm.group(1))
                        if not _unit_raw:
                            _unit_raw = _qm.group(2).strip() or None
                    except ValueError:
                        pass
            new_item = {
                "smiles": item.get("smiles"),
                "category": item.get("category"),
                "role": item.get("role") or item.get("category"),
                "text": item.get("text") or item.get("label"),
                "quantity": _qty_raw if isinstance(_qty_raw, (int, float)) else None,
                "unit": _unit_raw,
                "smiles_source": item.get("smiles_source"),
                "smiles_ocsr_raw": item.get("smiles_ocsr_raw"),
                "smiles_ocsr_graph": item.get("smiles_ocsr_graph"),
                "smiles_pubchem": item.get("smiles_pubchem"),
                "smiles_opsin": item.get("smiles_opsin"),
                "smiles_llm": item.get("smiles_llm"),
                "resolved_from": item.get("resolved_from"),
            }
            # Remove None values
            cleaned.append({k: v for k, v in new_item.items() if v is not None})
        return cleaned

    # Map of keywords found in condition text -> specific role name
    # Order matters: more specific matches should come first. written based on the used articles. This is a fallback only if llm emits a generic role. enrich_from_table_data can also overide the role.
    _ROLE_KEYWORDS = [
        # Photocatalysts — generic class keywords only, no specific compound names
        ("photocatalyst",  "photocatalyst"),
        ("photo catalyst", "photocatalyst"),
        ("photoredox",     "photocatalyst"),
        ("photosensitizer","photocatalyst"),
        ("ir(",            "photocatalyst"),
        ("ru(",            "photocatalyst"),
        # Metal salts / transition metal catalysts
        ("nicl",           "metal_salt"),
        ("nibr",           "metal_salt"),
        ("ni(",            "metal_salt"),
        ("nickel",         "metal_salt"),
        ("pdcl",           "metal_salt"),
        ("pd(",            "metal_salt"),
        ("pd2(",           "metal_salt"),
        ("palladium",      "metal_salt"),
        ("cucl",           "metal_salt"),
        ("cobalt",         "metal_salt"),
        # Ligands
        ("bpy",            "ligand"),
        ("phen",           "ligand"),
        ("dppp",           "ligand"),
        ("dppf",           "ligand"),
        ("binap",          "ligand"),
        ("xphos",          "ligand"),
        ("sphos",          "ligand"),
        ("ligand",         "ligand"),
        # Bases
        ("na2co3",         "base"),
        ("k2co3",          "base"),
        ("cs2co3",         "base"),
        ("k3po4",          "base"),
        ("et3n",           "base"),
        ("dbu",            "base"),
        ("koh",            "base"),
        ("naoh",           "base"),
        ("lda",            "base"),
        ("base",           "base"),
        # Light sources
        ("nm",             "light"),
        ("led",            "light"),
        ("kessil",         "light"),
        ("lamp",           "light"),
        ("light",          "light"),
        ("irradiat",       "light"),
        ("photon",         "light"),
        # Scale / reactor
        ("mmol scale",     "scale"),
        ("reaction performed on", "scale"),
        ("immersion-well", "reactor"),
        ("batch reactor",  "reactor"),
        # Atmosphere / degassing
        ("degass",         "degassing"),
        ("argon",          "atmosphere"),
        ("nitrogen atmosphere", "atmosphere"),
        ("under n2",       "atmosphere"),
        # Standard roles
        ("oxidant",        "oxidant"),
        ("reductant",      "reductant"),
        ("additive",       "additive"),
        ("solvent",        "solvent"),
        ("acid",           "acid"),
        ("catalyst",       "catalyst"),
        ("reagent",        "reagent"),
    ]

    def _parse_condition_item(self, item: dict) -> dict:
        """Converts a raw role/text condition dict into a structured entry.

        The LLM is instructed (chemmine_orchestration_logic_prompt.txt) to emit
        `quantity` and `unit` directly. This method only:
          1. Infers a more specific role from keywords when the raw role is generic.
          2. Strips noisy mapping notes from `text`.
          3. Acts as a safety-net regex fallback IF the LLM forgot to split the
             numeric quantity out of `text` AND did not populate `quantity`.
        """
        import re
        raw_role = (item.get("role") or "").strip().lower()
        text = (item.get("text") or "").strip()
        text_lower = text.lower()

        # Try to infer a more specific role from the text itself.
        inferred_role = raw_role
        if raw_role in ("", "reagent", "text", "catalyst"):
            for keyword, role_name in self._ROLE_KEYWORDS:
                if keyword in text_lower:
                    inferred_role = role_name
                    break

        # Remove noisy mapping notes that aren't part of the actual chemical data
        # e.g., "PC1 (1 mol%) (mapped to inset '1')" -> "PC1 (1 mol%)"
        text = re.sub(r'\s*\((?:mapped to|found in|refers to|labeled as)\s+[^)]+\)', '', text, flags=re.I).strip()

        result = {"role": inferred_role, "text": text}

        # Carry forward LLM-provided quantity/unit when present.
        # Accept both "quantity" (new schema) and "value" (legacy LLM output).
        _qty_raw = item.get("quantity") if item.get("quantity") not in (None, "") \
                   else item.get("value")
        if _qty_raw not in (None, ""):
            try:
                result["quantity"] = float(_qty_raw)
            except (TypeError, ValueError):
                pass
        if "unit" in item and item["unit"]:
            result["unit"] = str(item["unit"]).strip()
        # Carry forward any other LLM-set fields the downstream pipeline uses.
        for k in ("smiles", "label"):
            if item.get(k):
                result[k] = item[k]

        # Safety net: only if LLM did NOT supply quantity, parse a trailing number+unit out of the text.
        if "quantity" not in result:
            qty_unit_re = re.compile(
                r'(?P<num>\d+(?:\.\d+)?)\s*'
                r'(?P<unit>mol\s*%|mol/L|equiv\.?|mmol|mL|°C|atm|nm|h|K|M|%)'
                r'(?=$|[\s,;)\]/])'
            )
            m = qty_unit_re.search(text)
            if m:
                result["quantity"] = float(m.group("num"))
                result["unit"] = re.sub(r'\s+', '', m.group("unit"))
                cleaned = (text[:m.start()] + text[m.end():]).strip()
                cleaned = re.sub(r'\(\s*\)', '', cleaned)
                cleaned = re.sub(r',\s*\)', ')', cleaned)
                cleaned = re.sub(r'\(\s*,\s*', '(', cleaned)
                cleaned = re.sub(r'\s+,', ',', cleaned)
                cleaned = re.sub(r'\s{2,}', ' ', cleaned).strip(' ,;')
                if cleaned:
                    result["text"] = cleaned
        return result

    def _clean_conditions(self, cond_input) -> List[Dict[str, Any]]:
        """Handles R-group conditions which come as [[{role, text}, ...]] (list-of-lists).
        Flattens and parses each item, extracting quantity/unit where available."""
        if not cond_input:
            return []
        flat = []
        for item in cond_input:
            if isinstance(item, list):
                flat.extend(item)  
            elif isinstance(item, dict):
                flat.append(item)
        return [self._parse_condition_item(c) for c in flat if isinstance(c, dict)]

    #  Columns that are METADATA about the entry, not reaction conditions 
    _SKIP_COLUMNS = {"entry"}
    #  Columns that map to a yield / outcome metric 
    # Substring-matched against the normalized column key (e.g. "yield_percent", "conv._a_percent", "conversion_percent"). Outcome columns replace the baseline yield rather than being appended as a separate role.
    _YIELD_COLUMN_PATTERNS = ("yield", "conv", "conversion")

    # Aliases between column key roles and condition role names. The parsed-table column "Photocatalyst" normalizes to "catalyst", but the LLM emits role "photocatalyst" — without aliasing we'd append a duplicate.
    _ROLE_ALIASES = {
        "catalyst": ("photocatalyst", "catalyst"),
        "photocatalyst": ("photocatalyst", "catalyst"),
        "temp": ("temperature",),
        "temperature": ("temperature",),
    }

    def _enrich_from_table_data(
        self,
        entries: List[Dict[str, Any]],
        parsed_table: Dict[str, Any],
        footnotes: Dict[str, str] = None,
        columns_metadata: Dict[str, Any] = None,
        participant_smiles_map: Dict[str, Any] = None,
        compound_name_map: Dict[str, str] = None,
    ) -> List[Dict[str, Any]]:
        """Merge structured markdown-table data into LLM entries.
        
        Ensures 100% data parity: one entry for every row in the table.
        If the LLM truncated the output, missing entries are created using
        the first entry as a structural template.
        """
        import re
        import copy
        table_rows = parsed_table.get("entries", [])
        if not table_rows:
            return entries

        # Normalize the column metadata keys
        from miner.table_parser import normalize_header
        normalized_metadata = {}
        if columns_metadata:
            for k, v in columns_metadata.items():
                normalized_metadata[normalize_header(k)] = v

        footnotes = footnotes or parsed_table.get("footnotes", {})
        
        def _make_condition(role: str, value) -> Dict[str, Any]:
            return self._parse_condition_item({"role": role, "text": str(value) if value is not None else ""})

        # Build map of entry_id -> entry
        entry_map = {e.get("entry_id"): e for e in entries}
        final_entries = []

        # We use Entry 1 as the structural baseline for created rows
        baseline_entry = entries[0] if entries else None

        # Positional list: LLM entries sorted by their numeric ID.
        # Used when the LLM used the paper's own entry numbering (e.g. 5–13) which doesn't overlap with the sequential 1..N the parity check expects.
        def _entry_sort_key(e):
            try:
                return int(str(e.get("entry_id", "0")).strip())
            except ValueError:
                return 0

        sorted_lm_entries = sorted(entries, key=_entry_sort_key)

        # Detect whether the LLM IDs align with sequential 1..N.
        # If the smallest LLM entry_id is > 1 the LLM used the paper's own entry numbering (e.g. 5–13 for a table whose first row is entry 5). In that case pair markdown rows positionally instead of by sequential ID so that no LLM entries are silently dropped or mismatched.
        sequential_ids = {str(i + 1) for i in range(len(table_rows))}
        _min_lm_id = _entry_sort_key(sorted_lm_entries[0]) if sorted_lm_entries else 1
        use_positional = _min_lm_id > 1

        # Positional cursor: tracks which sorted LLM entry to use next when ID-based lookup misses 
        _pos_idx = 0

        for i, row in enumerate(table_rows):
            eid = str(i + 1)

            if use_positional:
                # Pair markdown row i with sorted LLM entry i directly.
                entry = sorted_lm_entries[i] if i < len(sorted_lm_entries) else None
            else:
                entry = entry_map.get(eid)
                # Fallback: if this sequential ID wasnt extracted by the LLM, use the next unmatched sorted entry rather than a blank clone.
                if not entry:
                    while _pos_idx < len(sorted_lm_entries):
                        candidate = sorted_lm_entries[_pos_idx]
                        _pos_idx += 1
                        if str(candidate.get("entry_id", "")) not in sequential_ids:
                            entry = candidate
                            break

            # Last resort: create from baseline when no LLM entry is available
            if not entry:
                if not baseline_entry:
                    _log.warning(f"Orchestrator: No baseline entry to create row {eid}")
                    continue
                _log.info(f"Orchestrator: Auto-generating missing entry {eid} from table row.")
                entry = copy.deepcopy(baseline_entry)
                entry["entry_id"] = eid
                # Do NOT clear conditions; keep global parameters (Ni, Ir, etc.) extracted from the reaction scheme in the baseline entry.
                entry.pop("yield", None)

            conditions = list(entry.get("conditions", []))
            yield_val = None
            variations_to_apply = []

            # If the LLM already extracted multiple yield conditions (one per product), the markdown table cannot represent this structure — skip yield enrichment to avoid overwriting correct multi-product yields with a single wrong value.
            _llm_yield_count = sum(1 for c in conditions if c.get("role") == "yield")
            _skip_yield_enrichment = _llm_yield_count > 1

            for col_key, cell_value in row.items():
                if col_key in self._SKIP_COLUMNS or cell_value is None:
                    continue

                col_meta = normalized_metadata.get(col_key, {})
                # Treat as variation focus if explicitly typed or if the column name implies adaptation/deviation
                is_variation = (
                    col_meta.get("type") in ("variable_from_default", "variation_from_standard") or
                    any(keyword in col_key.lower() for keyword in ("deviation", "variation", "standard condition"))
                )

                if is_variation:
                    variations_to_apply.append({"role": col_key, "text": str(cell_value)})
                    continue

                # Outcome columns (yield / conversion) — replace baseline yield.
                # Skipped when the LLM already produced per-product yields.
                if any(p in col_key for p in self._YIELD_COLUMN_PATTERNS):
                    if _skip_yield_enrichment:
                        continue
                    yield_text = str(cell_value)
                    yield_val = yield_text
                    conditions = [c for c in conditions if c.get("role") != "yield"]
                    cond = {"role": "yield", "text": yield_text}
                    m = re.search(r'([\d]+(?:\.[\d]+)?)', yield_text)
                    if m:
                        cond["quantity"] = float(m.group(1))
                        cond["unit"] = "%"
                    conditions.append(cond)
                    continue

                # Detect "[compound_label] (unit)" columns — e.g. "1a (equiv.)", "2a (mmol)". These carry per-row quantities for a specific participant rather than a new condition.  Add/update quantity on the matching reactant or product entry; skip condition logic.
                _lbl_unit_m = re.match(
                    r'^([A-Za-z0-9][A-Za-z0-9\-\']*?)\s*[\(\[]?(equiv\.?|mmol|mol%|mg|mL|g|M)[\)\]]?$',
                    col_key.replace('_', ' ').strip(), re.I
                )
                if _lbl_unit_m:
                    _ptcp_lbl  = _lbl_unit_m.group(1)
                    _ptcp_unit = _lbl_unit_m.group(2).replace('.', '').strip()
                    for _ptcp_list in (entry.get("reactants", []), entry.get("products", [])):
                        for _ptcp in _ptcp_list:
                            if _ptcp.get("text") == _ptcp_lbl:
                                try:
                                    _ptcp["quantity"] = float(str(cell_value).replace(',', '.'))
                                    _ptcp["unit"]     = _ptcp_unit
                                except (TypeError, ValueError):
                                    pass
                    continue

                # Per-row condition value: REPLACE the existing condition with the same role (handling aliases like Photocatalyst ↔ catalyst) so we don't end up with two `base`, two `solvent`, or a stale photocatalyst from the baseline copy of entry 1.
                target_roles = self._ROLE_ALIASES.get(col_key, (col_key,))
                replaced = False
                for c in conditions:
                    if c.get("role") in target_roles:
                        new_cond = _make_condition(c.get("role"), cell_value)
                        c.clear()
                        c.update(new_cond)
                        replaced = True
                        break
                if not replaced:
                    col_text = str(cell_value)
                    if not any(c.get("text") == col_text for c in conditions):
                        conditions.append(_make_condition(col_key, cell_value))

            if variations_to_apply and conditions:
                # Store the raw deviation text so it's visible in the output
                for v in variations_to_apply:
                    conditions.append({"role": "deviation_from_standard", "text": v["text"]})
                try:
                    variation_texts = [f"{v['role']}: {v['text']}" for v in variations_to_apply]
                    conditions = self._apply_condition_variation(conditions, " | ".join(variation_texts))
                except Exception as e:
                    _log.error(f"Failed to apply variation: {e}")
            
            # Safety net: if any photocatalyst/catalyst condition ended up with a purely-numeric text, ask an LLM whether it is a legitimate compound label or a misplaced mol% quantity value.
            _CATALYST_ROLES = {"photocatalyst", "catalyst"}
            for c in conditions:
                if c.get("role") in _CATALYST_ROLES:
                    _txt = str(c.get("text", "")).strip()
                    if not _txt:
                        continue
                    try:
                        float(_txt.replace(',', '.'))
                        _is_numeric = True
                    except ValueError:
                        _is_numeric = False
                    if not _is_numeric:
                        continue

                    _is_label = self._numeric_text_is_compound_label(
                        text=_txt,
                        role=c.get("role", ""),
                        existing_quantity=c.get("quantity"),
                        compound_name_map=compound_name_map or {},
                        columns_metadata=columns_metadata or {},
                    )
                    if not _is_label:
                        try:
                            c.setdefault("quantity", float(_txt.replace(',', '.')))
                            c.setdefault("unit", "mol%")
                        except (TypeError, ValueError):
                            pass
                        c["text"] = ""

            # Store variation text for post-injection substitution.
            # LLM entries often have reactant text=None at this stage; the actual substitution runs after participant injection populates the base reactants (see post-injection loop below).
            if variations_to_apply:
                combined_variation = " | ".join(v["text"] for v in variations_to_apply)
                entry["_pending_variation_text"] = combined_variation

            entry["conditions"] = conditions

            if yield_val is not None:
                entry["yield"] = yield_val

            final_entries.append(entry)

        # Preserve any LLM entries that had no corresponding markdown row (e.g. LLM extracted 13 entries but markdown only has 9 rows).
        appended_ids = {e.get("entry_id") for e in final_entries}
        for e in sorted_lm_entries:
            if e.get("entry_id") not in appended_ids:
                final_entries.append(e)

        return final_entries

    def _numeric_text_is_compound_label(
        self,
        text: str,
        role: str,
        existing_quantity,
        compound_name_map: dict,
        columns_metadata: dict,
    ) -> bool:
        """
        Returns True if a purely-numeric condition text is a legitimate compound label (an integer shorthand for a named compound), False if it is a misplaced mol% quantity value that should be cleared.

        Fast paths (no LLM):
          1. compound_name_map has a key whose numeric suffix matches text,
             separated by a space, hyphen, or no separator at a word boundary
             — indicating the number is used as a compound index in this paper.
          2. Any column metadata for this role has value_is_abbreviation=True
             or a label_map_source_id — the column explicitly uses integer
             shorthands for named compounds.

        LLM fallback for ambiguous cases.
        """
        # Fast path 1: any compound_name_map key ends with this number (space-, hyphen-, or directly-appended suffix, e.g. "Cat 2", "Ir-3", "PC4")
        for key in (compound_name_map or {}):
            stripped = key.rstrip()
            if (stripped.endswith(f" {text}") or
                    stripped.endswith(f"-{text}") or
                    (stripped.endswith(text) and
                     len(stripped) > len(text) and
                     not stripped[-len(text) - 1].isdigit())):
                _diag.write(f"  [SafetyNet] '{text}' matched compound_name_map key '{key}' → treat as label")
                return True

        # Fast path 2: column metadata indicates abbreviation / label mapping
        _role_norm = role.lower().replace("_", "").replace(" ", "")
        for col_name, col_meta in (columns_metadata or {}).items():
            _col_norm = col_name.lower().replace("_", "").replace(" ", "")
            if _role_norm in _col_norm or _col_norm in _role_norm:
                if col_meta.get("value_is_abbreviation") or col_meta.get("label_map_source_id"):
                    _diag.write(f"  [SafetyNet] column '{col_name}' has abbreviation/label mapping → '{text}' is a label")
                    return True

        # LLM fallback: ask the model to clarify with full context
        try:
            import json as _json
            from context import load_config as _lc, get_openrouter_url as _oru
            from openai import OpenAI as _OpenAI
            import os as _os

            cfg = _lc()
            model = cfg.get("model", {}).get("agents", {}).get(
                "label_resolver_model", "google/gemini-flash-1.5")
            api_key = (_os.environ.get("OPENROUTER_API_KEY")
                       or _os.environ.get("API_KEY")
                       or _os.environ.get("OPENAI_API_KEY"))
            client = _OpenAI(api_key=api_key, base_url=_oru())

            map_keys = list((compound_name_map or {}).keys())[:20]
            prompt = (
                f"In a chemistry paper extraction, a '{role}' condition has text='{text}'.\n"
                f"Known compound labels in this paper: {map_keys}\n"
                f"Existing quantity already recorded: {existing_quantity}\n\n"
                f"Question: Is '{text}' a compound label/shorthand (e.g. '1' meaning 'Photocatalyst 1') "
                f"or is it a misplaced numeric quantity value (e.g. mol%) that accidentally "
                f"replaced the real compound name?\n\n"
                f'Return JSON: {{"is_label": true/false, "reason": "..."}}'
            )
            resp = client.chat.completions.create(
                model=model, temperature=0,
                messages=[
                    {"role": "system", "content": "You are a chemistry data extraction assistant. Output JSON only."},
                    {"role": "user",   "content": prompt},
                ],
                response_format={"type": "json_object"},
            )
            result = _json.loads(resp.choices[0].message.content or "{}")
            is_label = bool(result.get("is_label", False))
            _diag.write(f"  [SafetyNet LLM] '{text}' is_label={is_label} reason={result.get('reason','')}")
            return is_label
        except Exception as e:
            _diag.write(f"  [SafetyNet LLM] failed ({e}) — defaulting to quantity (safe)")
            return False

    def _apply_condition_variation(self, base_conditions: List[Dict], variation_text: str) -> List[Dict]:
        """Uses an LLM to smartly alter the base_conditions based on the variation_text."""
        from context import load_config, get_openrouter_url
        from openai import OpenAI
        import os
        import json

        if not variation_text:
            return base_conditions
            
        clean_text = variation_text.strip().lower()
        if ":" in clean_text:
            clean_text = clean_text.split(":", 1)[1].strip()
            
        if not clean_text or clean_text in ("none", "nd", "-", "n/a", "standard", "baseline"):
            return base_conditions

        config = load_config()
        api_key = os.environ.get("OPENROUTER_API_KEY") or os.getenv("API_KEY")
        base_url = get_openrouter_url()
        model_name = config.get("model", {}).get("agents", {}).get("molecular_model", "openai/gpt-4o")

        client = OpenAI(api_key=api_key, base_url=base_url)

        prompt_path = os.path.join(os.path.dirname(__file__), "prompts", "condition_variation_prompt.txt")
        try:
            with open(prompt_path, "r", encoding="utf-8") as f:
                base_prompt = f.read()
                prompt = base_prompt.format(
                    base_conditions=json.dumps(base_conditions, indent=2),
                    variation_text=variation_text
                )
        except Exception as e:
            _log.error(f"Failed to load variation prompt: {e}")
            return base_conditions
        try:
            response = client.chat.completions.create(
                model=model_name,
                temperature=0,
                messages=[{"role": "user", "content": prompt}]
            )
            from miner.token_tracker import tracker
            if hasattr(response, "usage") and response.usage:
                tracker.record("ConditionVariationAgent", model_name,
                               response.usage.prompt_tokens or 0,
                               response.usage.completion_tokens or 0)
            content = response.choices[0].message.content.strip()
            # Clean up markdown formatting if present
            if content.startswith("```json"):
                content = content[7:]
            if content.endswith("```"):
                content = content[:-3]
            updated_conditions = json.loads(content.strip())
            if isinstance(updated_conditions, list):
                return updated_conditions
        except Exception as e:
            _log.error(f"LLM variation failed: {e}")
        
        # On failure, return original
        return base_conditions

    def _expand_range_labels_llm(self, texts: List[str]) -> Dict[str, List[str]]:
        """
        Use an LLM to detect and expand range-notation compound labels.

        Takes a batch of condition text strings and returns a dict mapping each
        text to its expanded labels (or [] if not a range).  Batching avoids
        one LLM call per condition.
        """
        if not texts:
            return {}

        try:
            from pathlib import Path as _Path
            _SKILLS_DIR = _Path(__file__).parent / "skills"
            prompt = (_SKILLS_DIR / "range_label_expansion.md").read_text().strip()
        except Exception as e:
            _log.warning(f"[RangeExpand] Failed to load skill prompt: {e}")
            return {}

        try:
            import json as _json
            from miner.agents.base_agent import BaseAgent
            from context import load_config as _lc
            _cfg = _lc()
            model = _cfg.get("model", {}).get("agents", {}).get(
                "label_resolver_model", "google/gemini-3-flash-preview"
            )
            agent = BaseAgent(model=model)
            messages = [
                {"role": "system", "content": "You are a helpful assistant that outputs JSON."},
                {"role": "user", "content": (
                    f"{prompt}\n\n"
                    f"Input:\n{_json.dumps({'labels': texts}, ensure_ascii=False)}"
                )},
            ]
            raw = agent._call_llm(messages, response_format={"type": "json_object"})
            result = agent._parse_json(raw) or {}
            # Validate: each value must be a list of strings
            return {
                k: v for k, v in result.items()
                if isinstance(v, list) and all(isinstance(s, str) for s in v)
            }
        except Exception as e:
            _log.warning(f"[RangeExpand] LLM call failed: {e}")
            return {}

    def _apply_reactant_substitution(
        self,
        reactants: List[Dict],
        variation_text: str,
        participant_smiles_map: Dict[str, Any],
        compound_name_map: Dict[str, str],
    ) -> List[Dict]:
        """
        Parse "X instead of Y" patterns in variation_text and replace reactant Y with X.
        Resolves SMILES for X via participant_smiles_map ->compound_name_map -.> PubChem.
        Returns the original list unchanged if no substitution pattern is found.
        """
        # Find all "X instead of Y" patterns
        substitutions = {}  # old_label -> new_label
        for m in re.finditer(
            r'\bwith\s+(\S+?)\s+instead\s+of\s+(\S+)',
            variation_text, re.IGNORECASE
        ):
            new_lbl = m.group(1).strip('.,;()')
            old_lbl = m.group(2).strip('.,;()')
            substitutions[old_lbl] = new_lbl
        # Also catch bare "X instead of Y" without leading "with"
        if not substitutions:
            for m in re.finditer(
                r'(\S+?)\s+instead\s+of\s+(\S+)',
                variation_text, re.IGNORECASE
            ):
                new_lbl = m.group(1).strip('.,;()')
                old_lbl = m.group(2).strip('.,;()')
                substitutions[old_lbl] = new_lbl

        if not substitutions:
            return reactants

        updated = []
        for r in reactants:
            old_text = r.get("text", "")
            if old_text not in substitutions:
                updated.append(r)
                continue

            new_text = substitutions[old_text]
            new_r = {"text": new_text}

            # Tier 1: participant_smiles_map (OCSR-resolved from the same image)
            if new_text in participant_smiles_map:
                mapping = participant_smiles_map[new_text]
                new_r["smiles"] = mapping.get("smiles", "")
                new_r["smiles_source"] = mapping.get("smiles_source", "ocsr_coref")
                new_r["resolved_from"] = mapping.get("found_in", "participant_smiles_map")

            # Tier 2: compound_name_map -> full label resolver chain (PubChem -> OPSIN -> LLM)
            elif new_text in compound_name_map:
                full_name = compound_name_map[new_text]
                new_r["resolved_name"] = full_name
                try:
                    resolver = getattr(self, "label_resolver_agent", None)
                    if resolver is not None:
                        smi, src = resolver._resolve_name_to_smiles(full_name)
                    else:
                        smi = LabelResolverAgent._pubchem_name_to_smiles_simple(full_name)
                        src = "pubchem_lookup"
                    if smi:
                        new_r["smiles"] = smi
                        new_r["smiles_source"] = src
                except Exception:
                    pass

            # Tier 3: no mapping available — leave unresolved for downstream handling
            if "smiles" not in new_r:
                new_r["smiles_source"] = "pending_resolution"

            print(f"  [ReactantSubstitution] Entry: replaced '{old_text}' → '{new_text}'"
                  f" (smiles_source={new_r.get('smiles_source', 'none')})")
            updated.append(new_r)

        return updated

    def process_document(self, scheme_image_path: str = None, table_data: Any = None,
                         text_content: str = None, full_text_content: str = None,
                         use_mllm: bool = True,
                         footnote_dependencies: list = None,
                         has_arrow_parameters: bool = False,
                         arrow_mapping: str = None,
                         has_r_group_substitution: bool = False,
                         image_table_relationship: str = "both",
                         has_structure_drawings: bool = False,
                         structure_drawing_labels: list = None,
                         output_dir: str = None,
                         columns_metadata: Dict[str, Any] = None,
                         progress_callback=None,
                         advanced_structure: Dict[str, Any] = None,
                         current_item: Dict[str, Any] = None,
                         is_scope_table: bool = False) -> Dict[str, Any]:
        """
        Orchestrates the extraction process:
        1. Abbreviation Extraction -> Context for SMILES mapping
        2. Image Detection -> Decide Table vs Scheme path
        3. Piper Intelligence Agent -> Perform core extraction
        4. Piper Formatting -> Sanitize for UI display
        """
        # Internal intelligence module
        from miner.intelligence.engine import RunPiperIntelligence

        _log.info(f"🎼 Orchestrator started processing... (MLLM: {use_mllm})")
        print(f"DEBUG: Orchestrator.process_document called with:")
        print(f"  - scheme_image_path: {scheme_image_path}")
        print(f"  - text_content length: {len(text_content) if text_content else 0}")
        print(f"  - output_dir: {output_dir}")

        # Abbreviations are now sourced from compound_name_map (advanced structure parser).
        # The old abbreviation_agent LLM call has been removed.
        abbreviations = {}

        # --- GLOBAL LABEL REGISTRY INITIALIZATION ---
        global_label_mappings = {} # label -> {smiles, name, source_id}
        all_registries = []

        from context import load_config as _load_cfg
        _cfg_global = _load_cfg()
        _build_all_global = _cfg_global.get("build_global_labels", False)
        _force_visualheist_global = _cfg_global.get("force_enable_visualheist_fallback", False)

        # When build_global_labels is False (default): only process figures explicitly referenced by this table's column label_map_source_id values.
        # This skips substrate scope figures, synthesis schemes, mechanistic figures — anything not directly linked to the optimization table's abbreviation columns.
        # When build_global_labels is True: process every labelled figure in the document.
        referenced_source_ids = set()
        if not _build_all_global:
            if columns_metadata:
                for col_meta in columns_metadata.values():
                    src_id = col_meta.get("label_map_source_id")
                    if src_id:
                        referenced_source_ids.add(src_id)
            # Always include the current item itself — its defined_labels are drawn in the scheme image passed to this call and must be resolved even when no column explicitly references this item's id.
            if current_item and current_item.get("id"):
                referenced_source_ids.add(current_item["id"])

        structure_to_use = advanced_structure or {}
        # LLM-extracted {label -> compound_name} from the advanced structure parser.
        # Used as authoritative name source for PubChem lookup in all resolve() calls.
        _compound_name_map: Dict[str, str] = structure_to_use.get("compound_name_map") or {}
        for item in structure_to_use.get("data_items", []):
            item_id = item.get("id", "")

            if not _build_all_global:
                if item_id not in referenced_source_ids:
                    _diag.write(f"  [GlobalRegistry] Skipping '{item_id}' — not referenced by this table's columns")
                    continue

            labels = list(item.get("defined_labels", []))
            # Always merge structure_drawing_labels for included figures
            drawing_labels = item.get("structure_drawing_labels", [])
            for dl in drawing_labels:
                if dl not in labels:
                    labels.append(dl)

            # Drop sentence-like labels (LLM hallucinated descriptions, not compound names)
            _placeholder_phrases = (
                "see legend", "see figure", "shown in image", "shown in table",
                "see scheme", "as shown", "see gallery", "in the image",
                "photocatalyst shown", "structure shown", "compound shown",
            )
            labels = [
                lbl for lbl in labels
                if lbl
                and len(lbl) <= 80
                and not any(ph in lbl.lower() for ph in _placeholder_phrases)
                and lbl.count(" ") <= 6  # real compound names are rarely > 6 words
            ]

            image_path = item.get("reaction_scheme_ref")
            if labels and image_path:
                # Resolve relative image_path to absolute.
                if output_dir and not os.path.isabs(image_path):
                    candidates = [
                        os.path.join(output_dir, image_path),
                        os.path.join(output_dir, "processed", image_path),
                        os.path.join(output_dir, os.path.basename(image_path)),
                    ]
                    resolved = False
                    for candidate in candidates:
                        if os.path.exists(candidate):
                            image_path = os.path.abspath(candidate)
                            resolved = True
                            break
                    if not resolved:
                        _diag.write(f"  [GlobalRegistry] ⚠️  Could not resolve image for labels {labels}: {image_path}")
                        _diag.write(f"    Tried: {candidates}")
                        # Hallucination guard: the advanced structure parser sometimes invents an image path (wrong number or hash suffix). Attempt a fallback by scanning the artifacts directory for images that:
                        #   1. Are NOT the table's own scheme image (wrong image)
                        #   2. Appear in the article markdown after the table (structure gallery)
                        import re as _re_img
                        _arts_dir = os.path.join(output_dir, os.path.dirname(image_path))
                        _scheme_basename = os.path.basename(scheme_image_path) if scheme_image_path else ""
                        if os.path.isdir(_arts_dir) and full_text_content:
                            # Find all image links in the markdown that appear AFTER the table rows
                            _all_md_imgs = _re_img.findall(r'!\[.*?\]\(([^)]+\.png)\)', full_text_content)
                            _post_table_imgs = []
                            # Locate the last |..| table row in the markdown
                            _last_pipe = max((full_text_content.rfind(r) for r in ['|---', '| ---'] if r in full_text_content), default=-1)
                            if _last_pipe > 0:
                                _after_md = full_text_content[_last_pipe:]
                                _post_table_imgs = _re_img.findall(r'!\[.*?\]\(([^)]+\.png)\)', _after_md)
                            for _md_path in _post_table_imgs:
                                _fname = os.path.basename(_md_path)
                                if _fname == _scheme_basename:
                                    continue   # skip the scheme image
                                _full = os.path.abspath(os.path.join(output_dir, _md_path))
                                if os.path.exists(_full):
                                    image_path = _full
                                    resolved = True
                                    print(f"  [GlobalRegistry] Hallucination fallback: resolved to {_fname}")
                                    _diag.write(f"  [GlobalRegistry] Hallucination fallback → {_fname}")
                                    break
                else:
                    resolved = os.path.exists(image_path)
                    if not resolved:
                        _diag.write(f"  [GlobalRegistry] ⚠️  Abs path not found for labels {labels}: {image_path}")

                if not resolved:
                    print(f"  [GlobalRegistry] ❌ Skipping labels {labels} — image not found: "
                          f"{os.path.basename(image_path)}")
                    continue

                # VisualHeist override: prefer lowest-page visualheist file over Docling artifact
                if _force_visualheist_global and item_id:
                    _vh_path = _pick_visualheist_for_item(item_id, output_dir)
                    if _vh_path:
                        _diag.write(f"  [GlobalRegistry] VisualHeist override: {os.path.basename(image_path)} → {os.path.basename(_vh_path)}")
                        image_path = _vh_path

                _diag.write(f"  [GlobalRegistry] labels={labels} → {os.path.basename(image_path)}")
                all_registries.append((labels, image_path, item.get("id")))

        if all_registries:
            if progress_callback:
                progress_callback("🏷️ Building Global Document Label Registry...")
            for labels, img, sid in all_registries:
                try:
                    res = self.label_resolver_agent.resolve(
                        labels=labels, source_type="image", source_content=img, source_id=sid,
                        paper_text=full_text_content or "",
                        name_map=_compound_name_map
                    )
                    if res and res.get("mappings"):
                        for m in res["mappings"]:
                            lbl = m.get("label")
                            if lbl:
                                global_label_mappings[lbl] = m
                except Exception as e:
                    _log.warning(f"Failed to resolve global labels for {sid}: {e}")

        # --- REACTION PARTICIPANT RESOLUTION ---
        # Resolve reactant/product labels drawn in THIS table's own reaction scheme.
        # These take priority over the global registry because the local scheme is the authoritative source for those specific compounds.
        participant_smiles_map = {}  # label -> mapping dict, used for reactant/product SMILES
        if current_item:
            participants = current_item.get("reaction_scheme_participants", [])
            # Prefer the merged advanced-item id (e.g. "Figure 3a") over heading so the LLM label resolver knows which panel to focus on
            sid = current_item.get("id") or current_item.get("heading", "current_item")
            # Use scheme_image_path (resolved by document_processor from the actual extraction image), NOT current_item["reaction_scheme_ref"] which the structure parser can mis-assign to the wrong sub-figure image.
            participant_scheme_image = scheme_image_path
            print(f"  [ParticipantResolver] labels={participants} image={participant_scheme_image} exists={os.path.exists(participant_scheme_image) if participant_scheme_image else 'N/A'}")
            if participants and participant_scheme_image and os.path.exists(participant_scheme_image):
                if progress_callback:
                    progress_callback(f"⚗️ Resolving reaction participants for {sid}...")
                try:
                    res = self.label_resolver_agent.resolve(
                        labels=participants, source_type="image",
                        source_content=participant_scheme_image, source_id=sid,
                        paper_text=full_text_content or "",
                        name_map=_compound_name_map
                    )
                    if res and res.get("mappings"):
                        for m in res["mappings"]:
                            lbl = m.get("label")
                            # Store even when SMILES is missing — the label itself is needed for participant injection (product label shows in UI even if structure couldn't be resolved).
                            if lbl and (m.get("smiles") or m.get("name")):
                                participant_smiles_map[lbl] = m
                                _log.info(f"Participant registry: '{lbl}' resolved from {sid}")
                            elif lbl:
                                _log.warning(f"Participant '{lbl}' resolved but has no smiles — skipping map entry")
                except Exception as e:
                    _log.warning(f"Failed to resolve reaction participants for {sid}: {e}")
            elif participants and not participant_scheme_image:
                _log.warning(f"No scheme_image_path available for participant resolution of {sid}")
            elif participants and participant_scheme_image:
                _log.warning(f"Participant scheme image does not exist: {participant_scheme_image}")

        reaction_data = {}
        if use_mllm and scheme_image_path:
            print(f"Processing with Piper Intelligence agents... Image: {scheme_image_path}")
            
            # Detect Tabular Data in Image
            is_tabular_image = False
            if progress_callback:
                progress_callback("🔍 Checking image for tabular data...")
            
            try:
                detection_result = self.table_detector.run(scheme_image_path)
                is_tabular_image = detection_result.get("is_table", False)
                is_junk = detection_result.get("is_junk", False)
                
                print(f"DEBUG: Image detection: Table={is_tabular_image}, Junk={is_junk} ({detection_result.get('reasoning')})")
                
                if is_junk:
                    print(f"    -> 🚫 Image identified as junk/logo. Skipping extraction.")
                    if progress_callback:
                        progress_callback("🚫 Skipping irrelevant image (logo/icon)...")
                    return {"reactions": [], "abbreviations": abbreviations, "is_junk": True}

            except Exception as e:
                _log.error(f"Detector failed: {e}")

            print("Routing to unified Piper Intelligence Engine...")
            if progress_callback:
                progress_callback("🧠 Orchestrating Chemical Intelligence...")
            
            try:
                # Extract article body text around this table so the engine can find fixed conditions (solvent, T, time) stated in prose, not columns.
                # Strategy: find the table heading, skip past the markdown table rows (pipe-delimited lines and image embeds), then take the following discussion text where authors describe their conditions.
                _paper_context = None
                if full_text_content and current_item:
                    import re as _re_ctx
                    _tbl_id = current_item.get("id", "")
                    _ctx_m = _re_ctx.search(
                        _re_ctx.escape(_tbl_id), full_text_content, _re_ctx.IGNORECASE
                    )
                    if _ctx_m:
                        # Collect text BEFORE the table (brief caption context)
                        _pre = full_text_content[max(0, _ctx_m.start() - 300): _ctx_m.start()].strip()
                        # Find the discussion text that follows this table.
                        # Structure: "Table N. Caption...\n\n![Image](...)\n\n|...|...|"
                        # We need to skip the caption, image embed, and all pipe rows, then take the prose text where authors describe conditions.
                        _post_start = _ctx_m.end()
                        _after = full_text_content[_post_start:]
                        _lines = _after.splitlines()
                        # Phase A: find where the pipe-table starts
                        _table_start_line = None
                        for _li, _line in enumerate(_lines):
                            if _line.strip().startswith('|'):
                                _table_start_line = _li
                                break
                        # Phase B: skip from table start through all table/image/blank lines
                        _skip_until = _table_start_line if _table_start_line is not None else 0
                        if _table_start_line is not None:
                            for _li in range(_table_start_line, len(_lines)):
                                _s = _lines[_li].strip()
                                if (_s.startswith('|') or _s.startswith('![') or
                                        _s == '' or _re_ctx.match(r'^[-|:\s]+$', _s)):
                                    _skip_until = _li + 1
                                else:
                                    break
                        _discussion = '\n'.join(_lines[_skip_until:_skip_until + 80]).strip()
                        _paper_context = (_pre + '\n\n' + _discussion)[:4000].strip()
                        if _paper_context:
                            print(f"  [PaperContext] Extracted {len(_paper_context)} chars "
                                  f"around '{_tbl_id}' (pre={len(_pre)}, discussion first 200: "
                                  f"{_discussion[:200]!r})")

                # Use the unified standalone engine instead of manual agent selection
                raw_reaction_data = RunPiperIntelligence(
                    scheme_image_path,
                    text_content=text_content,
                    has_arrow_parameters=has_arrow_parameters,
                    arrow_mapping=arrow_mapping,
                    has_r_group_substitution=has_r_group_substitution,
                    footnote_dependencies=footnote_dependencies,
                    image_table_relationship=image_table_relationship,
                    has_structure_drawings=has_structure_drawings,
                    structure_drawing_labels=structure_drawing_labels or [],
                    progress_callback=progress_callback,
                    paper_context=_paper_context,
                    is_scope_table=is_scope_table,
                    expected_row_count=current_item.get("expected_row_count") if current_item else None,
                )
                self._save_intelligence_raw(raw_reaction_data, output_dir, "piper_intelligence")
                reaction_data = raw_reaction_data
                
                # Post-process for Piper UI
                if reaction_data and "reactions" in reaction_data:
                    specific_reactions = []
                    raw_rxn_list = reaction_data["reactions"]
                    
                    if isinstance(raw_rxn_list, dict):
                        for rxn_id, rxn in raw_rxn_list.items():
                            cleaned_rxn = {
                                "entry_id": str(rxn_id),
                                "reactants": self._clean_component(rxn.get("reactants", [])),
                                "products": self._clean_component(rxn.get("products", [])),
                                "conditions": self._clean_conditions(rxn.get("conditions", [])),
                                "additional_info": rxn.get("additional_info", [])
                            }
                            specific_reactions.append(cleaned_rxn)
                    elif isinstance(raw_rxn_list, list):
                        import re as _re_eid

                        def _parse_entry_id(rxn_id: str) -> str:
                            """Extract a clean numeric entry ID from an LLM-generated reaction_id.

                            Handles formats like:
                              "entry_1"          -> "1"
                              "row_3"            -> "3"
                              "Table1_2"         -> "2"   (trailing pure-digit segment)
                              "Entry_6_Cs,CO3"   -> "6"   (first number after prefix strip)
                              "Entry_24 6"       -> "24"  (first number after prefix strip)
                              "5"                -> "5"
                            Returns "" if no number is found.
                            """
                            rxn_id = rxn_id.strip()
                            # 1. Strip leading "entry_" / "row_" prefix
                            stripped = _re_eid.sub(r'^(?i:(entry|row)[_\s]*)', '', rxn_id)
                            # 2. If the trailing "_"-segment is purely numeric, use it e.g. "Table1_2" -> tail="2" ✅  "Entry_6_CO3" -> tail="CO3" ❌
                            if '_' in stripped:
                                tail = stripped.rsplit('_', 1)[-1].strip()
                                if tail.isdigit():
                                    return tail
                            # 3. First contiguous integer in the remaining string
                            m = _re_eid.search(r'\d+', stripped)
                            return m.group(0) if m else ""

                        # First pass: extract candidate IDs (skip malformed non-dict elements)
                        candidates = []
                        for rxn in raw_rxn_list:
                            if not isinstance(rxn, dict):
                                candidates.append("")
                                continue
                            rxn_id = str(rxn.get("reaction_id", ""))
                            candidates.append(_parse_entry_id(rxn_id))

                        # If IDs are non-unique (LLM used garbled OCR row label for all) fall back to sequential numbering so parity check works correctly.
                        unique_ids = [c for c in candidates if c and c != "0"]
                        use_sequential = len(set(unique_ids)) < len(unique_ids) or not unique_ids

                        for seq_i, rxn in enumerate(raw_rxn_list, start=1):
                            if not isinstance(rxn, dict):
                                _log.warning(f"Orchestrator: skipping malformed reaction element at index {seq_i}: {type(rxn).__name__}")
                                continue
                            entry_id = str(seq_i) if use_sequential else candidates[seq_i - 1]
                            if not entry_id or entry_id == "0":
                                continue
                            cleaned_rxn = {
                                "entry_id": entry_id,
                                "reactants": self._clean_component(rxn.get("reactants", [])),
                                "products": self._clean_component(rxn.get("products", [])),
                                "conditions": self._clean_conditions(rxn.get("conditions", [])),
                                "additional_info": rxn.get("additional_info", [])
                            }
                            specific_reactions.append(cleaned_rxn)
                    
                    #  Enrich entries with parsed markdown table data 
                    parsed_table: Dict[str, Any] = {}   # kept in scope for footnote extraction below
                    if text_content and "|" in text_content:
                        from miner.table_parser import parse_markdown_table, normalize_header
                        import re as _re
                        _tname_m = _re.search(r'(Table\s+\d+)', text_content, _re.IGNORECASE)
                        _tbl_name = _tname_m.group(1) if _tname_m else "Table"
                        parsed_table = parse_markdown_table(text_content, _tbl_name)
                        # Validate headers: at least one must map to a known chemistry key.
                        # If the PDF extractor lost the header row, the first data row becomes the "header" and produces garbage keys — skip enrichment in that case and let the intelligence-engine output stand on its own.
                        _known_roles = {
                            "entry", "catalyst", "deviation", "solvent", "additive",
                            "time", "yield_percent", "base", "ligand", "temperature",
                            "light_source", "equivalents", "selectivity_ratio",
                            "selectivity_ee", "substituent",
                        }
                        _parsed_keys = {normalize_header(h) for h in parsed_table.get("headers", [])} if parsed_table else set()
                        _table_has_valid_headers = bool(_parsed_keys & _known_roles)
                        if parsed_table and parsed_table.get("entries") and _table_has_valid_headers:
                            specific_reactions = self._enrich_from_table_data(
                                specific_reactions, parsed_table,
                                columns_metadata=columns_metadata,
                                participant_smiles_map=participant_smiles_map,
                                compound_name_map=_compound_name_map,
                            )
                        elif parsed_table and parsed_table.get("entries") and not _table_has_valid_headers:
                            print(f"[Orchestrator] Skipping table enrichment — no recognisable "
                                  f"chemistry headers found (got: {list(_parsed_keys)[:5]}). "
                                  f"PDF table likely lost its header row.")

                    #  Clean up stale additional_info from LLM copy-paste errors ──
                    specific_reactions = self._clean_additional_info(specific_reactions)

                    # ── RESOLVE PLACEHOLDER CONDITIONS FROM PAPER TEXT ──────────────────
                    # If the intelligence engine left any fixed condition as a verbatim placeholder (e.g. "solvent", "T", "time", "light"), call the
                    # FixedConditionExtractorAgent on the surrounding article text to resolve the actual values.  The agent is skipped entirely when no placeholders remain — avoids an unnecessary LLM call.
                    def _is_placeholder(text: str, role: str) -> bool:
                        t = (text or "").strip().lower()
                        r = (role or "").strip().lower()
                        # Exact single-word placeholders the LLM copies from the arrow
                        if t in {"solvent", "t", "time", "light", "temperature"}:
                            return True
                        # "(not specified)" suffix written by the LLM when it can't resolve
                        if "not specified" in t:
                            return True
                        # Text is literally the role name or starts with it (e.g. "solvent (X)")
                        if t == r or t.startswith(r + " ") or t.startswith(r + "("):
                            return True
                        return False

                    _unresolved_roles = {
                        cond["role"]
                        for rxn in specific_reactions
                        for cond in rxn.get("conditions", [])
                        if _is_placeholder(cond.get("text", ""), cond.get("role", ""))
                    }
                    # Also call when footnote markers exist even if all conditions are resolved — footnote overrides need applying regardless.
                    _md_footnotes = parsed_table.get("footnotes", {}) if parsed_table else {}
                    _has_footnote_markers = any(
                        _re_module.search(r'\[[a-z]\]', c.get("text","") or "")
                        for rxn in specific_reactions
                        for c in rxn.get("conditions",[])
                    ) or bool(_md_footnotes)

                    if (_unresolved_roles or _has_footnote_markers) and _paper_context:
                        if _unresolved_roles:
                            print(f"  [FixedConditionExtractor] Unresolved placeholders: "
                                  f"{_unresolved_roles} — calling agent.")
                        if _has_footnote_markers:
                            print(f"  [FixedConditionExtractor] Footnote markers detected — "
                                  f"calling agent for per-entry overrides.")
                        agent_result = self._resolve_placeholders_from_text(
                            _paper_context,
                            markdown_footnotes=_md_footnotes,
                            table_markdown=text_content,
                        )
                        fixed = agent_result.get("fixed_conditions", {})
                        overrides = agent_result.get("per_entry_overrides", {})

                        # Apply fixed conditions to placeholder slots
                        if fixed:
                            for rxn in specific_reactions:
                                for cond in rxn.get("conditions", []):
                                    if (_is_placeholder(cond.get("text", ""), cond.get("role", ""))
                                            and cond["role"] in fixed):
                                        resolved = fixed[cond["role"]]
                                        cond["text"] = resolved.get("text", cond["text"])
                                        if resolved.get("quantity"):
                                            cond["quantity"] = resolved["quantity"]
                                        if resolved.get("unit"):
                                            cond["unit"] = resolved["unit"]

                        # Apply per-entry overrides (replace or remove conditions)
                        if overrides:
                            for rxn in specific_reactions:
                                eid = str(rxn.get("entry_id", ""))
                                if eid not in overrides:
                                    continue
                                for role, new_val in overrides[eid].items():
                                    if new_val is None:
                                        # Subtractive footnote — remove this condition
                                        rxn["conditions"] = [
                                            c for c in rxn.get("conditions", [])
                                            if c.get("role") != role
                                        ]
                                        _log.info(f"Entry {eid}: removed condition '{role}' "
                                                  f"(subtractive footnote)")
                                    else:
                                        # Replace or add the condition
                                        replaced = False
                                        for cond in rxn.get("conditions", []):
                                            if cond.get("role") == role:
                                                cond.clear()
                                                cond.update({"role": role, **new_val})
                                                replaced = True
                                                break
                                        if not replaced:
                                            rxn.setdefault("conditions", []).append(
                                                {"role": role, **new_val}
                                            )
                                        _log.info(f"Entry {eid}: overrode '{role}' → {new_val}")

                    # ── VARIATION FROM STANDARD: inject baseline conditions ──────────────
                    # When the table uses a "Deviation from standard conditions" column, each row only records the ONE parameter that changed.  The standard (baseline) conditions are written above the reaction arrow.  Extract them once from the scheme image and merge into every entry so each reaction has the full condition set.
                    is_variation_table = columns_metadata and any(
                        v.get("type") == "variation_from_standard"
                        for v in columns_metadata.values()
                    )
                    if is_variation_table and scheme_image_path and os.path.exists(scheme_image_path):
                        baseline, _ptcp_qtys = self._extract_baseline_conditions(
                            scheme_image_path, full_text_content or ""
                        )
                        # Apply participant quantities from scheme image to all reactions
                        if _ptcp_qtys:
                            for rxn in specific_reactions:
                                for _pq in _ptcp_qtys:
                                    _pq_label = _pq.get("label", "").strip()
                                    _pq_side  = _pq.get("side", "").lower()
                                    _pq_qty   = _pq.get("quantity")
                                    _pq_unit  = _pq.get("unit", "")
                                    if not _pq_label or _pq_qty is None:
                                        continue
                                    _lists = []
                                    if _pq_side == "reactant":
                                        _lists = [rxn.get("reactants", [])]
                                    elif _pq_side == "product":
                                        _lists = [rxn.get("products", [])]
                                    else:
                                        _lists = [rxn.get("reactants", []), rxn.get("products", [])]
                                    for _lst in _lists:
                                        for _ptcp in _lst:
                                            if _ptcp.get("text") == _pq_label:
                                                _ptcp.setdefault("quantity", _pq_qty)
                                                _ptcp.setdefault("unit", _pq_unit)
                        if baseline:
                            _log.info(f"Variation table: injecting {len(baseline)} baseline conditions")
                            for rxn in specific_reactions:
                                existing_roles = {c.get("role") for c in rxn.get("conditions", [])}
                                # Detect roles explicitly negated by the deviation text (e.g. "No photocatalyst" >> do not re-inject photocatalyst)
                                deviation_text = " ".join(
                                    c.get("text", "").lower()
                                    for c in rxn.get("conditions", [])
                                    if c.get("role") == "deviation_from_standard"
                                )
                                _negation_map = {
                                    "photocatalyst": ["no photocatalyst", "without photocatalyst", "no pc"],
                                    "catalyst":      ["no catalyst", "without catalyst"],
                                    "ligand":        ["no ligand", "without ligand"],
                                    "metal_salt":    ["no chromium", "no cr", "without chromium"],
                                    "light":         ["no light", "dark", "without light"],
                                }
                                negated_roles = {
                                    role for role, phrases in _negation_map.items()
                                    if any(p in deviation_text for p in phrases)
                                }
                                # Extract labels from "instead of X" clauses so we can skip injecting the baseline condition that was replaced.
                                # Also check _pending_variation_text which holds the full deviation string before it's split into separate fields.
                                _full_dev_text = deviation_text
                                _pv = rxn.get("_pending_variation_text", "")
                                if _pv:
                                    _full_dev_text = (_full_dev_text + " " + _pv).lower()
                                import re as _re_bl
                                _instead_of_labels = {
                                    _re_bl.sub(r'[.,;)\s]+$', '', _m.group(1)).lower()
                                    for _m in _re_bl.finditer(
                                        r'instead\s+of\s+(\S+)', _full_dev_text, _re_bl.IGNORECASE
                                    )
                                }
                                for bc in baseline:
                                    bc_role = bc.get("role")
                                    if bc_role in existing_roles:
                                        continue
                                    if bc_role in negated_roles:
                                        continue
                                    if bc.get("text", "").lower() in _instead_of_labels:
                                        continue
                                    rxn.setdefault("conditions", []).append(bc)

                    # Backfill compound names when the column shows drawn structures
                    # (Docling leaves those cells empty; LLM writes generic placeholders)
                    if columns_metadata and specific_reactions:
                        specific_reactions = self._gallery_entry_mapper(
                            specific_reactions=specific_reactions,
                            columns_metadata=columns_metadata,
                            structure_to_use=structure_to_use,
                            scheme_image_path=scheme_image_path,
                            progress_callback=progress_callback,
                        )

                    # ── RESOLVE LABEL MAPPINGS (Photocatalysts, Ligands, etc.) ──
                    if columns_metadata:
                        if progress_callback:
                            progress_callback("🏷️ Resolving chemical labels and abbreviations...")
                        
                        # Identify candidates for resolution
                        for col_name, meta in columns_metadata.items():
                            if meta.get("value_is_abbreviation") and meta.get("label_map"):
                                source_id = meta.get("label_map_source_id", "Figure/Text")
                                _log.info(f"Resolution candidate: Column '{col_name}' from {source_id}")
                                
                                # Gather unique labels in this column.
                                # Skip any label already resolved via the global registry — those came from the dedicated gallery figure (OCSR quality) and must not be overwritten by inline resolver which uses the scheme image only.
                                _col_role = self._col_name_to_role(col_name)
                                labels_to_resolve = set()
                                for rxn in specific_reactions:
                                    for cond in rxn.get("conditions", []):
                                        cond_role = cond.get("role", "")
                                        if cond_role == col_name or cond_role == _col_role or cond_role == "reagent":
                                            # Filter out standard reagents
                                            val = cond.get("text", "")
                                            if self._is_standard_reagent(val):
                                                continue
                                            # Skip placeholder descriptions the intelligence engine wrote when it couldn't identify drawn structures in cells
                                            _inline_placeholders = (
                                                "see legend", "see figure", "shown in image",
                                                "shown in table", "see scheme", "as shown",
                                                "see gallery", "in the image", "shown in",
                                                "see image", "refer to", "structure in",
                                            )
                                            _inline_generic_terms = {
                                                "catalyst", "photocatalyst", "ligand", "base",
                                                "solvent", "additive", "reagent", "oxidant",
                                                "reductant", "unknown", "n/a", "nd",
                                            }
                                            if (any(ph in val.lower() for ph in _inline_placeholders)
                                                    or val.strip().lower() in _inline_generic_terms):
                                                _log.debug(f"InlineResolver: skipping placeholder '{val}'")
                                                continue
                                            if val in global_label_mappings:
                                                _log.info(f"InlineResolver: skipping '{val}' — already in global registry")
                                                continue
                                            # Try composed key: col_name + " " + val e.g. column "Photocatalyst", value "2" -> "Photocatalyst 2"
                                            # Maps bare numeric values back to the full key registered when the gallery figure was resolved, avoiding a rogue resolver re-run against the wrong (current-table) image.
                                            composed_key = f"{col_name} {val}".strip()
                                            if composed_key in global_label_mappings:
                                                _log.info(f"InlineResolver: '{val}' → composed '{composed_key}' in global registry — applying directly")
                                                _diag.write(f"  [InlineResolver] '{val}' → composed key '{composed_key}' resolved from global registry")
                                                mapping = global_label_mappings[composed_key]
                                                cond["resolved_name"] = mapping.get("name")
                                                cond["resolved_smiles"] = mapping.get("smiles")
                                                cond["smiles_source"] = mapping.get("smiles_source", "label_registry")
                                                cond["mapping_source"] = mapping.get("found_in")
                                                for _k in ("smiles_pubchem", "smiles_opsin", "smiles_llm", "smiles_ocsr_raw"):
                                                    if mapping.get(_k):
                                                        cond[_k] = mapping[_k]
                                                continue
                                            labels_to_resolve.add(val)
                                
                                if labels_to_resolve:
                                    # Use the *referenced figure's* image, not the current table's scheme image. When label_map_source_id points to a different figure (e.g. "Fig. 1" holding photocatalyst drawings), running OCSR on the current table's substrate scheme produces completely wrong structures.
                                    inline_image_path = scheme_image_path  # default fallback
                                    for _di in structure_to_use.get("data_items", []):
                                        if _di.get("id") == source_id:
                                            _ref = _di.get("reaction_scheme_ref", "")
                                            if _ref and output_dir and not os.path.isabs(_ref):
                                                for _cand in [
                                                    os.path.join(output_dir, _ref),
                                                    os.path.join(output_dir, "processed", _ref),
                                                    os.path.join(output_dir, os.path.basename(_ref)),
                                                ]:
                                                    if os.path.exists(_cand):
                                                        inline_image_path = os.path.abspath(_cand)
                                                        break
                                            elif _ref and os.path.isabs(_ref) and os.path.exists(_ref):
                                                inline_image_path = _ref
                                            break
                                    # VisualHeist override: prefer lowest-page visualheist file
                                    if _force_visualheist_global and source_id:
                                        _vh_inline = _pick_visualheist_for_item(source_id, output_dir)
                                        if _vh_inline:
                                            _diag.write(f"  [InlineResolver] VisualHeist override: "
                                                        f"{os.path.basename(inline_image_path) if inline_image_path else 'None'} → {os.path.basename(_vh_inline)}")
                                            inline_image_path = _vh_inline
                                    _diag.write(f"  [InlineResolver] Column '{col_name}' labels {list(labels_to_resolve)} "
                                                f"from {os.path.basename(inline_image_path) if inline_image_path else 'None'}")
                                    resolution_data = self.label_resolver_agent.resolve(
                                        labels=list(labels_to_resolve),
                                        source_type="image",
                                        source_content=inline_image_path,
                                        source_id=source_id,
                                        progress_callback=progress_callback,
                                        paper_text=full_text_content or "",
                                        name_map=_compound_name_map
                                    )
                                    
                                    if resolution_data and resolution_data.get("mappings"):
                                        specific_reactions = self._apply_resolved_mappings(
                                            specific_reactions, 
                                            resolution_data["mappings"], 
                                            col_name,
                                            source_id
                                        )

                    reaction_data["specific_reactions"] = specific_reactions

                    # --- GLOBAL RESOLUTION ENRICHMENT ---
                    if specific_reactions:
                        if progress_callback:
                            progress_callback("🧪 Matching conditions to Global Label Registry...")
                        
                        # Sort labels by length descending so longer labels match before shorter substrings
                        sorted_labels = sorted(global_label_mappings.keys(), key=len, reverse=True)
                        
                        # Set of common units/suffixes to avoid matching numeric labels against
                        # (e.g. "1 h", "1 eq", "33 %")
                        unit_blacklist = {
                            "h", "h.", "eq", "equiv", "equiv.", "mol%", "deg",
                            "℃", "°c", "c", "m", "mmol", "mg", "nm",
                            "%", "percent", "yield",
                        }
                        # Roles that should never receive label resolution
                        _no_resolve_roles = {"yield", "additional_info", "time", "temperature", "scale", "reactor"}

                        for rxn in specific_reactions:
                            for cond in rxn.get("conditions", []):
                                # Skip conditions that are not chemical identity fields
                                if cond.get("role", "").lower() in _no_resolve_roles:
                                    continue
                                text = cond.get("text", "")
                                # Allow the global registry to override llm_vision results —
                                # the registry used the dedicated gallery figure (OCSR quality) and
                                # should take precedence over LLM-hallucinated SMILES.
                                existing_source = cond.get("smiles_source", "")
                                already_resolved = cond.get("resolved_smiles") and existing_source != "llm_vision"
                                if not already_resolved:
                                    import re as _re_cond
                                    # Build a set of label positions that appear after deviation
                                    # phrases ("instead of X", "without X", "no X", "in the
                                    # absence of X") — those are the STANDARD being replaced,
                                    # not the compound being used in this entry.
                                    _deviation_phrases = r'(?:instead\s+of|without|in\s+the\s+absence\s+of|omitting|no\s+)\s*'
                                    _excluded_positions: set = set()
                                    for _dm in _re_cond.finditer(_deviation_phrases + r'(\S+)', text, _re_cond.IGNORECASE):
                                        _excluded_positions.add(_dm.start(1))

                                    # Greedy check for any global label in the text
                                    for label in sorted_labels:
                                        mapping = global_label_mappings[label]

                                        boundary_chars = r'[A-Za-z0-9.\-\'\,\(\)\[\]]'
                                        pattern = f'(?<!{boundary_chars})' + _re_cond.escape(label) + f'(?!{boundary_chars})'
                                        match = _re_cond.search(pattern, text)

                                        # Skip if this match falls in a deviation phrase context
                                        if match and match.start() in _excluded_positions:
                                            continue
                                        
                                        if match:
                                            # Special handling for short/numeric labels: 
                                            # 1. Don't match if it's likely a quantity (e.g. "1 h", "1 equiv")
                                            if label.isdigit() or len(label) <= 2:
                                                after_text = text[match.end():].lstrip().lower()
                                                is_unit = False
                                                for unit in unit_blacklist:
                                                    if after_text.startswith(unit):
                                                        is_unit = True
                                                        break
                                                if is_unit:
                                                    continue
                                                    
                                            cond["resolved_name"] = mapping.get("name")
                                            cond["resolved_smiles"] = mapping.get("smiles")
                                            cond["smiles_source"] = mapping.get("smiles_source", "label_registry")
                                            cond["mapping_source"] = mapping.get("found_in")
                                            for _k in ("smiles_pubchem", "smiles_opsin", "smiles_llm", "smiles_ocsr_raw"):
                                                if mapping.get(_k):
                                                    cond[_k] = mapping[_k]
                                            _log.info(f"Global Registry: Matched '{label}' in condition '{text}'")
                                            break

                        # ── RANGE CONDITION EXPANSION ──────────────────────────────────────
                        # Expand range-notation conditions (e.g. "NHC-2-4", "2b-2f") into
                        # one condition per label. Batch all unique texts into one LLM call,
                        # then apply lookups from global registry / participant map.
                        _all_cond_texts = list({
                            cond.get("text", "")
                            for rxn in specific_reactions
                            for cond in rxn.get("conditions", [])
                            if cond.get("text", "")
                        })
                        _range_map = self._expand_range_labels_llm(_all_cond_texts)
                        # Only keep entries the LLM flagged as actual ranges
                        _range_map = {k: v for k, v in _range_map.items() if v}

                        if _range_map:
                            for rxn in specific_reactions:
                                expanded_conds = []
                                for cond in rxn.get("conditions", []):
                                    sub_labels = _range_map.get(cond.get("text", ""))
                                    if not sub_labels:
                                        expanded_conds.append(cond)
                                        continue
                                    print(f"  [RangeExpand] '{cond['text']}' → {sub_labels}")
                                    for lbl in sub_labels:
                                        new_cond = {k: v for k, v in cond.items()}
                                        new_cond["text"] = lbl
                                        new_cond.pop("resolved_smiles", None)
                                        new_cond.pop("resolved_name", None)
                                        new_cond.pop("smiles_source", None)
                                        new_cond.pop("mapping_source", None)
                                        # Try sources in priority order
                                        if lbl in global_label_mappings:
                                            _m = global_label_mappings[lbl]
                                            new_cond["resolved_name"] = _m.get("name")
                                            new_cond["resolved_smiles"] = _m.get("smiles")
                                            new_cond["smiles_source"] = _m.get("smiles_source", "label_registry")
                                            new_cond["mapping_source"] = _m.get("found_in")
                                        elif lbl in (participant_smiles_map or {}):
                                            _m = participant_smiles_map[lbl]
                                            new_cond["resolved_smiles"] = _m.get("smiles")
                                            new_cond["smiles_source"] = _m.get("smiles_source", "ocsr_coref")
                                        elif lbl in (_compound_name_map or {}):
                                            new_cond["resolved_name"] = _compound_name_map[lbl]
                                        expanded_conds.append(new_cond)
                                rxn["conditions"] = expanded_conds

                        # ── PUBCHEM ENRICHMENT FOR CONDITIONS ──
                        # Conditions that have a chemical role but no resolved SMILES
                        # (LLM didn't generate one, global registry didn't match) get
                        # a direct PubChem name lookup using the cleaned condition text.
                        _chem_roles = {
                            "photocatalyst", "catalyst", "metal_salt", "ligand",
                            "base", "reagent", "solvent", "additive", "oxidant", "reductant",
                        }
                        for rxn in specific_reactions:
                            for cond in rxn.get("conditions", []):
                                if cond.get("role", "").lower() not in _chem_roles:
                                    continue
                                if cond.get("resolved_smiles") or cond.get("smiles_source") == "pubchem_lookup":
                                    continue
                                raw_text = cond.get("text", "")
                                if not raw_text:
                                    continue
                                import re as _re_pc
                                # Strip quantity suffixes: "(1.5 mol%)", "(5 mol%)", "0.05 M", etc.
                                cleaned = _re_pc.sub(
                                    r'\s*[\(\[]\s*[\d\.]+\s*(?:mol%|%|M|mM|equiv?\.?|mmol|g|mg|μL|mL)\s*[\)\]]',
                                    '', raw_text
                                ).strip()
                                # Strip trailing parenthetical shorthand:
                                # "Tetrahydrofuran (THF)" -> full name FIRST, abbreviation LAST.
                                # Abbreviations (DMA, THF) can match unrelated PubChem entries;
                                # full chemical names are far more reliable.
                                # Normalise two common OCR/formatting artifacts before
                                # building the candidate list:
                                #
                                # 1. Middle-dot complex notation: "NiCl2·glyme"
                                #    PubChem prefers "NiCl2(glyme)" or the full IUPAC name.
                                #    glyme = 1,2-dimethoxyethane (DME), so also try the
                                #    spelled-out form.
                                _GLYME_MAP = {
                                    "glyme": "1,2-dimethoxyethane",
                                    "dme":   "1,2-dimethoxyethane",
                                    "thf":   "tetrahydrofuran",
                                    "dmso":  "dimethyl sulfoxide",
                                    "dma":   "N,N-dimethylacetamide",
                                    "dmf":   "N,N-dimethylformamide",
                                }
                                if "·" in cleaned or "•" in cleaned:
                                    _parts = _re_pc.split(r'[·•]', cleaned, maxsplit=1)
                                    if len(_parts) == 2:
                                        _metal, _lig = _parts[0].strip(), _parts[1].strip()
                                        cleaned = f"{_metal}({_lig})"            # NiCl2(glyme)
                                        _lig_exp = _GLYME_MAP.get(_lig.lower())
                                        if _lig_exp:
                                            candidates_extra = [
                                                f"{_metal}({_lig_exp})",          # NiCl2(1,2-dimethoxyethane)
                                                f"{_metal} {_lig}",               # NiCl2 glyme
                                                f"{_metal} {_lig_exp}",           # NiCl2 1,2-dimethoxyethane
                                            ]
                                        else:
                                            candidates_extra = [f"{_metal} {_lig}"]
                                    else:
                                        candidates_extra = []
                                else:
                                    candidates_extra = []

                                # 2. OCR subscript spacing: "Cs 2 CO 3" → "Cs2CO3"
                                #    Subscript digits often appear as " 2 " / " 3 " after
                                #    OCR strips the subscript formatting.
                                _ocr_clean = _re_pc.sub(r'(?<=[A-Za-z])\s+(\d+)(?=\s*[A-Za-z\d])', r'\1', cleaned)
                                _ocr_clean = _re_pc.sub(r'(?<=\d)\s+(\d)', r'\1', _ocr_clean).strip()

                                candidates = [cleaned]
                                if _ocr_clean != cleaned:
                                    candidates.append(_ocr_clean)
                                candidates.extend(candidates_extra)
                                m = _re_pc.search(r'\(([^)]+)\)\s*$', cleaned)
                                if m:
                                    full_without_paren = cleaned[:cleaned.rfind('(')].strip()  # "Tetrahydrofuran"
                                    abbrev = m.group(1).strip()                                 # "THF"
                                    if full_without_paren:
                                        candidates.append(full_without_paren)   # full name second
                                    candidates.append(abbrev)                   # abbreviation last
                                # Also try compound_name_map: exact key match, then
                                # suffix match for decorated variants.
                                # Strip trailing "(abbrev)" before suffix matching so
                                # "Dimethylacetamide (DMA)" correctly matches key "DMA".
                                _name_map_hit = False
                                if _compound_name_map:
                                    cleaned_bare = _re_pc.sub(r'\s*\([^)]*\)\s*$', '', cleaned).strip()
                                    full_name = (_compound_name_map.get(cleaned)
                                                 or _compound_name_map.get(cleaned_bare))
                                    if full_name:
                                        candidates.insert(0, full_name)
                                        _name_map_hit = True
                                    else:
                                        # Suffix match for keys like "dMeObpy" in "4,4'-dMeObpy"
                                        for k, v in _compound_name_map.items():
                                            bare_lower = cleaned_bare.lower()
                                            if bare_lower.endswith(k.lower()) or cleaned.lower().endswith(k.lower()):
                                                candidates.insert(0, v)
                                                _name_map_hit = True
                                                break
                                # When the compound_name_map provided a full authoritative name, skip article-specific codes (e.g. "OPC-1", "Cat3") as PubChem candidates — they return unrelated compounds and pollute results.
                                # An article code is: short (≤12 chars), starts with letters, ends with a digit or hyphen+digit.
                                import re as _re_code
                                _is_article_code = lambda s: bool(
                                    s and len(s) <= 12
                                    and _re_code.match(r'^[A-Za-z]', s)
                                    and _re_code.search(r'[-\s]?\d+$', s)
                                )
                                from miner.agents.label_resolver_agent import LabelResolverAgent
                                # When name_map provided an authoritative chemical name, use the full name-resolution chain (PubChem → OPSIN → LLM) via the resolver's cache — this is far more reliable than a bare PubChem call and reuses results already computed during global registry builds. Also validates organometallic SMILES (no Ir/Ru → reject).
                                _authoritative_name = candidates[0] if (_name_map_hit and candidates) else None
                                if _authoritative_name:
                                    _res_smi, _res_src = self.label_resolver_agent._resolve_name_to_smiles(_authoritative_name)
                                    # Reject if name implies a transition metal but SMILES has none
                                    import re as _re_metal
                                    _name_implies_metal = bool(_re_metal.search(
                                        r'\b(Ir|Ru|Pd|Pt|Rh|Ni|Cu|Fe|Co|Mn|Os|Au)\b', _authoritative_name
                                    ))
                                    if _res_smi and _name_implies_metal:
                                        _smi_has_metal = bool(_re_metal.search(
                                            r'\[(Ir|Ru|Pd|Pt|Rh|Ni|Cu|Fe|Co|Mn|Os|Au)', _res_smi
                                        ))
                                        if not _smi_has_metal:
                                            _log.warning(
                                                f"PubChem enrichment: '{_authoritative_name}' implies metal "
                                                f"but SMILES has none — discarding"
                                            )
                                            _res_smi = ""
                                    if _res_smi:
                                        cond["resolved_smiles"] = _res_smi
                                        cond["resolved_name"] = _authoritative_name
                                        cond["smiles_source"] = _res_src
                                        _log.info(f"NameMap+Chain condition: '{raw_text}' via '{_authoritative_name}' ({_res_src}) → {_res_smi[:50]}")
                                        continue  # next condition — skip raw PubChem loop

                                for cand in candidates:
                                    if not cand or len(cand) <= 1:
                                        continue
                                    if _name_map_hit and _is_article_code(cand):
                                        _log.debug(f"PubChem: skipping article code '{cand}' — name_map provided full name")
                                        continue
                                    smi = LabelResolverAgent._pubchem_name_to_smiles_simple(cand)
                                    # Reject organometallic false-positives: if name implies a metal but SMILES has none, the PubChem hit matched the wrong compound.
                                    if smi:
                                        import re as _re_m2
                                        _n_metal = bool(_re_m2.search(r'\b(Ir|Ru|Pd|Pt|Rh|Ni|Cu|Fe|Co|Mn)\b', cand))
                                        _s_metal = bool(_re_m2.search(r'\[(Ir|Ru|Pd|Pt|Rh|Ni|Cu|Fe|Co|Mn)', smi))
                                        if _n_metal and not _s_metal:
                                            _log.warning(f"PubChem: '{cand}' implies metal but SMILES has none — skipping")
                                            continue
                                        cond["resolved_smiles"] = smi
                                        cond["smiles_source"] = "pubchem_lookup"
                                        _log.info(f"PubChem condition: '{raw_text}' via '{cand}' → {smi}")
                                        break

                        # ── RESOLVE SMILES FOR REACTANTS AND PRODUCTS ──
                        # participant_smiles_map ALWAYS overrides — it is the authoritative source for the specific reactant/product drawn in this table's reaction scheme.
                        # global_label_mappings only fills when SMILES is still absent.
                        import re as _re2
                        for rxn in specific_reactions:
                            for mol_list in (rxn.get("reactants", []), rxn.get("products", [])):
                                for mol in mol_list:
                                    text = (mol.get("text") or mol.get("label") or "").strip()
                                    if not text:
                                        # No label — try SMILES-based matching as fallback.
                                        # The LLM sometimes omits the label but provides the correct SMILES.
                                        # If it matches a participant or global registry entry, inherit that source.
                                        mol_smiles = (mol.get("smiles_canonical") or mol.get("smiles", "")).strip()
                                        smiles_matched = False
                                        if mol_smiles:
                                            for key, mapping in participant_smiles_map.items():
                                                if mapping.get("smiles") == mol_smiles:
                                                    mol["smiles_source"] = mapping.get("smiles_source", "ocsr_coref")
                                                    mol["resolved_from"] = mapping.get("found_in")
                                                    smiles_matched = True
                                                    break
                                            if not smiles_matched:
                                                for lbl, mapping in global_label_mappings.items():
                                                    if mapping.get("smiles") == mol_smiles:
                                                        mol["smiles_source"] = mapping.get("smiles_source", "label_registry")
                                                        mol["resolved_from"] = mapping.get("found_in")
                                                        smiles_matched = True
                                                        break
                                        if not smiles_matched and mol.get("smiles") and not mol.get("smiles_source"):
                                            mol["smiles_source"] = "llm_vision"
                                        continue
                                    m = _re2.match(r'([A-Za-z0-9]+)', text)
                                    label_tok = m.group(1) if m else text
                                    base_tok = _re2.sub(r'[a-z]+$', '', label_tok)
                                    candidates = [label_tok]
                                    if base_tok and base_tok != label_tok:
                                        candidates.append(base_tok)

                                    # 1. Participant map: always check and always override.
                                    #    RxnScribe may have set wrong SMILES from the table image;
                                    #    the participant map uses the same image but with targeted
                                    #    label resolution, so it wins unconditionally.
                                    participant_resolved = False
                                    if participant_smiles_map:
                                        for candidate in candidates:
                                            for key, mapping in participant_smiles_map.items():
                                                if key.lower() == candidate.lower() and mapping.get("smiles"):
                                                    mol["smiles"] = mapping["smiles"]
                                                    mol["resolved_from"] = mapping.get("found_in")
                                                    mol["smiles_source"] = mapping.get("smiles_source", "ocsr_coref")
                                                    for _k in ("smiles_ocsr_raw", "smiles_ocsr_graph",
                                                               "smiles_pubchem", "smiles_opsin", "smiles_llm"):
                                                        if mapping.get(_k):
                                                            mol[_k] = mapping[_k]
                                                    _log.info(f"Participant Registry: '{label_tok}' → SMILES (overrides any prior value)")
                                                    participant_resolved = True
                                                    break
                                            if participant_resolved:
                                                break

                                    if participant_resolved:
                                        continue  # done — participant map is authoritative

                                    # 1b. If this label is declared in reaction_scheme_participants
                                    # but the participant map couldn't resolve it, the existing
                                    # SMILES (likely from RxnScribe scanning the whole multi-panel image) is unreliable — clear it so it doesn't persist.
                                    declared_participants = set(
                                        p.lower() for p in
                                        (current_item.get("reaction_scheme_participants", []) if current_item else [])
                                    )
                                    if declared_participants:
                                        for candidate in candidates:
                                            if candidate.lower() in declared_participants:
                                                if mol.get("smiles_source") in ("ocsr_rxnscribe", "ocsr_coref"):
                                                    _log.info(
                                                        f"Clearing RxnScribe SMILES for participant '{candidate}' "
                                                        f"— participant map resolution failed, SMILES is from full image"
                                                    )
                                                    mol["smiles"] = ""
                                                    mol["smiles_source"] = ""
                                                break

                                    # 2. Global label registry: only fill when still empty.
                                    if mol.get("smiles"):
                                        continue
                                    for candidate in candidates:
                                        for label in sorted_labels:
                                            if label.lower() == candidate.lower():
                                                mapping = global_label_mappings[label]
                                                if mapping.get("smiles"):
                                                    mol["smiles"] = mapping["smiles"]
                                                    mol["resolved_from"] = mapping.get("found_in")
                                                    mol["smiles_source"] = mapping.get("smiles_source", "label_registry")
                                                    _log.info(f"Global Registry: '{label_tok}' → SMILES (via '{label}')")
                                                break
                                        else:
                                            continue
                                        break

                        # ─ Secondary participant resolution ────────────────
                        # Collect reactant/product labels still unresolved (smiles exists
                        # but no smiles_source yet — came from LLM with no OCSR match so far).
                        # Run them through the label resolver using the scheme image so they
                        # get a chance at OCSR tiers before falling back to llm_vision.
                        _missing_labels = set()
                        for _rxn in specific_reactions:
                            for _mol in _rxn.get("reactants", []) + _rxn.get("products", []):
                                if _mol.get("smiles") and not _mol.get("smiles_source"):
                                    _lbl = (_mol.get("label") or _mol.get("text") or "").strip()
                                    if _lbl and _lbl not in participant_smiles_map:
                                        _missing_labels.add(_lbl)

                        if _missing_labels and scheme_image_path and os.path.exists(scheme_image_path):
                            try:
                                _sec_res = self.label_resolver_agent.resolve(
                                    labels=list(_missing_labels),
                                    source_type="image",
                                    source_content=scheme_image_path,
                                    source_id=current_item.get("id", "scheme") if current_item else "scheme",
                                    paper_text=full_text_content or "",
                                    name_map=_compound_name_map,
                                )
                                if _sec_res and _sec_res.get("mappings"):
                                    for _m in _sec_res["mappings"]:
                                        _lbl = _m.get("label")
                                        if _lbl and _m.get("smiles"):
                                            participant_smiles_map[_lbl] = _m
                            except Exception as _e:
                                _log.warning(f"Secondary participant resolution failed: {_e}")

                        # Apply expanded participant_smiles_map to still-unresolved molecules.
                        # Store OCSR in smiles_ocsr, validate, then select best for smiles.
                        try:
                            from rdkit import Chem as _RdChem
                        except ImportError:
                            _RdChem = None

                        for _rxn in specific_reactions:
                            for _mol in _rxn.get("reactants", []) + _rxn.get("products", []):
                                if _mol.get("smiles") and not _mol.get("smiles_source"):
                                    _lbl = (_mol.get("label") or _mol.get("text") or "").strip()
                                    if _lbl and _lbl in participant_smiles_map:
                                        _mapping = participant_smiles_map[_lbl]
                                        _ocsr_smi = _mapping.get("smiles", "")
                                        if _ocsr_smi:
                                            _mol["smiles_ocsr"] = _ocsr_smi
                                            _ocsr_valid = False
                                            if _RdChem:
                                                try:
                                                    _ocsr_valid = _RdChem.MolFromSmiles(_ocsr_smi) is not None
                                                except Exception:
                                                    pass
                                            if _ocsr_valid:
                                                _mol["smiles"] = _ocsr_smi
                                                _mol["smiles_source"] = _mapping.get("smiles_source", "ocsr_coref")

                        # Tag any mol that has SMILES but no source — came directly from the LLM
                        for rxn in specific_reactions:
                            for mol in rxn.get("reactants", []) + rxn.get("products", []):
                                if mol.get("smiles") and not mol.get("smiles_source"):
                                    mol["smiles_source"] = "llm_vision"

                        # ── INJECT PARTICIPANTS into entries that have no reactants/products ──
                        # Optimization tables share the same substrate→product across all rows.
                        # If the LLM didn't extract them (put everything in conditions), inject them from the participant map so every entry has the correct structures.
                        if participant_smiles_map and current_item:
                            all_participants = current_item.get("reaction_scheme_participants", [])
                            if all_participants:
                                # Priority 1: use explicit labels from advanced structure when the LLM already separated reactants from products.
                                reactant_labels = current_item.get("reactant_labels", [])
                                product_labels  = current_item.get("product_labels",  [])

                                # Priority 2: RxnScribe arrow-direction classification.
                                if not reactant_labels and not product_labels:
                                    if scheme_image_path and os.path.exists(scheme_image_path):
                                        reactant_labels, product_labels = self._classify_from_rxnscribe(
                                            scheme_image_path, all_participants
                                        )
                                # Priority 3: naming-heuristic fallback
                                if not reactant_labels and not product_labels:
                                    reactant_labels, product_labels = _classify_participant_roles(
                                        all_participants
                                    )
                                inject_reactants = [
                                    _make_participant_mol(lbl, participant_smiles_map[lbl])
                                    for lbl in reactant_labels if lbl in participant_smiles_map
                                ]
                                inject_products = [
                                    _make_participant_mol(lbl, participant_smiles_map[lbl])
                                    for lbl in product_labels if lbl in participant_smiles_map
                                ]
                                if inject_reactants or inject_products:
                                    for rxn in specific_reactions:
                                        # Participant map is authoritative for optimization tables — but do NOT overwrite entries where the deviation column already substituted a different reactant (e.g. "2b instead of 2a").
                                        if inject_reactants and not rxn.get("_reactant_substitution_applied"):
                                            rxn["reactants"] = inject_reactants
                                        if inject_products:
                                            rxn["products"] = inject_products
                                    _log.info(
                                        f"Participant override: all {len(specific_reactions)} entries set "
                                        f"reactants={[m['text'] for m in inject_reactants]}, "
                                        f"products={[m['text'] for m in inject_products]}"
                                    )

                        # ── REACTANT / PRODUCT DEDUPLICATION ─────────────────────────────
                        # If a compound label appears in both reactants and products
                        # (e.g. RxnScribe over-classified it), remove it from reactants — products classification is the authoritative output.
                        for rxn in specific_reactions:
                            prod_texts = {p.get("text") for p in rxn.get("products", []) if p.get("text")}
                            if prod_texts:
                                rxn["reactants"] = [r for r in rxn.get("reactants", []) if r.get("text") not in prod_texts]

                        # ── POST-INJECTION REACTANT SUBSTITUTION ──────────────────────────
                        # Deviation rows (e.g. "2b instead of 2a") are applied here, AFTER participant injection has given every entry its base reactants.
                        for rxn in specific_reactions:
                            pending = rxn.pop("_pending_variation_text", None)
                            if not pending:
                                continue
                            orig_texts = [r.get("text", "") for r in rxn.get("reactants", [])]
                            new_reactants = self._apply_reactant_substitution(
                                rxn.get("reactants", []),
                                pending,
                                participant_smiles_map or {},
                                _compound_name_map or {},
                            )
                            new_texts = [r.get("text", "") for r in new_reactants]
                            if new_texts != orig_texts:
                                rxn["reactants"] = new_reactants
                                print(
                                    f"  [ReactantSub] Entry {rxn.get('entry_id')}: "
                                    f"{orig_texts} → {new_texts}"
                                )

            except Exception as e:
                import traceback
                _log.error(f"Intelligence engine failed: {e}")
                print(f"  ❌ [Orchestrator] Intelligence engine exception: {e}")
                print(traceback.format_exc())
                reaction_data = {"error": str(e)}

        else:
            # Text-only or fallback mode
            print("Processing in text-only mode...")
            if progress_callback:
                progress_callback("📝 Running Text Reaction Agent...")
            try:
                reaction_data = self.text_reaction_agent.run(text_content)
            except Exception as e:
                _log.error(f"Text reaction agent failed: {e}")
                reaction_data = {"error": str(e)}

        if isinstance(reaction_data, dict):
            reaction_data["abbreviations"] = abbreviations
        
        _log.info("✅ Orchestrator finished processing.")
        
        # --- FINAL SMILES SANITIZATION ---
        from miner.utils.smiles_sanitizer import sanitize_smiles
        if reaction_data and "specific_reactions" in reaction_data:
            for rxn in reaction_data["specific_reactions"]:
                rxn.pop("_reactant_substitution_applied", None)
                rxn.pop("_pending_variation_text", None)
                for r in rxn.get("reactants", []):
                    if "smiles" in r: r["smiles"] = sanitize_smiles(r["smiles"])
                for p in rxn.get("products", []):
                    if "smiles" in p: p["smiles"] = sanitize_smiles(p["smiles"])
                for c in rxn.get("conditions", []):
                    if "resolved_smiles" in c: c["resolved_smiles"] = sanitize_smiles(c["resolved_smiles"])
        
        return reaction_data

    # ── Abbreviation substitution ──────────────────────────────────────────
    @staticmethod
    def _apply_abbreviations(
        # Not used
        entries: List[Dict[str, Any]],
        abbreviations: Dict[str, str],
    ) -> List[Dict[str, Any]]:
        """Expand abbreviations in all text fields of conditions and additional_info.

        For each condition/additional_info item that contains an abbreviation,
        appends the full form in parentheses, e.g. "DCM" → "DCM (dichloromethane)".
        Only annotates once per field to avoid duplication on re-runs.
        """
        import re
        if not abbreviations:
            return entries

        # Sort by length (longest first) to avoid partial matches
        sorted_abbrs = sorted(abbreviations.items(), key=lambda x: len(x[0]), reverse=True)

        def _expand(text: str) -> str:
            if not isinstance(text, str):
                return text
            for abbr, full_name in sorted_abbrs:
                if abbr in text and f"({full_name})" not in text:
                    # Use lookahead/lookbehind to avoid matching inside
                    # hyphenated terms (e.g. "PC" inside "PC-II")
                    pattern = re.compile(
                        r'(?<![A-Za-z0-9\-])' + re.escape(abbr) + r'(?![A-Za-z0-9\-])'
                    )
                    if pattern.search(text):
                        text = pattern.sub(f"{abbr} ({full_name})", text, count=1)
            return text

        for entry in entries:
            # Expand in conditions
            for cond in entry.get("conditions", []):
                if isinstance(cond, dict) and "text" in cond:
                    cond["text"] = _expand(cond["text"])

            # Expand in additional_info
            for info in entry.get("additional_info", []):
                if isinstance(info, dict) and "text" in info:
                    info["text"] = _expand(info["text"])

        return entries

    def _extract_baseline_conditions(self, scheme_image_path: str, paper_text: str) -> list:
        """
        Extract the standard (baseline) reaction conditions from the scheme image
        and surrounding paper text for variation-from-standard tables.

        The reaction arrow above the table lists the fixed parameters that apply
        to ALL rows. This method uses the LLM to read those conditions from the
        image and returns them as a list of condition dicts (same format as
        conditions in specific_reactions entries).
        """
        try:
            import base64, json as _json
            from openai import OpenAI
            from context import load_config, get_openrouter_url
            from dotenv import load_dotenv
            import os
            load_dotenv()

            cfg = load_config()
            model = cfg.get("model", {}).get("agents", {}).get(
                "label_resolver_model", "google/gemini-3-flash-preview"
            )
            api_key = (os.environ.get("OPENROUTER_API_KEY")
                       or os.environ.get("API_KEY")
                       or os.environ.get("OPENAI_API_KEY"))
            client = OpenAI(api_key=api_key, base_url=get_openrouter_url())

            with open(scheme_image_path, "rb") as fh:
                b64 = base64.b64encode(fh.read()).decode()

            prompt = (
                "This image shows a reaction scheme above an optimization table. "
                "The reaction arrow has conditions written above and below it — these "
                "are the STANDARD (baseline) conditions that apply to all table entries "
                "unless a row explicitly deviates.\n\n"
                "Also look at the reactant and product structures drawn on each side of the arrow. "
                "If a quantity (e.g. '0.1 mmol', '2.0 equiv', '1.0 equiv') is written next to a "
                "reactant or product label, extract it.\n\n"
                "Return JSON with two keys:\n"
                "1. \"baseline_conditions\": ["
                "{\"role\": \"photocatalyst|catalyst|ligand|base|solvent|reagent|light|time|temperature|scale\", "
                "\"text\": \"exact text from image\", \"quantity\": numeric_value, \"unit\": \"mmol|equiv|mol%|...\"}]\n"
                "2. \"participant_quantities\": ["
                "{\"label\": \"exact label from image (e.g. 1a, substrate)\", "
                "\"side\": \"reactant|product\", "
                "\"quantity\": numeric_value, \"unit\": \"mmol|equiv|mg|...\"}]\n\n"
                "Only extract values clearly visible in the image. "
                "Do not invent or infer. If no quantities are shown next to participants, return participant_quantities as []."
            )

            resp = client.chat.completions.create(
                model=model, temperature=0,
                messages=[
                    {"role": "system", "content": "You are a helpful chemistry assistant that outputs JSON."},
                    {"role": "user", "content": [
                        {"type": "text", "text": prompt},
                        {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{b64}"}},
                    ]}
                ],
                response_format={"type": "json_object"}
            )
            from miner.token_tracker import tracker
            if hasattr(resp, "usage") and resp.usage:
                tracker.record("BaselineConditionAgent", model,
                               resp.usage.prompt_tokens or 0,
                               resp.usage.completion_tokens or 0)
            data = _json.loads(resp.choices[0].message.content or "{}")
            return data.get("baseline_conditions", []), data.get("participant_quantities", [])
        except Exception as e:
            _log.warning(f"Failed to extract baseline conditions: {e}")
            return [], []

    def _classify_from_rxnscribe(
        self, scheme_image_path: str, participants: list
    ) -> tuple:
        """
        Use RxnScribe's arrow-direction understanding to classify participant
        labels as reactants or products.

        Strategy:
          1. Run extract_full_reaction_schema on the scheme image (RxnScribe +
             ChemIEToolkit coref) to get reaction_prediction and molecule_coref.
          2. Build smiles → label map from molecule_coref.
          3. For each molecule in reaction_prediction[0]["reactants"/"products"],
             find its label via smiles match, then bbox-overlap as fallback.
          4. Return (reactant_labels, product_labels) filtered to known participants.
             Returns ([], []) if classification fails — caller falls back to heuristic.
        """
        try:
            from miner.intelligence.agents.reaction_agent import extract_full_reaction_schema
            result = extract_full_reaction_schema(scheme_image_path)
        except Exception as e:
            _log.warning(f"[RxnScribe classify] Failed to run: {e}")
            return [], []

        prediction = result.get("reaction_prediction", [])
        coref      = result.get("molecule_coref", [])
        if not prediction:
            return [], []

        rxn = prediction[0]  # primary reaction in the scheme

        # ── Build smiles → [label, ...] map from ChemIEToolkit coref ──────────
        smiles_to_labels: Dict[str, list] = {}
        for item in coref:
            s = item.get("smiles", "")
            texts = item.get("texts", [])
            if s and texts:
                smiles_to_labels.setdefault(s, []).extend(texts)
                # Also index by canonical SMILES to improve matching
                try:
                    from rdkit import Chem
                    mol_obj = Chem.MolFromSmiles(s)
                    if mol_obj:
                        canon = Chem.MolToSmiles(mol_obj)
                        smiles_to_labels.setdefault(canon, []).extend(texts)
                except Exception:
                    pass

        # ── Helper: convert polygon coords to bbox [x1,y1,x2,y2] ─────────────
        def _coords_to_bbox(coords):
            xs = [p[0] for p in coords]
            ys = [p[1] for p in coords]
            return [min(xs), min(ys), max(xs), max(ys)]

        def _bbox_iou(b1, b2) -> float:
            ix1, iy1 = max(b1[0], b2[0]), max(b1[1], b2[1])
            ix2, iy2 = min(b1[2], b2[2]), min(b1[3], b2[3])
            iw, ih   = max(0, ix2 - ix1), max(0, iy2 - iy1)
            inter    = iw * ih
            if inter == 0:
                return 0.0
            a1 = max(1e-9, (b1[2]-b1[0]) * (b1[3]-b1[1]))
            a2 = max(1e-9, (b2[2]-b2[0]) * (b2[3]-b2[1]))
            return inter / (a1 + a2 - inter)

        # Pre-compute coref bboxes for overlap matching
        coref_bboxes = []
        for item in coref:
            raw = item.get("bbox")
            if raw:
                if isinstance(raw, (list, tuple)) and len(raw) == 4:
                    coref_bboxes.append((list(raw), item))

        def _find_labels_for_mol(mol: dict) -> list:
            """Try smiles match then bbox overlap to find label(s) for a mol."""
            s = mol.get("smiles", "")
            # 1. Direct SMILES match — works even for wildcard SMILES since both
            # reaction_prediction and molecule_coref come from the same OCSR model.
            if s and s in smiles_to_labels:
                return smiles_to_labels[s]
            # Also try canonical form
            if s:
                try:
                    from rdkit import Chem
                    mol_obj = Chem.MolFromSmiles(s)
                    if mol_obj:
                        canon = Chem.MolToSmiles(mol_obj)
                        if canon in smiles_to_labels:
                            return smiles_to_labels[canon]
                except Exception:
                    pass
            # 2. Bbox overlap using coords polygon
            coords = mol.get("coords")
            if coords:
                mol_bbox = _coords_to_bbox(coords)
                best_iou, best_item = 0.0, None
                for cb, citem in coref_bboxes:
                    iou = _bbox_iou(mol_bbox, cb)
                    if iou > best_iou:
                        best_iou, best_item = iou, citem
                if best_iou > 0.3 and best_item:
                    return best_item.get("texts", [])
            return []

        # ── Classify ─────────────────────────────────────────────────────────
        reactant_labels: list = []
        product_labels: list  = []
        participant_set = set(participants)

        for mol in rxn.get("reactants", []):
            for lbl in _find_labels_for_mol(mol):
                if lbl in participant_set and lbl not in reactant_labels:
                    reactant_labels.append(lbl)

        for mol in rxn.get("products", []):
            for lbl in _find_labels_for_mol(mol):
                if lbl in participant_set and lbl not in product_labels:
                    product_labels.append(lbl)

        if not reactant_labels and not product_labels:
            _log.info("[RxnScribe classify] No labels matched — returning empty (caller will fallback)")
            return [], []

        # If every participant appears in BOTH lists the SMILES-based matching was
        # ambiguous (e.g. the substrate and product share the same OCSR scaffold).
        # Fall back to the naming heuristic so we don't inject garbage.
        overlap = set(reactant_labels) & set(product_labels)
        if overlap and overlap >= set(p for p in participants if _re_module.search(r'[a-zA-Z]', p)):
            _log.info(
                f"[RxnScribe classify] All participants matched both sides (ambiguous SMILES) "
                f"— returning empty (caller will fallback to heuristic)"
            )
            print(f"  [RxnScribeClassify] ambiguous — all labels matched both sides, deferring to heuristic")
            return [], []

        # Any participant not matched to either list is almost certainly a reactant/reagent
        # (products always appear after the arrow; RxnScribe's SMILES mismatch is more
        # likely to affect reagents/conditions than main products).
        # Skip pure-numeric labels (e.g. "4" = catalyst) — those are reagents/catalysts
        # already handled as conditions, not structural participants to inject.
        classified = set(reactant_labels) | set(product_labels)
        unmatched  = [
            l for l in participants
            if l not in classified
            and _re_module.search(r'[a-zA-Z]', l)  # must have at least one letter
        ]
        if unmatched:
            reactant_labels.extend(unmatched)
            _log.info(f"[RxnScribe classify] Unmatched → reactants: {unmatched}")

        _log.info(f"[RxnScribe classify] reactants={reactant_labels} products={product_labels}")
        print(f"  [RxnScribeClassify] reactants={reactant_labels} products={product_labels}")
        return reactant_labels, product_labels

    def _clean_additional_info(self, entries: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """Removes stale or repeated additional_info items.

        1. Strips any item whose text explicitly references a different entry number
           (LLM copy-paste from Entry 1 into other entries).
        2. Removes items that are identical across ALL entries — these are table-level
           notes (e.g. "Yields determined by NMR") that add no per-entry information.
        """
        import re
        if not entries:
            return entries

        # Pass 1: drop items that reference a different entry id
        for entry in entries:
            eid = str(entry.get("entry_id", "")).strip()
            cleaned = []
            for info in entry.get("additional_info", []):
                text = info if isinstance(info, str) else info.get("text", "")
                m = re.match(r"entry\s+(\d+)\s*:", text, re.IGNORECASE)
                if m and m.group(1) != eid:
                    _log.debug(f"Dropping stale additional_info for entry {eid}: '{text[:60]}'")
                    continue
                cleaned.append(info)
            entry["additional_info"] = cleaned

        # Pass 2: find items that appear identically in every entry and remove them —
        # they are table-level footnotes repeated by the LLM, not per-entry details.
        if len(entries) > 1:
            def _text(info):
                return info if isinstance(info, str) else info.get("text", "")
            all_sets = [set(_text(i) for i in e.get("additional_info", [])) for e in entries]
            universal = all_sets[0].intersection(*all_sets[1:])
            if universal:
                for entry in entries:
                    entry["additional_info"] = [
                        i for i in entry.get("additional_info", [])
                        if _text(i) not in universal
                    ]
                _log.info(f"Removed {len(universal)} table-level additional_info item(s) "
                          f"repeated across all entries.")
        return entries

    def _resolve_placeholders_from_text(
        self,
        text: str,
        markdown_footnotes: Dict[str, str] = None,
        table_markdown: str = None,
    ) -> Dict[str, Any]:
        """Delegate to FixedConditionExtractorAgent to resolve placeholder conditions
        and per-entry footnote overrides."""
        return self.fixed_condition_extractor.run(
            paper_context=text,
            markdown_footnotes=markdown_footnotes,
            table_markdown=table_markdown,
        )

    def _gallery_entry_mapper(
        self,
        specific_reactions: List[Dict],
        columns_metadata: Dict[str, Any],
        structure_to_use: Dict[str, Any],
        scheme_image_path: str,
        progress_callback=None,
    ) -> List[Dict]:
        """
        Backfills empty condition text for columns whose cells contain drawn
        molecular structures instead of text (Docling leaves those cells blank).

        Trigger: a value_is_abbreviation column has empty text in more than half
        its entries AND its label_map_source_id points to a Gallery item in
        advanced_structure.

        The LLM reads the gallery image and maps each entry number to a compound
        name.  The defined_labels list from the Gallery item is passed as a
        constrained hint so the LLM cannot invent names not present in the paper.
        """
        import base64 as _b64
        import json as _json
        import os as _os

        data_items = {item.get("id", ""): item for item in structure_to_use.get("data_items", [])}

        for col_name, meta in columns_metadata.items():
            if not meta.get("value_is_abbreviation"):
                continue
            gallery_id = meta.get("label_map_source_id", "")
            if not gallery_id:
                continue
            gallery_item = data_items.get(gallery_id)
            if not gallery_item or gallery_item.get("type") != "Gallery":
                continue

            # Collect entry ids with empty/placeholder condition text for this column.
            # "Effectively empty" means blank OR a generic description that the
            # intelligence engine wrote because it saw structures in the cell image
            # but couldn't identify them individually.
            col_role = self._col_name_to_role(col_name)
            _cell_placeholder_phrases = (
                "see legend", "see figure", "shown in image", "shown in table",
                "see scheme", "as shown", "see gallery", "in the image",
                "photocatalyst shown", "structure shown", "compound shown",
                "see image", "refer to", "structure in",
            )

            # Single-word generic role names the LLM echoes when it cannot
            # identify individual drawn structures in table cells.
            _generic_role_terms = {
                "catalyst", "photocatalyst", "ligand", "base", "solvent",
                "additive", "reagent", "oxidant", "reductant", "acid",
                "unknown", "n/a", "nd", "-", "—",
            }

            def _is_effectively_empty(text: str) -> bool:
                t = (text or "").strip()
                if not t:
                    return True
                tl = t.lower()
                if tl in _generic_role_terms:
                    return True
                return any(ph in tl for ph in _cell_placeholder_phrases)

            import re as _re_eid_pre
            def _entry_num_pre(rid: str) -> str:
                m = _re_eid_pre.search(r'(\d+)$', str(rid).strip())
                return m.group(1) if m else str(rid).strip()

            empty_entries, total_entries = [], []
            for rxn in specific_reactions:
                entry_id = rxn.get("reaction_id", rxn.get("entry_id", ""))
                num = _entry_num_pre(str(entry_id))
                for cond in rxn.get("conditions", []):
                    if cond.get("role") in (col_name, col_role):
                        total_entries.append(num)
                        if _is_effectively_empty(cond.get("text", "")):
                            empty_entries.append(num)

            # Only fire when the majority of cells are empty
            if not empty_entries or len(empty_entries) < len(total_entries) * 0.5:
                continue

            # Resolve the gallery image path
            gallery_ref = gallery_item.get("reaction_scheme_ref", "")
            gallery_image = None
            if gallery_ref and _os.path.exists(gallery_ref):
                gallery_image = gallery_ref
            elif gallery_ref and scheme_image_path:
                candidate = _os.path.join(_os.path.dirname(scheme_image_path), _os.path.basename(gallery_ref))
                if _os.path.exists(candidate):
                    gallery_image = candidate

            if not gallery_image:
                _log.warning(f"[GalleryMapper] Gallery image not found for '{gallery_id}': {gallery_ref}")
                continue

            defined_labels = gallery_item.get("defined_labels", [])
            _log.info(f"[GalleryMapper] Triggering for column '{col_name}' — "
                      f"{len(empty_entries)} empty entries, gallery='{gallery_id}'")
            if progress_callback:
                progress_callback(f"🗂️ Mapping gallery structures to table entries...")

            # Build LLM prompt
            try:
                with open(gallery_image, "rb") as f:
                    img_b64 = _b64.b64encode(f.read()).decode()
            except Exception as e:
                _log.warning(f"[GalleryMapper] Could not read gallery image: {e}")
                continue

            # Only use defined_labels as a hint when they look like real compound
            # names (not placeholder descriptions the LLM hallucinated).
            _bad_phrases = (
                "see legend", "see figure", "shown in image", "shown in table",
                "see scheme", "as shown", "see gallery", "in the image",
                "photocatalyst shown", "structure shown", "compound shown",
            )
            clean_labels = [
                lbl for lbl in defined_labels
                if lbl and len(lbl) <= 80
                and not any(ph in lbl.lower() for ph in _bad_phrases)
                and lbl.count(" ") <= 6
            ]
            hint = (f"\nKNOWN COMPOUND NAMES in this gallery (use exactly as written):\n"
                    + "\n".join(f"  - {n}" for n in clean_labels)) if clean_labels else ""

            prompt = (
                "You are a chemistry expert. The image shows a gallery of molecular "
                "structures used as the photocatalyst/reagent column of a reaction table. "
                "Each drawn structure has a name label below or beside it, and they appear "
                "in the same sequential order as the table entries.\n"
                f"{hint}\n\n"
                f"TABLE ENTRIES WITH MISSING COMPOUND NAMES: {empty_entries}\n\n"
                "For each entry number listed above, identify the compound name from the "
                "gallery image that corresponds to that entry's position in the sequence. "
                "Use the compound names exactly as they appear in the image or the known "
                "names list above — do not invent or paraphrase.\n\n"
                'Return JSON: {"mapping": {"<entry_id>": "<compound_name>", ...}}\n'
                "Only include entries from the provided list. If you cannot determine the "
                "compound for an entry, omit it."
            )

            try:
                from miner.agents.base_agent import BaseAgent
                from context import load_config as _lc
                _cfg = _lc()
                model = _cfg.get("model", {}).get("agents", {}).get(
                    "label_resolver_model", "google/gemini-3-flash-preview")

                agent = BaseAgent(model=model)
                messages = [
                    {"role": "system", "content": "You are a helpful chemistry assistant that outputs JSON."},
                    {"role": "user", "content": [
                        {"type": "text", "text": prompt},
                        {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{img_b64}"}},
                    ]},
                ]
                raw = agent._call_llm(messages, response_format={"type": "json_object"})
                mapping: Dict[str, str] = (agent._parse_json(raw) or {}).get("mapping", {})
            except Exception as e:
                _log.warning(f"[GalleryMapper] LLM call failed: {e}")
                continue

            if not mapping:
                _log.warning(f"[GalleryMapper] LLM returned empty mapping for '{gallery_id}'")
                continue

            # Backfill empty condition text for each matched entry.
            # reaction_id from the intelligence engine uses formats like "Table1_1" or "Entry 1". The LLM returns plain entry numbers ("1", "2", ...).
            # Build a normalised lookup: extract the trailing integer from reaction_id.
            import re as _re_eid
            def _entry_num(rid: str) -> str:
                m = _re_eid.search(r'(\d+)$', rid.strip())
                return m.group(1) if m else rid.strip()

            backfilled = 0
            for rxn in specific_reactions:
                entry_id = str(rxn.get("reaction_id", rxn.get("entry_id", "")))
                num = _entry_num(entry_id)
                compound_name = (
                    mapping.get(entry_id)           # exact match ("Entry 1")
                    or mapping.get(num)              # trailing number ("1")
                    or mapping.get(entry_id.lstrip("0"))  # zero-stripped
                )
                if not compound_name:
                    continue
                for cond in rxn.get("conditions", []):
                    if cond.get("role") in (col_name, col_role) and _is_effectively_empty(cond.get("text", "")):
                        cond["text"] = compound_name
                        backfilled += 1
                        _log.info(f"[GalleryMapper] Entry {entry_id} → '{compound_name}'")

            if backfilled:
                print(f"  [GalleryMapper] Backfilled {backfilled} entries from gallery '{gallery_id}'")

        return specific_reactions

    @staticmethod
    def _col_name_to_role(col_name: str) -> str:
        """
        Convert a column header like 'Photocatalyst (mol%)' to the canonical
        condition role string used by the intelligence engine ('photocatalyst').
        Strips parenthetical units/suffixes, lowercases, and collapses spaces.
        """
        import re as _re_col
        base = _re_col.sub(r'\s*[\(\[].*', '', col_name).strip().lower()
        # Map common multi-word variants to the intelligence-engine role names
        _aliases = {
            "photocatalyst": "photocatalyst",
            "photo catalyst": "photocatalyst",
            "catalyst": "catalyst",
            "ligand": "ligand",
            "base": "base",
            "solvent": "solvent",
            "additive": "additive",
            "oxidant": "oxidant",
            "reductant": "reductant",
            "metal salt": "metal_salt",
            "metal_salt": "metal_salt",
            "reagent": "reagent",
        }
        return _aliases.get(base, base)

    def _is_standard_reagent(self, text: str) -> bool:
        """Heuristic to detect standard chemical formulas/names that shouldn't be mapped."""
        import re
        if not text: return True
        # Standard formulas with simple numbers (Cs2CO3, H2O, Pd(OAc)2)
        if re.match(r'^[A-Z][a-z]?\d*(?:[A-Z][a-z]?\d*)*$', text): return True
        if "(" in text and ")" in text and len(text) < 15: return True # Common salts/ligands
        
        # Common acronyms usually resolved by AbbreviationAgent, not LabelResolver
        COMMON = {"DMF", "DMSO", "THF", "ACN", "DBU", "TEA", "DMAP", "MTBD", "BTPP", "DCM"}
        if text.upper() in COMMON: return True
        
        return False

    def _apply_resolved_mappings(self, entries: List[Dict], mappings: List[Dict], column_name: str, source_id: str) -> List[Dict]:
        """Merges resolved SMILES/Names into the corresponding reaction entries."""
        mapping_dict = {m["label"]: m for m in mappings if m.get("smiles") or m.get("name")}
        _col_role = self._col_name_to_role(column_name)

        for entry in entries:
            for cond in entry.get("conditions", []):
                cond_role = cond.get("role", "")
                if cond_role == column_name or cond_role == _col_role or cond_role == "reagent":
                    label_text = cond.get("text")
                    if label_text in mapping_dict:
                        res = mapping_dict[label_text]
                        cond["resolved_name"] = res.get("name")
                        cond["resolved_smiles"] = res.get("smiles")
                        cond["smiles_source"] = res.get("smiles_source", "llm_vision")
                        cond["mapping_source"] = source_id
                        for _k in ("smiles_pubchem", "smiles_opsin", "smiles_llm", "smiles_ocsr_raw"):
                            if res.get(_k):
                                cond[_k] = res[_k]
        return entries
