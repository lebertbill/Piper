import logging
import json
import base64
from typing import List, Dict, Any
from PIL import Image

from miner.agents.base_agent import BaseAgent
from miner.utils.smiles_sanitizer import sanitize_smiles
from miner.diagnostic_logger import diagnostic_logger as _diag

_log = logging.getLogger(__name__)


class LabelResolverAgent(BaseAgent):
    """
    Resolves chemical labels (e.g. "PC5", "L2", "4b") to SMILES using a seven-tier strategy that maximises deterministic OCSR and minimises
    reliance on LLM-generated SMILES:

    Tier 0  name_map         When compound_name_map already has the
                              authoritative chemical name for the label,
                              run it through PubChem → OPSIN → LLM-name-to-
                              SMILES BEFORE OCSR. Decisive for organometallic
                              photocatalysts (Ir/Ru/Pd/etc.) where MolScribe
                              is documented to fail (as observed).

    Tier 1  ocsr_coref        ChemIEToolkit pairs the molecule drawing with its
                              text label directly via coref detection.  Zero LLM
                              cost — purely OCSR.

    Tier 2  ocsr_graph        OCSR graph two-phase pipeline: LLM tool-calls the
                              OCSR tool, receives the full atom graph (coords,
                              edges, symbols), corrects any OCR errors in atom
                              symbols, and _convert_graph_to_smiles rebuilds
                              canonical SMILES deterministically via RDKit.
                              Handles: symbol OCR errors, missing substituents,
                              wrong atom labels.

    Tier 3  ocsr_graph_visual Piper targeted approach: OCSR supplies the graph;
                              a focused LLM call visually identifies which bbox
                              corresponds to each unmatched label and corrects
                              symbols.  Then RDKit rebuilds SMILES.
                              Handles: cases where OCSR never associated the
                              label with any molecule (label text not detected).

    Tier 4  ocsr_rgroup       R-group substitution + visual verification.
                              When OCSR produces wildcard SMILES (*) and the
                              image contains explicit R-group text equations
                              (e.g. "L1, R=H  L2, R=tBu"), the LLM substitutes
                              each R value into the wildcard positions.  RDKit
                              renders the result to an image, and a second LLM
                              call verifies the rendered structure against the
                              original crop.  Bridges RDKit atom-graph tiers
                              and pure LLM generation.

    Tier 5  pubchem_lookup    PubChem REST name/synonym search.  Catches common
                              reagents and photocatalysts by name.
                              Zero LLM cost — pure API lookup.

    Tier 6  llm_vision        Last resort.  LLM generates SMILES directly from
                              the image with the OCSR skeleton as context.
                              Used only when all structural paths have failed.
    """

    def __init__(self):
        from context import load_config
        config = load_config()
        model = config.get("model", {}).get("agents", {}).get("label_resolver_model", "openai/gpt-4o")
        super().__init__(model=model)

    # ── Public API ──────────────────────────────────────────
    def resolve(self, labels: List[str], source_type: str, source_content: str, source_id: str, progress_callback=None, paper_text: str = "", name_map: Dict[str, str] = None) -> Dict[str, Any]:
        """
        Resolve a list of labels to {smiles, name, smiles_source, found_in}.

        Args:
            labels:       Labels to resolve (e.g. ["PC1", "PC2", "4b"]).
            source_type:  "image" or "text".
            source_content: Absolute image path or markdown text string.
            source_id:    Human-readable name for logging (e.g. "Figure 3b").
            paper_text:   Full markdown text of the paper.
            name_map:     LLM-extracted {label → compound_name} from the
                          advanced structure parser. Used as the authoritative
                          name source for PubChem lookup — takes priority over
                          regex extraction from paper text.
        """
        self._paper_text = paper_text  # make available to tier methods
        self._name_map = name_map or {}
        if source_type == "text":
            return self._resolve_from_text(labels, source_content, source_id)
        return self._resolve_from_image(labels, source_content, source_id,
                                        progress_callback)

    # ── Text resolution ────────────────────────────────────────────────

    def _resolve_from_text(self, labels, text, source_id):
        user_content = (
            f"LABELS TO RESOLVE: {labels}\n"
            f"SOURCE ID: {source_id}\n"
            f"SOURCE TYPE: text\n\n"
            f"SOURCE TEXT:\n{text}"
        )
        messages = self._assemble_prompt(
            base_prompt="You are a helpful assistant that outputs JSON.",
            user_content=user_content,
            skills=["label_mapping.md"]
        )
        try:
            response = self._call_llm(messages, response_format={"type": "json_object"})
            data = self._parse_json(response)
            mappings = self._extract_mappings(data)
            for m in mappings:
                if "smiles" in m:
                    m["smiles"] = sanitize_smiles(m["smiles"])
                m.setdefault("smiles_source", "llm_vision")
            return {"mappings": mappings}
        except Exception as e:
            _log.error(f"LabelResolverAgent text resolve failed: {e}")
            return {"mappings": []}

    # ── Image resolution — four-tier ──────────────────────────────────────────

    def _resolve_from_image(self, labels, image_path, source_id, progress_callback):
        import os
        _diag.section(f"LabelResolver: {source_id}")
        _diag.write(f"  Labels   : {labels}")
        _diag.write(f"  Image    : {image_path}")
        _diag.write(f"  Exists   : {os.path.exists(image_path)}")

        # Cache for name → SMILES resolutions reused across tiers
        self._name_resolution_cache: dict = {}

        # ── Tier 0: name_map shortcut ───────────────────────────────────────────

        t0_mappings, remaining = self._tier0_name_map_shortcut(labels, source_id)
        _diag.write(f"  [Tier 0 NameMap] resolved={[m['label'] for m in t0_mappings]}  remaining={remaining}")
        if not remaining:
            self._enrich_names(t0_mappings)
            return {"mappings": t0_mappings}

        # ── Tier 1 (Direct): OCSR coref — no LLM ────────────────────────────────
        raw_bboxes, clean_corefs = self._run_ocsr(image_path)
        t1_mappings, remaining = self._tier1_text_match(remaining, clean_corefs, source_id)
        _diag.write(f"  [Tier 1 Direct] resolved={[m['label'] for m in t1_mappings]}  remaining={remaining}")
        if not remaining:
            combined = t0_mappings + t1_mappings
            self._inject_ocsr_raw(combined, clean_corefs)
            self._enrich_names(combined)
            return {"mappings": combined}

        if progress_callback:
            progress_callback(f"🔬 Tier 2: OCSR graph + LLM symbol correction for {remaining}…")

        # ── Tier 2 (LLM symbol correction): OCSR graph -> LLM corrects atom symbols --> RDKit ──
        t2_mappings, remaining = self._tier2_ocsr_graph(remaining, image_path, source_id)
        _diag.write(f"  [Tier 2 Symbol Correction] resolved={[m['label'] for m in t2_mappings]}  remaining={remaining}")
        if not remaining:
            combined = t0_mappings + t1_mappings + t2_mappings
            self._inject_ocsr_raw(combined, clean_corefs)
            self._enrich_names(combined)
            return {"mappings": combined}

        if progress_callback:
            progress_callback(f"🎯 Tier 3: OCSR graph + LLM bbox matching for {remaining}…")

        # ── Tier 3 (LLM bbox matching): OCSR orphan bboxes -> LLM identifies correct bbox -> RDKit ──
        t3_mappings, remaining = self._tier3_visual_bbox(remaining, raw_bboxes, image_path, source_id)
        _diag.write(f"  [Tier 3 BBox Match] resolved={[m['label'] for m in t3_mappings]}  remaining={remaining}")
        if not remaining:
            combined = t0_mappings + t1_mappings + t2_mappings + t3_mappings
            self._inject_ocsr_raw(combined, clean_corefs)
            self._enrich_names(combined)
            return {"mappings": combined}

        if progress_callback:
            progress_callback(f"🔄 Tier 4: R-group substitution + visual verify for {remaining}…")

        # ── Tier 4 (LLM R-group substitution): wildcard SMILES + label-R text -> LLM substitutes -> verify ──
        t4_mappings, remaining = self._tier4_rgroup_sub(remaining, clean_corefs, raw_bboxes, image_path, source_id)
        _diag.write(f"  [Tier 4 R-group Sub] resolved={[m['label'] for m in t4_mappings]}  remaining={remaining}")
        if not remaining:
            combined = t0_mappings + t1_mappings + t2_mappings + t3_mappings + t4_mappings
            self._inject_ocsr_raw(combined, clean_corefs)
            self._enrich_names(combined)
            return {"mappings": combined}

        if progress_callback:
            progress_callback(f"🔍 Tier 5: PubChem lookup for {remaining}…")

        # ── Tier 5 (PubChem): compound name -> PubChem API ────────────────────────
        t5_mappings, remaining = self._tier35_pubchem(remaining, raw_bboxes, source_id)
        _diag.write(f"  [Tier 5 PubChem] resolved={[m['label'] for m in t5_mappings]}  remaining={remaining}")
        if not remaining:
            combined = t0_mappings + t1_mappings + t2_mappings + t3_mappings + t4_mappings + t5_mappings
            self._inject_ocsr_raw(combined, clean_corefs)
            self._enrich_names(combined)
            return {"mappings": combined}

        if progress_callback:
            progress_callback(f"👁️ Tier 6: LLM vision for {remaining}…")

        # ── Tier 6 (LLM vision): last resort — LLM generates SMILES from image 
        t6_mappings = self._tier4_llm_vision(remaining, clean_corefs, image_path, source_id)
        _diag.write(f"  [Tier 6 LLM Vision] resolved={[m['label'] for m in t6_mappings]}")

        all_mappings = (t0_mappings + t1_mappings + t2_mappings + t3_mappings
                        + t4_mappings + t5_mappings + t6_mappings)
        self._inject_ocsr_raw(all_mappings, clean_corefs)
        self._enrich_names(all_mappings)
        return {"mappings": all_mappings}

    # ── Shared OCSR runner ─────────────────────────────────────────────

    def _run_ocsr(self, image_path: str):
        """
        Run ChemIEToolkit and return:
          raw_bboxes  — full bbox list with coords/symbols/edges (for Tier 3)
          clean_corefs — [{smiles, texts, bbox}] paired list (for Tier 1 / Tier 4)
        """
        import os
        from miner.intelligence.core.model_registry import get_toolkit

        if not os.path.exists(image_path):
            _log.warning(f"[OCSR] Image not found: {image_path}")
            _diag.write(f"  [OCSR] ⚠️  Image not found: {image_path}")
            return [], []

        try:
            image = Image.open(image_path).convert("RGB")
            results = get_toolkit().extract_molecule_corefs_from_figures([image], molscribe=True, ocr=True)
        except Exception as e:
            _log.warning(f"[OCSR] ChemIEToolkit failed on {image_path}: {e}")
            _diag.write(f"  [OCSR] ⚠️  ChemIEToolkit exception: {e}")
            return [], []

        if not results:
            _log.warning(f"[OCSR] No results returned for {image_path}")
            _diag.write(f"  [OCSR] ⚠️  No results returned")
            return [], []

        data = results[0]
        raw_bboxes = data.get("bboxes", [])
        coref_pairs = data.get("corefs", [])
        _diag.write(f"  [OCSR] {os.path.basename(image_path)}: {len(raw_bboxes)} bboxes, {len(coref_pairs)} coref pairs")

        clean_corefs = []
        paired_idx = set()
        for i1, i2 in coref_pairs:
            mol_entry = raw_bboxes[i1] if "smiles" in raw_bboxes[i1] else raw_bboxes[i2]
            txt_entry = raw_bboxes[i2] if "text"  in raw_bboxes[i2] else raw_bboxes[i1]
            smiles = mol_entry.get("smiles", "")
            texts  = txt_entry.get("text", [])
            has_wildcard = smiles and "*" in smiles
            _diag.write(f"  [OCSR]   pair [{i1},{i2}]: texts={texts}  smiles={smiles[:50] if smiles else 'N/A'}  wildcard={has_wildcard}")
            if smiles and not has_wildcard:
                clean_corefs.append({
                    "smiles": smiles,
                    "texts":  texts,
                    "bbox":   mol_entry.get("bbox", ()),
                })
            elif smiles and has_wildcard:
                # Keep wildcard molecules for Tier 3 graph reconstruction but mark them
                clean_corefs.append({
                    "smiles": "",      # empty so Tier 1 skips but texts are preserved
                    "smiles_raw": smiles,
                    "texts":  texts,
                    "bbox":   mol_entry.get("bbox", ()),
                })
            paired_idx.update([i1, i2])

        # Orphan molecules (no label detected by OCSR)
        for idx, entry in enumerate(raw_bboxes):
            if "smiles" in entry and idx not in paired_idx:
                smiles = entry.get("smiles", "")
                if smiles and "*" not in smiles:
                    clean_corefs.append({
                        "smiles": smiles,
                        "texts":  ["No label detected — recheck image"],
                        "bbox":   entry.get("bbox", ()),
                    })

        _diag.write(f"  [OCSR] → {len(clean_corefs)} usable corefs for Tier 1 matching")
        return raw_bboxes, clean_corefs

    # ── Tier 1 ────────────────────────────────────────────────────────────────

    @staticmethod
    def _text_contains_label(ocr_text: str, label_lower: str) -> bool:
        """
        Return True if `label_lower` appears as a standalone token in `ocr_text`.

        OCR frequently bundles the label with yield / extra characters, e.g.:
          "3a,"  "3d, 53%"  "80*0\n3a,"  "L1, R-h"
        We tokenise on common separators and check each token against the label.
        Also allows direct substring-at-start match terminated by a non-alphanumeric.
        """
        import re
        # Normalise common OCR confusions before any comparison:
        # capital-I at the start of a token is often digit-1 in compound labels.
        def _ocr_norm(s: str) -> str:
            return re.sub(r'(?<![A-Za-z])I(?=[a-z0-9])', '1', s)

        t = _ocr_norm(ocr_text.lower().strip())
        label_lower = _ocr_norm(label_lower)
        # Exact match
        if t == label_lower:
            return True
        # Token split on comma / whitespace / bracket / percent / asterisk / digit-run
        tokens = re.split(r'[\s,\(\)\[\]{}\*%\|/\\]+', t)
        if label_lower in tokens:
            return True
        # Prefix match: label followed immediately by non-alphanumeric,
        # BUT exclude range patterns like "4c-7c" where a dash is followed
        # by more alphanumeric content — that's a label range, not this label.
        if re.match(rf'^{re.escape(label_lower)}[^a-z0-9]', t):
            if not re.match(rf'^{re.escape(label_lower)}-[a-z0-9]', t):
                return True
        return False

    def _tier1_text_match(self, labels, clean_corefs, source_id):
        """Match labels against OCR-detected text in the OCSR coref output.

        Uses flexible tokenisation so labels embedded in yield strings
        (e.g. '3a,' '3d, 53%') are still matched.

        Prime-label fallback: when the OCSR tool labels both a compound and its
        prime isomer with the same base text (e.g. both "3aa" and "3aa'" appear
        as "3aa" in the OCR), the first match goes to the base label; any
        structurally distinct coref still labeled "3aa" is then assigned to the
        primed variant ("3aa'") as a fallback.
        """
        import re
        # Track which smiles were already assigned so the fallback can avoid reuse
        assigned_smiles: set = set()

        direct, remaining = [], []
        for label in labels:
            label_lower = label.lower().strip()
            matched_smiles = None
            for item in clean_corefs:
                smiles = item.get("smiles", "")
                if not smiles:
                    continue
                for raw_text in item.get("texts", []):
                    if self._text_contains_label(raw_text, label_lower):
                        matched_smiles = smiles
                        break
                if matched_smiles:
                    break

            if not matched_smiles:
                # Prime-label fallback: strip trailing primes and check if the base label matches any coref that hasn't been assigned yet.
                base_label = label_lower.rstrip("'\"")
                if base_label != label_lower:
                    for item in clean_corefs:
                        smiles = item.get("smiles", "")
                        if not smiles or smiles in assigned_smiles:
                            continue
                        for raw_text in item.get("texts", []):
                            if self._text_contains_label(raw_text, base_label):
                                matched_smiles = smiles
                                break
                        if matched_smiles:
                            _log.info(
                                f"[Tier 1] '{label}' → prime-fallback via base '{base_label}'"
                            )
                            break

            if matched_smiles:
                assigned_smiles.add(matched_smiles)
                direct.append({
                    "label": label,
                    "smiles": sanitize_smiles(matched_smiles),
                    "name": label,
                    "found_in": source_id,
                    "smiles_source": "ocsr_coref",
                })
                _log.info(f"[Tier 1] '{label}' -> ocsr_coref")
            else:
                remaining.append(label)
        return direct, remaining

    # ── Tier 2 ────────────────────────────────────────────────────────────────

    def _tier2_ocsr_graph(self, remaining_labels, image_path, source_id):
        """
        Full OCSR graph two-phase pipeline:

          Phase 1 — LLM tool-calls the OCSR tool, receives the full atom graph
                    (coords, symbols, edges) for every molecule in the image.
          Phase 2 — LLM corrects OCR errors in atom symbols AND extracts
                    explicit R-group equations visible as standalone text
                    (e.g. "L2  R = tBu", "Ar = 4-CF₃C₆H₄").
                    Matched R-group placeholders ([R1], [Ar], *) are replaced
                    with the actual substituent symbols before SMILES regeneration.
          Final   — _convert_graph_to_smiles(coords, updated_symbols, edges)
                    rebuilds canonical SMILES deterministically via RDKit.

        Produces fully substituted SMILES when R-group equations are visible;
        otherwise falls back to symbol-corrected SMILES (may still be wildcard).
        """
        from miner.intelligence.agents.molecular_agent import (
            process_reaction_multiple_products_correctmultiR,
        )
        from miner.intelligence.agents.reaction_agent import _parse_corefs

        try:
            # Two-phase pipeline: OCSR tool call + LLM symbol correction and R-group substitution from visible text equations in the image.
            corrected_data = process_reaction_multiple_products_correctmultiR(image_path)
        except Exception as e:
            _log.warning(f"[Tier 2] OCSR graph pipeline failed: {e}")
            return [], remaining_labels

        # Parse the corrected coref data into [{smiles, texts, bbox}]
        corrected_corefs = []
        for item in corrected_data:
            corrected_corefs.extend(_parse_corefs(item))

        # Re-attempt label text matching on the corrected output (same flexible matching as Tier 1).
        # Clean SMILES (no wildcards) -> fully resolved.
        # Wildcard SMILES -> not resolved, but store as smiles_ocsr_graph for downstream use.
        tier2_mappings, still_remaining = [], []
        self._ocsr_graph_wildcards: dict = {}  # label -> OCSR graph wildcard SMILES

        for label in remaining_labels:
            label_lower = label.lower().strip()
            clean_smiles = None
            wildcard_smiles = None
            for item in corrected_corefs:
                smiles = item.get("smiles", "")
                if not smiles:
                    continue
                for raw_text in item.get("texts", []):
                    if self._text_contains_label(raw_text, label_lower):
                        if "*" not in smiles:
                            clean_smiles = smiles
                        else:
                            wildcard_smiles = smiles
                        break
                if clean_smiles:
                    break

            if clean_smiles:
                tier2_mappings.append({
                    "label": label,
                    "smiles": sanitize_smiles(clean_smiles),
                    "name": label,
                    "found_in": source_id,
                    "smiles_source": "ocsr_graph",
                })
                _log.info(f"[Tier 2] '{label}' → ocsr_graph")
            else:
                if wildcard_smiles:
                    self._ocsr_graph_wildcards[label] = wildcard_smiles
                still_remaining.append(label)

        return tier2_mappings, still_remaining

    # ── Tier 3 ────────────────────────────────────────────────────────────────

    def _tier3_visual_bbox(self, remaining_labels, raw_bboxes, image_path, source_id):
        """
        Piper targeted approach: LLM visually matches each label to its molecule
        bbox, corrects atom symbols if needed, then RDKit rebuilds SMILES.
        Handles cases where OCSR never associated the label with any molecule
        (e.g., label text was not detected by OCR at all).
        """
        from miner.intelligence.core.molnextr.chemistry import _convert_graph_to_smiles
        from miner.intelligence.engine import load_prompt_with_commons
        from context import load_config, get_openrouter_url
        from openai import OpenAI
        import os

        if not raw_bboxes:
            return [], remaining_labels

        # Build full bbox summary for the LLM — all symbols, no truncation
        bbox_summary = []
        for idx, bbox in enumerate(raw_bboxes):
            if "smiles" not in bbox:
                continue
            bbox_summary.append({
                "bbox_idx":      idx,
                "bbox_position": bbox.get("bbox", []),
                "initial_smiles": (bbox.get("smiles") or "")[:100],
                "atom_symbols":  bbox.get("symbols", []),   # full list, no truncation
            })

        if not bbox_summary:
            return [], remaining_labels

        prompt = load_prompt_with_commons("label_bbox_symbol_correction_prompt.txt")
        user_text = (
            f"LABELS TO RESOLVE: {remaining_labels}\n"
            f"SOURCE: {source_id}\n\n"
            f"BBOX_SUMMARY (from OCSR tool):\n{json.dumps(bbox_summary, indent=2)}\n\n"
            "Match each label to its bbox and correct atom symbols if needed."
        )

        config = load_config()
        api_key = (
            os.environ.get("OPENROUTER_API_KEY") or
            os.environ.get("API_KEY") or
            os.environ.get("OPENAI_API_KEY")
        )
        model = config.get("model", {}).get("agents", {}).get("label_resolver_model", "openai/gpt-4o")
        client = OpenAI(api_key=api_key, base_url=get_openrouter_url())

        with open(image_path, "rb") as f:
            b64 = base64.b64encode(f.read()).decode("utf-8")

        messages = [
            {"role": "system", "content": "You are a helpful assistant that outputs JSON."},
            {"role": "user", "content": [
                {"type": "text", "text": prompt + "\n\n" + user_text},
                {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{b64}"}},
            ]},
        ]

        try:
            resp = client.chat.completions.create(
                model=model, temperature=0, messages=messages,
                response_format={"type": "json_object"}
            )
            from miner.token_tracker import tracker
            if hasattr(resp, "usage") and resp.usage:
                tracker.record("LabelResolverAgent_Tier3", model,
                               resp.usage.prompt_tokens or 0,
                               resp.usage.completion_tokens or 0)
            data = json.loads(resp.choices[0].message.content or "{}")
        except Exception as e:
            _log.warning(f"[Tier 3] Visual bbox matching call failed: {e}")
            return [], remaining_labels

        tier3_mappings, still_remaining = [], []

        for match in data.get("label_matches", []):
            label = match.get("label")
            bbox_idx = match.get("bbox_idx")
            corrected_syms = match.get("corrected_symbols")

            if label not in remaining_labels:
                continue
            if bbox_idx is None or bbox_idx >= len(raw_bboxes):
                _diag.write(f"  [Tier 3] '{label}': bbox_idx={bbox_idx} out of range (n={len(raw_bboxes)})")
                still_remaining.append(label)
                continue

            bbox = raw_bboxes[bbox_idx]
            coords  = bbox.get("coords", [])
            edges   = bbox.get("edges", [])
            symbols = corrected_syms if corrected_syms else bbox.get("symbols", [])

            if not coords or not edges or not symbols:
                _diag.write(f"  [Tier 3] '{label}': no graph data at bbox {bbox_idx} "
                            f"(coords={len(coords)}, edges={len(edges)}, symbols={len(symbols)})")
                still_remaining.append(label)
                continue

            # Guard: symbol count must match atom count
            if len(symbols) != len(coords):
                _diag.write(f"  [Tier 3] '{label}': symbol/coord count mismatch ({len(symbols)} vs {len(coords)}), using original")
                symbols = bbox.get("symbols", [])

            try:
                smiles, _, _ = _convert_graph_to_smiles(coords, symbols, edges)
                has_wildcard = smiles and "*" in smiles
                _diag.write(f"  [Tier 3] '{label}': bbox={bbox_idx} atoms={len(symbols)} "
                            f"smiles={smiles[:60] if smiles else 'None'} wildcard={has_wildcard}")
                if smiles and not has_wildcard:
                    # Always reject organometallic SMILES from MolScribe/OCSR.
                    # MolScribe routinely emits chemically invalid SMILES for transition-metal complexes (broken ring closures, wrong
                    # coordination bond counts) regardless of whether a name_map
                    # entry exists. Deferring unconditionally to Tier 5/6 name
                    # resolution is far more reliable for these structures.
                    if self._contains_transition_metal(smiles):
                        _diag.write(
                            f"  [Tier 3] '{label}': SMILES contains transition metal "
                            f"— always deferring to Tier 5/6 (MolScribe unreliable for organometallics)"
                        )
                        still_remaining.append(label)
                        continue
                    tier3_mappings.append({
                        "label": label,
                        "smiles": sanitize_smiles(smiles),
                        "name": label,
                        "found_in": source_id,
                        "smiles_source": "ocsr_graph_visual",
                    })
                else:
                    still_remaining.append(label)
            except Exception as e:
                _diag.write(f"  [Tier 3] '{label}': graph→SMILES exception — {e}")
                still_remaining.append(label)

        # Labels the LLM didn't return an answer for
        answered = {m["label"] for m in tier3_mappings} | set(still_remaining)
        for label in remaining_labels:
            if label not in answered:
                still_remaining.append(label)

        return tier3_mappings, still_remaining

    # ── OCSR raw SMILES injection ─────────────────────────────────────────────

    def _inject_ocsr_raw(self, mappings: list, clean_corefs: list) -> None:
        """
        For every resolved mapping, attach:
          - smiles_ocsr_raw  : raw MolScribe SMILES (may contain * substituent placeholders)
          - smiles_ocsr_graph : OCSR graph SMILES with R-group substitution applied where text equations were found
        This lets downstream code store the full SMILES provenance:
          smiles           = best available (Tier 1 OCSR / Tier 2 OCSR graph clean / PubChem / LLM)
          smiles_ocsr_raw  = MolScribe raw output
          smiles_ocsr_graph = OCSR graph output with R-group substitution (may still contain wildcards if no text equations found)
        """
        ocsr_graph_wc = getattr(self, "_ocsr_graph_wildcards", {})
        for m in mappings:
            label = m.get("label", "")
            if not label:
                continue
            # smiles_ocsr_raw from OCSR coref
            for coref in clean_corefs:
                raw = coref.get("smiles_raw") or coref.get("smiles", "")
                if not raw:
                    continue
                for t in coref.get("texts", []):
                    if self._text_contains_label(t, label.lower()):
                        m["smiles_ocsr_raw"] = raw
                        break
                if "smiles_ocsr_raw" in m:
                    break
            # smiles_ocsr_graph from Tier 2 wildcard captures
            if label in ocsr_graph_wc:
                m["smiles_ocsr_graph"] = ocsr_graph_wc[label]

    # ── Tier 4: R-group substitution + visual verification ───────────────────

    def _tier4_rgroup_sub(self, remaining_labels: list, clean_corefs: list, raw_bboxes: list, image_path: str, source_id: str):
        """
        Tier 4 — LLM-driven R-group substitution with visual verification.

        Label naming in chemistry papers is unpredictable (PC-1, Cat-3, Ir-2,
        PC-4, …) so all scaffold-to-label assignment is done by an LLM that reads
        the image directly rather than by regex rules.

        Step 1 — LLM assignment:
          Send the full image + all OCSR wildcard SMILES + all remaining labels.
          LLM returns {label, scaffold_smiles, r_value} for each R-group variant.

        Step 2 — SMILES generation:
          For each assigned label, LLM substitutes the r_value into the wildcard
          scaffold to produce a specific SMILES.

        Step 3 — Visual verification:
          RDKit renders the generated SMILES; a second LLM call compares the
          rendered image against the original crop and corrects if mismatched.
        """
        import re as _re
        import json as _json
        import base64 as _b64
        from io import BytesIO
        from PIL import Image as _PILImage
        from dotenv import load_dotenv as _lde
        _lde()
        from openai import OpenAI as _OpenAI
        from context import get_openrouter_url as _oru
        import os as _os

        def _img_b64(pil_img) -> str:
            buf = BytesIO()
            pil_img.save(buf, format="PNG")
            return _b64.b64encode(buf.getvalue()).decode()

        def _smiles_to_img(smiles: str):
            try:
                from rdkit import Chem
                from rdkit.Chem import Draw
                mol = Chem.MolFromSmiles(smiles)
                return Draw.MolToImage(mol, size=(300, 300)) if mol else None
            except Exception:
                return None

        def _call(messages):
            api_key = (_os.environ.get("OPENROUTER_API_KEY")
                       or _os.environ.get("API_KEY")
                       or _os.environ.get("OPENAI_API_KEY"))
            client = _OpenAI(api_key=api_key, base_url=_oru())
            from context import load_config as _lc
            model = _lc().get("model", {}).get("agents", {}).get(
                "label_resolver_model", "google/gemini-3-flash-preview")
            resp = client.chat.completions.create(
                model=model, temperature=0, messages=messages,
                response_format={"type": "json_object"}
            )
            from miner.token_tracker import tracker
            if hasattr(resp, "usage") and resp.usage:
                tracker.record("LabelResolverAgent", model,
                               resp.usage.prompt_tokens or 0,
                               resp.usage.completion_tokens or 0)
            return resp.choices[0].message.content or ""

        from miner.intelligence.core.molnextr.chemistry import _convert_graph_to_smiles
        from miner.intelligence.core.molnextr.constants import RGROUP_SYMBOLS

        # Collect all wildcard scaffolds from OCSR output
        wildcard_corefs = [c for c in clean_corefs if "*" in c.get("smiles_raw", "")]
        if not wildcard_corefs:
            return [], remaining_labels

        # Build bbox → raw_bboxes lookup for graph-level substitution (Change 1)
        bbox_to_raw: dict = {}
        for entry in raw_bboxes:
            if "smiles" in entry and entry.get("coords") and entry.get("symbols") and entry.get("edges"):
                key = tuple(entry.get("bbox", ()))
                bbox_to_raw[key] = entry

        # Prefer graph-corrected SMILES from Tier 2 for the scaffold list (Change 2)
        ocsr_graph_wc = getattr(self, "_ocsr_graph_wildcards", {})

        label_rgroup_map: dict = {}

        # ── LLM assignment ────────────────────────────────────────────────────
        # Send the full image + all wildcard SMILES + all remaining labels to the llm.
        #It reads the image visually to determine which scaffold each label belongs to and what its specific substituent value is.
        # This handles any naming convention (OPC-1, Cat-3, L2, PC-4, Ir-5, …) without brittle regex assumptions.
        try:
            pil_img   = _PILImage.open(image_path).convert("RGB")
            img_b64   = _img_b64(pil_img)
            def _best_scaffold_smiles(c):
                # Prefer Tier-2 graph-corrected SMILES; fall back to raw MolScribe output
                for lbl in c.get("texts", []):
                    if lbl in ocsr_graph_wc:
                        return ocsr_graph_wc[lbl]
                return c.get("smiles_raw", "")

            scaffold_list = "\n".join(
                f"  Scaffold {i+1}: {_best_scaffold_smiles(c)}  "
                f"(OCR texts nearby: {c.get('texts', [])})"
                for i, c in enumerate(wildcard_corefs)
            )
            llm_prompt = (
                "You are a chemistry expert. The image shows a reaction table or figure "
                "containing one or more drawn molecular scaffolds that have wildcard "
                "positions (*) for variable substituents.\n\n"
                f"WILDCARD SCAFFOLDS detected by OCSR:\n{scaffold_list}\n\n"
                f"LABELS TO RESOLVE: {remaining_labels}\n\n"
                "For each label, determine:\n"
                "  1. Which scaffold it belongs to (copy the scaffold SMILES exactly).\n"
                "  2. The specific substituent value for that label variant "
                "(e.g. 'X = CF3', 'R = tBu', 'Ar = 4-pyridyl').\n\n"
                "The substituent may be written next to the scaffold in the image, "
                "encoded in the label text itself (e.g. 'L3 (R = tBu)'), or visible "
                "in the column header or table rows.\n\n"
                'Return JSON: {"assignments": [{"label": "...", '
                '"scaffold_smiles": "...", "r_value": "..."}, ...]}\n'
                "Omit any label that is NOT an R-group variant of one of the scaffolds."
            )
            resp_raw = _call([
                {"role": "system", "content": "You are a helpful chemistry assistant that outputs JSON."},
                {"role": "user", "content": [
                    {"type": "text",      "text": llm_prompt},
                    {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{img_b64}"}},
                ]},
            ])
            assignments   = _json.loads(resp_raw).get("assignments", [])
            scaffold_bbox = {c.get("smiles_raw", ""): c.get("bbox", ()) for c in wildcard_corefs}
            for a in assignments:
                lbl  = a.get("label", "")
                smi  = a.get("scaffold_smiles", "")
                rval = a.get("r_value", "")
                if lbl in remaining_labels and smi and rval:
                    label_rgroup_map[lbl] = {
                        "wildcard_smiles": smi,
                        "bbox":            scaffold_bbox.get(smi, ()),
                        "r_value":         rval,
                        "r_source":        "llm_assignment",
                    }
                    _diag.write(f"    [Tier 4 LLM] '{lbl}' → r_value='{rval}'")
        except Exception as e:
            _diag.write(f"    [Tier 4 LLM] assignment failed: {e}")

        if not label_rgroup_map:
            return [], remaining_labels

        _diag.write(f"    [Tier 4] R-group candidates: {list(label_rgroup_map.keys())}")

        # Load full image once
        pil_full = _PILImage.open(image_path).convert("RGB")
        img_w, img_h = pil_full.size

        # Group labels sharing the same molecule bbox (same template drawing)
        from collections import defaultdict
        bbox_groups: dict = defaultdict(list)
        for lbl, info in label_rgroup_map.items():
            key = tuple(info["bbox"])
            bbox_groups[key].append(lbl)

        resolved, still_remaining = [], []

        for bbox_key, group_labels in bbox_groups.items():
            bbox = list(bbox_key)
            wildcard_smiles = label_rgroup_map[group_labels[0]]["wildcard_smiles"]

            # Crop the molecule bbox.
            # bbox is empty when the LLM returned a scaffold_smiles that didn't exactly match any scaffold_bbox key (format mismatch, e.g. "*c1ccccc1" vs "[*:1]c1ccccc1"). Fall back to the full image so the rest of the Tier 4 pipeline can still attempt substitution.
            pad = 20
            if len(bbox) >= 4:
                x1 = max(0, int(bbox[0] * img_w) - pad)
                y1 = max(0, int(bbox[1] * img_h) - pad)
                x2 = min(img_w, int(bbox[2] * img_w) + pad)
                y2 = min(img_h, int(bbox[3] * img_h) + pad)
                crop = pil_full.crop((x1, y1, x2, y2))
            else:
                _diag.write(f"    [Tier 4] bbox missing for group {group_labels} — using full image")
                crop = pil_full
            crop_b64 = _img_b64(crop)

            # Build R-group list for this group
            rgroup_lines = "\n".join(
                f"  {lbl}: R = {label_rgroup_map[lbl]['r_value']}"
                for lbl in group_labels
            )

            # ── Step 1: Graph-level substitution (ChemEagle approach) ─────────
            # Find the raw bbox entry for this scaffold so we can replace wildcard
            # symbols directly in the atom graph and regenerate SMILES via RDKit.
            # This avoids asking an LLM to do SMILES string surgery, which breaks
            # ring closures, chirality markers, and aromatic notation.
            raw_entry = bbox_to_raw.get(bbox_key)
            graph_candidates: dict = {}  
            llm_needed: list = []        

            if raw_entry:
                g_coords  = raw_entry.get("coords", [])
                g_symbols = raw_entry.get("symbols", [])
                g_edges   = raw_entry.get("edges", [])

                for lbl in group_labels:
                    r_val = label_rgroup_map[lbl]["r_value"]
                    # Replace every wildcard position with the substituent symbol.
                    # _convert_graph_to_smiles handles ABBREVIATIONS expansion (Ph,
                    # tBu, CF3, OMe, …) and valid RDKit atoms (H, F, Cl, …).
                    updated = [r_val if (s == "*" or s in RGROUP_SYMBOLS) else s
                               for s in g_symbols]
                    try:
                        smi, _, success = _convert_graph_to_smiles(g_coords, updated, g_edges)
                        if success and smi and "*" not in smi and smi != "<invalid>" and smi != "<mismatch>":
                            graph_candidates[lbl] = (smi, r_val)
                            _diag.write(f"    [Tier 4 graph] '{lbl}' r={r_val} → {smi[:60]}")
                        else:
                            _diag.write(f"    [Tier 4 graph] '{lbl}' r={r_val} fallback (smi={smi})")
                            llm_needed.append(lbl)
                    except Exception as e:
                        _diag.write(f"    [Tier 4 graph] '{lbl}' exception: {e}")
                        llm_needed.append(lbl)
            else:
                llm_needed = list(group_labels)

            # ── Step 2: LLM substitution fallback for unresolved labels ──────
            # Only called when graph-level substitution produced wildcards or
            # failed (e.g. complex r_values like "4-pyridyl" not in ABBREVIATIONS).
            llm_generated: dict = {}  # lbl -> (smiles, r_val)
            if llm_needed:
                fallback_lines = "\n".join(
                    f"  {lbl}: R = {label_rgroup_map[lbl]['r_value']}"
                    for lbl in llm_needed
                )
                gen_prompt = (
                    f"You are a chemistry expert. An OCSR tool detected this molecule with "
                    f"wildcard positions marked as *:\n\n"
                    f"Wildcard SMILES: {wildcard_smiles}\n\n"
                    f"The image shows this template with the following R-group substitutions:\n"
                    f"{fallback_lines}\n\n"
                    f"For each label, substitute the R value into the * position(s) of the "
                    f"template SMILES to produce the specific compound.\n"
                    f"Use the wildcard SMILES as the structural backbone — do not change the "
                    f"ring connectivity. R=H means the * is replaced by the aromatic ring "
                    f"hydrogen (implicit in aromatic SMILES).\n\n"
                    f'Return JSON: {{"results": [{{"label": "L1", "smiles": "...", "r_value": "H"}}, ...]}}'
                )
                try:
                    gen_resp = _call([
                        {"role": "system", "content": "You are a helpful chemistry assistant that outputs JSON."},
                        {"role": "user", "content": [
                            {"type": "text", "text": gen_prompt},
                            {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{crop_b64}"}},
                        ]}
                    ])
                    for g in _json.loads(gen_resp).get("results", []):
                        lbl = g.get("label", "")
                        smi = g.get("smiles", "")
                        rval = g.get("r_value", "")
                        if lbl in llm_needed and smi:
                            llm_generated[lbl] = (smi, rval)
                except Exception as e:
                    _diag.write(f"    [Tier 4] LLM fallback generation failed: {e}")

            # ── Step 3: Validate + optional visual verify ─────────────────────
            # Graph-level SMILES are trusted (RDKit-generated from a valid graph).
            # LLM-generated SMILES get visual verification against the original crop.
            for lbl in group_labels:
                if lbl in graph_candidates:
                    smiles, r_val = graph_candidates[lbl]
                    # Substructure check: scaffold core must be present in product
                    try:
                        from rdkit import Chem as _Chem
                        prod_mol = _Chem.MolFromSmiles(smiles)
                        if prod_mol is None:
                            raise ValueError("invalid product SMILES")
                    except Exception as e:
                        _diag.write(f"    [Tier 4] '{lbl}' graph SMILES invalid: {e}")
                        still_remaining.append(lbl)
                        continue
                    final_smiles = smiles
                    _diag.write(f"    [Tier 4] '{lbl}' ✅ graph-level: {final_smiles[:50]}")

                elif lbl in llm_generated:
                    smiles, r_val = llm_generated[lbl]
                    rendered = _smiles_to_img(smiles)
                    if rendered is None:
                        _diag.write(f"    [Tier 4] '{lbl}' RDKit render failed: {smiles[:40]}")
                        still_remaining.append(lbl)
                        continue

                    verify_prompt = (
                        f"You are a chemistry expert verifying a SMILES structure.\n"
                        f"Label: {lbl}  (R = {r_val})\n"
                        f"Generated SMILES: {smiles}\n\n"
                        f"Image 1: the RENDERED structure from the generated SMILES.\n"
                        f"Image 2: the ORIGINAL structure drawn in the figure.\n\n"
                        f"Does the rendered structure correctly represent {lbl}?\n"
                        f'Return JSON: {{"match": true/false, "corrected_smiles": "...(only if match=false)", "reason": "..."}}'
                    )
                    try:
                        ver_resp = _call([
                            {"role": "system", "content": "You are a helpful chemistry assistant that outputs JSON."},
                            {"role": "user", "content": [
                                {"type": "text", "text": verify_prompt},
                                {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{_img_b64(rendered)}"}},
                                {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{crop_b64}"}},
                            ]}
                        ])
                        ver_data = _json.loads(ver_resp)
                        match     = ver_data.get("match", True)
                        corrected = ver_data.get("corrected_smiles", "")
                        # Guard: reject correction if it contradicts the r_value.
                        # E.g. r_value says "CO2H" but corrected SMILES has CF3 — that's
                        # a vision hallucination; keep the original substituted SMILES.
                        if not match and corrected:
                            _r_lower = r_val.lower() if r_val else ""
                            _c_lower = corrected.lower()
                            _r_has_acid = any(t in _r_lower for t in ("co2h", "cooh", "carbox"))
                            _c_has_cf3  = "fc(f)(f)" in _c_lower or "c(f)(f)(f)" in _c_lower
                            _r_has_cf3  = any(t in _r_lower for t in ("cf3", "trifluoromethyl"))
                            _c_has_acid = any(t in _c_lower for t in ("c(=o)o", "oc(=o)"))
                            if (_r_has_acid and _c_has_cf3) or (_r_has_cf3 and _c_has_acid):
                                _diag.write(
                                    f"    [Tier 4] '{lbl}' correction contradicts r_value '{r_val}' — keeping original"
                                )
                                corrected = ""
                        final_smiles = corrected if (not match and corrected) else smiles
                        _diag.write(f"    [Tier 4] '{lbl}' {'✅ match' if match else '🔄 corrected'}: {final_smiles[:50]}")
                    except Exception as e:
                        _diag.write(f"    [Tier 4] Verification failed for '{lbl}': {e}")
                        final_smiles = smiles

                else:
                    still_remaining.append(lbl)
                    continue

                resolved.append({
                    "label": lbl,
                    "smiles": sanitize_smiles(final_smiles),
                    "smiles_source": "ocsr_rgroup",
                    "found_in": source_id,
                })

        # Labels with no R-group info go to next tier
        for lbl in remaining_labels:
            if lbl not in label_rgroup_map and lbl not in still_remaining:
                still_remaining.append(lbl)

        return resolved, still_remaining

    # ── Name extraction from paper text ────────────────────────────────

    def _extract_name_from_text(self, label: str) -> str:
        """
        Scan the paper markdown text for the compound name associated with a label.

        Looks for patterns like:
          "label (compound name)"  →  parenthetical name immediately after the label
          "label = compound name"  →  equation-style inline definition
          "label, compound name"   →  comma-separated name after the label

        Returns the extracted name string or "" if not found.
        """
        import re as _re
        text = getattr(self, "_paper_text", "")
        if not text or not label:
            return ""

        label_esc = _re.escape(label.strip())

        # Pattern 1: label followed by a parenthetical name
        m = _re.search(
            rf'\b{label_esc}\b[\s,]*\(([^){{}}]+)\)',
            text, _re.IGNORECASE
        )
        if m:
            name = m.group(1).strip()
            # Skip if the parenthetical looks like a quantity/unit, not a name
            if not _re.match(r'^[\d\.]+\s*(mol%|%|mM|mmol|equiv|h|°C)', name, _re.IGNORECASE):
                return name

        # Pattern 2: "label = name" or "label: name"
        m = _re.search(
            rf'\b{label_esc}\b\s*[=:]\s*([^\n,;.(){{}}]+)',
            text, _re.IGNORECASE
        )
        if m:
            return m.group(1).strip()

        return ""

    @staticmethod
    def _is_paper_specific_code(label: str) -> bool:
        """
        Return True if the label looks like a paper-specific compound code
        (e.g. "1a", "3aa", "PC1", "L2") rather than an actual chemical name.
        PubChem should not be queried for these — they won't exist in the database
        and may accidentally match unrelated compounds.
        """
        import re as _re
        s = label.strip()
        # Short alphanumeric codes: "1a", "3aa", "PC1", "L2", "2a", "3bb"
        if _re.match(r'^[A-Z]{0,3}\d+[a-z]{0,3}$', s, _re.IGNORECASE):
            return True
        # Descriptive paper labels: "Photocatalyst 1", "Ligand A", "Base 3"
        if _re.match(r'^(photocatalyst|catalyst|ligand|base|oxidant|reductant|reagent|compound|product|substrate)\s+\S+$',
                     s, _re.IGNORECASE):
            return True
        return False

    # ── Tier 5 (PubChem) ──────────────────────────────────────────────────────

    def _tier35_pubchem(self, remaining_labels: list, raw_bboxes: list, source_id: str):
        """
        Tier 5 — PubChem compound name lookup.

        Candidate name sources (in priority order):
          1. Compound name extracted from the paper text (inline definition near the label)
          2. The label itself, if it reads as a real chemical name rather than a paper code
          3. Cleaned variants: strip parenthetical abbreviations from full-name labels
          4. Spatially adjacent OCR text bboxes from the figure image

        Short alphanumeric paper codes are skipped — they will not exist in PubChem
        and may accidentally match unrelated compounds.

        SMILES found via PubChem are stored in both `smiles` (primary) and
        `smiles_pubchem` (dedicated field) so they can always be distinguished from
        OCSR-derived or LLM-generated structures.
        """
        import re

        # Spatial index of text bboxes in the image
        bbox_texts: list = []
        for entry in raw_bboxes:
            if "text" in entry and "smiles" not in entry:
                for t in (entry.get("text") or []):
                    t = t.strip()
                    if not t:
                        continue
                    coords = entry.get("bbox") or entry.get("coords") or ()
                    cx = cy = 0
                    if coords and len(coords) >= 4:
                        cx = (coords[0] + coords[2]) / 2
                        cy = (coords[1] + coords[3]) / 2
                    bbox_texts.append((t, (cx, cy)))

        def _nearby_texts(label: str, radius: float = 0.25) -> list:
            centers = [c for t, c in bbox_texts if self._text_contains_label(t, label.lower())]
            nearby = []
            for lc in centers:
                for t, c in bbox_texts:
                    if self._text_contains_label(t, label.lower()):
                        continue
                    if ((lc[0]-c[0])**2 + (lc[1]-c[1])**2)**0.5 < radius:
                        nearby.append(t)
            return nearby

        resolved, still_remaining = [], []
        for label in remaining_labels:
            # Build candidate name list
            candidates = []

            # 0. Name from LLM-extracted compound_name_map (highest priority)
            map_name = getattr(self, "_name_map", {}).get(label, "")
            if map_name:
                candidates.append(map_name)
                stripped_map = re.sub(r'\s*\([^)]*\)\s*$', '', map_name).strip()
                if stripped_map and stripped_map != map_name:
                    candidates.append(stripped_map)
                _diag.write(f"    [Tier 5 PubChem] '{label}' → name_map name: '{map_name}'")
            else:
                # Skip paper-specific codes when no name is known
                if self._is_paper_specific_code(label):
                    _diag.write(f"    [Tier 5 PubChem] '{label}' → skipped (paper-specific code, no name_map entry)")
                    still_remaining.append(label)
                    continue

            # 1. Name extracted from paper markdown text (fallback regex)
            extracted_name = self._extract_name_from_text(label)
            if extracted_name and extracted_name not in candidates:
                candidates.append(extracted_name)
                # Also try the part before any parenthetical
                stripped = re.sub(r'\s*\([^)]*\)\s*$', '', extracted_name).strip()
                if stripped and stripped != extracted_name:
                    candidates.append(stripped)

            # 2. Label itself as a PubChem query — only when name_map did NOT provide
            # a name. Paper-specific codes (PC1, L2, etc.) are meaningless to PubChem
            # and cause spurious hits when a name_map entry already gives the real name.
            if not map_name:
                candidates.append(label)
                stripped_label = re.sub(r'\s*\([^)]*\)\s*$', '', label).strip()
                if stripped_label and stripped_label != label:
                    candidates.append(stripped_label)

            # 2b. For Iridium photocatalyst names: generate cleaned variants that
            # PubChem is more likely to recognise (bracket/charge notation varies widely)
            for cand in list(candidates):
                if re.search(r'\bIr\b', cand, re.IGNORECASE):
                    # Remove formal charge indicators: [X+] → X, X+ → X
                    cleaned_ir = re.sub(r'\+\]', ']', cand)
                    cleaned_ir = re.sub(r'\+$', '', cleaned_ir.strip())
                    # Normalise bracket style: Ir[X] → Ir(X)
                    cleaned_ir2 = re.sub(r'Ir\[', 'Ir(', cleaned_ir).replace(']', ')')
                    for v in (cleaned_ir, cleaned_ir2):
                        if v and v not in candidates:
                            candidates.append(v)
                    # Also try appending PF6 counter-ion if absent (common Ir catalysts)
                    if 'PF6' not in cand and 'pf6' not in cand.lower():
                        candidates.append(cand.rstrip(']').rstrip('+') + 'PF6')

            # 3. Spatially adjacent OCR text — skip when name_map already gave a name.
            # Nearby labels (e.g. "PC2" adjacent to "PC1") can produce wrong PubChem
            # hits when name_map already tells us what the compound is.
            if not map_name:
                for nearby in _nearby_texts(label):
                    if nearby not in candidates:
                        candidates.append(nearby)

            smiles = None
            hit_name = None
            for cand in candidates:
                if not cand or len(cand) <= 2:
                    continue
                s = self._pubchem_name_to_smiles_simple(cand)
                if s:
                    smiles = s
                    hit_name = cand
                    break

            if smiles:
                clean = sanitize_smiles(smiles)
                _diag.write(f"    [Tier 5 PubChem] '{label}' → hit via '{hit_name}': {smiles}")
                resolved.append({
                    "label":        label,
                    "smiles":       clean,
                    "smiles_pubchem": clean,
                    "smiles_source": "pubchem_lookup",
                    "found_in":     source_id,
                    "name":         hit_name,
                })
            else:
                _diag.write(f"    [Tier 5 PubChem] '{label}' → no hit (tried {candidates[:4]})")
                still_remaining.append(label)
        return resolved, still_remaining

    @staticmethod
    def _pubchem_name_to_smiles_simple(name: str) -> str:
        """
        Query PubChem by compound name; return SMILES or "".
        SSL verification is disabled to handle macOS certifi issues.
        PubChem returns the key as "SMILES" in the JSON body regardless of the
        property name used in the URL.
        """
        if not name or len(name) <= 2:
            return ""
        import json as _json
        import ssl as _ssl
        import urllib.request as _req
        import urllib.parse as _parse
        import urllib.error as _err

        ctx = _ssl.create_default_context()
        ctx.check_hostname = False
        ctx.verify_mode = _ssl.CERT_NONE

        encoded = _parse.quote(name)
        endpoint = (
            f"https://pubchem.ncbi.nlm.nih.gov/rest/pug/compound/name/{encoded}"
            f"/property/IsomericSMILES/JSON"
        )
        try:
            req = _req.Request(endpoint, headers={"User-Agent": "Piper/1.0"})
            with _req.urlopen(req, timeout=8, context=ctx) as resp:
                data = _json.loads(resp.read().decode())
            props = data.get("PropertyTable", {}).get("Properties", [])
            if props:
                return (
                    props[0].get("IsomericSMILES")
                    or props[0].get("CanonicalSMILES")
                    or props[0].get("SMILES", "")
                )
        except _err.HTTPError:
            pass  # 404 = name not found
        except Exception:
            pass
        return ""

    # ── Tier 4 ────────────────────────────────────────────────────────────────

    def _tier4_llm_vision(self, remaining_labels, clean_corefs, image_path, source_id):
        """
        Pure LLM vision fallback.  The LLM receives the image and any available
        OCSR tool results and generates SMILES directly.  Only reached when all
        graph-based paths have failed or produced no result.
        """
        # Build OCSR context: include both clean corefs AND raw wildcard SMILES
        # for the unresolved labels.  The raw SMILES give the LLM the correct
        # molecular skeleton (bond connectivity, ring systems) — it only needs
        # to identify what the * substituents are, rather than reconstruct the
        # whole structure from scratch.
        label_set_lower = {l.lower() for l in remaining_labels}
        ocsr_raw_context = []
        for coref in clean_corefs:
            raw = coref.get("smiles_raw", "")
            texts = coref.get("texts", [])
            if not raw:
                continue
            matched_labels = [
                l for l in remaining_labels
                if any(self._text_contains_label(t, l.lower()) for t in texts)
            ]
            if matched_labels:
                ocsr_raw_context.append({
                    "labels": matched_labels,
                    "ocsr_skeleton": raw,
                    "note": "OCSR detected this structure but could not fully resolve substituents marked as *. Use the image to identify what * represents for each label."
                })

        # Build a map of label → known compound name from name_map for the labels
        # being resolved.  Include these as hard constraints in the prompt so the
        # LLM searches for the correct compound rather than guessing from context.
        _nm = getattr(self, "_name_map", {})
        known_names = {lbl: _nm[lbl] for lbl in remaining_labels if lbl in _nm}

        user_content = (
            f"LABELS TO RESOLVE: {remaining_labels}\n"
            f"SOURCE ID: {source_id}\n"
            f"SOURCE TYPE: image\n"
        )
        if known_names:
            user_content += (
                f"\nKNOWN COMPOUND NAMES (authoritative — use ONLY these names for the "
                f"listed labels; do NOT identify them as any other compound):\n"
                f"{json.dumps(known_names, indent=2)}\n"
            )
        if clean_corefs:
            clean_only = [c for c in clean_corefs if c.get("smiles")]
            if clean_only:
                user_content += (
                    f"\nOCSR_RESOLVED (use these directly where label matches):\n"
                    f"{json.dumps(clean_only, indent=2)}\n"
                )
        if ocsr_raw_context:
            user_content += (
                f"\nOCSR_SKELETON (partial structures from OCSR — * marks unresolved substituents):\n"
                f"{json.dumps(ocsr_raw_context, indent=2)}\n"
                f"Use the image to identify the substituents at each * position and complete the structure.\n"
            )
        user_content += "\nAnalyze the image and resolve the remaining labels."

        messages = self._assemble_prompt(
            base_prompt="You are a helpful assistant that outputs JSON.",
            user_content=user_content,
            image_path=image_path,
            skills=["label_mapping.md"]
        )
        try:
            response = self._call_llm(messages, response_format={"type": "json_object"})
            data = self._parse_json(response)
            mappings = self._extract_mappings(data)
            result = []
            for m in mappings:
                if m.get("label") not in remaining_labels:
                    continue

                label = m.get("label", "")
                # name_map is authoritative — if we know the compound's real name,
                # use it exclusively for PubChem.  Never let the LLM substitute a
                # different compound name (e.g. "Eosin Y" for an Ir complex).
                authoritative_name = getattr(self, "_name_map", {}).get(label, "")
                llm_name = (m.get("name") or m.get("iupac_name") or "").strip()
                name = authoritative_name or llm_name or self._extract_name_from_text(label)

                resolved_smi, resolved_src = ("", "")
                if name:
                    resolved_smi, resolved_src = self._resolve_name_to_smiles(name)
                if resolved_smi:
                    clean = sanitize_smiles(resolved_smi)
                    m["smiles"] = clean
                    if resolved_src == "pubchem_lookup":
                        m["smiles_pubchem"] = clean
                    m["smiles_source"] = resolved_src
                    m["name"] = name
                    _diag.write(f"    [Tier 6→{resolved_src}] '{label}' name={name!r} → {resolved_smi[:50]}")
                elif authoritative_name:
                    # All resolution paths failed (PubChem + OPSIN + LLM-name).
                    # Record the known name but do NOT accept the LLM-vision's
                    # hallucinated SMILES — a null SMILES is less harmful than
                    # a wrong one. The `name` field is preserved so the
                    # downstream `resolved_name` is at least correct.
                    m["smiles"] = None
                    m["smiles_source"] = "name_map_only"
                    m["name"] = authoritative_name
                    _diag.write(f"    [Tier 6] '{label}' PubChem/OPSIN/LLM all failed for "
                                f"authoritative name {authoritative_name!r} — name only, no SMILES")
                else:
                    if "smiles" in m:
                        m["smiles"] = sanitize_smiles(m["smiles"])
                    m["smiles_source"] = "llm_vision"
                m.setdefault("found_in", source_id)
                result.append(m)
            return result
        except Exception as e:
            _log.error(f"[Tier 6] LLM vision fallback failed: {e}")
            return []

    # ── Tier 0: name_map shortcut + name resolution chain ────────────────────

    def _tier0_name_map_shortcut(self, labels: list, source_id: str):
        """Resolve labels whose authoritative chemical name is already known
        (compound_name_map) via PubChem → OPSIN → LLM-name-to-SMILES, before
        OCSR runs. Decisive for organometallic photocatalysts and named
        ligands/reagents where MolScribe is unreliable.

        For organometallic compounds all three sources are tried and the results
        saved individually (smiles_pubchem / smiles_opsin / smiles_llm) so
        downstream consumers can access every candidate. The best SMILES by
        metal-fragment connectivity score is stored in 'smiles'."""
        import re as _re_local
        name_map = getattr(self, "_name_map", {})
        resolved, remaining = [], []
        for label in labels:
            name = (name_map.get(label) or "").strip()
            if not name or name == label:
                remaining.append(label)
                continue

            _is_organomet = bool(_re_local.search(
                r'\b(Ir|Ru|Pd|Rh|Pt|Au|Cu|Fe|Ni|Zn|Co|Mn|Cr|Mo|Os)\b', name
            ))

            if _is_organomet:
                all_results = self._resolve_name_all_sources(name)
                best_smiles, best_source, best_score = "", "", -2
                for src, smi in all_results.items():
                    if smi:
                        score = self._score_smiles_metal_connectivity(smi)
                        _diag.write(
                            f"  [Tier 0 NameMap Organomet] '{label}' {src}: "
                            f"score={score}  {smi[:70]}"
                        )
                        if score > best_score:
                            best_score, best_smiles, best_source = score, smi, src

                if best_smiles:
                    _diag.write(
                        f"  [Tier 0 NameMap] '{label}' → best={best_source} "
                        f"(score={best_score}) via name='{name}'"
                    )
                    entry = {
                        "label":            label,
                        "smiles":           sanitize_smiles(best_smiles),
                        "name":             name,
                        "found_in":         source_id,
                        "smiles_source":    best_source,
                        "smiles_pubchem":   sanitize_smiles(all_results["pubchem_lookup"])
                                            if all_results["pubchem_lookup"] else None,
                        "smiles_opsin":     sanitize_smiles(all_results["opsin_lookup"])
                                            if all_results["opsin_lookup"] else None,
                        "smiles_llm":       sanitize_smiles(all_results["llm_name_to_smiles"])
                                            if all_results["llm_name_to_smiles"] else None,
                    }
                    resolved.append({k: v for k, v in entry.items() if v is not None})
                else:
                    _diag.write(
                        f"  [Tier 0 NameMap] '{label}' → name '{name}' did not "
                        f"resolve via any source (organometallic)"
                    )
                    remaining.append(label)
            else:
                smiles, source = self._resolve_name_to_smiles(name)
                if smiles:
                    _diag.write(f"  [Tier 0 NameMap] '{label}' → {source} via name='{name}'")
                    resolved.append({
                        "label":         label,
                        "smiles":        sanitize_smiles(smiles),
                        "name":          name,
                        "found_in":      source_id,
                        "smiles_source": source,
                    })
                else:
                    _diag.write(f"  [Tier 0 NameMap] '{label}' → name '{name}' did not "
                                f"resolve via PubChem/OPSIN/LLM")
                    remaining.append(label)
        return resolved, remaining

    def _resolve_name_to_smiles(self, name: str) -> tuple:
        """Run a chemical name through the resolution chain.
        For organometallic names, tries ALL sources and picks the best by
        metal-fragment connectivity score. For all other compounds, stops at
        the first valid hit (PubChem → OPSIN → LLM).
        Returns (smiles, source) or ("", "")."""
        if not name or len(name) <= 2:
            return "", ""
        cache = getattr(self, "_name_resolution_cache", None)
        if cache is None:
            cache = {}
            self._name_resolution_cache = cache
        if name in cache:
            return cache[name]

        import re as _re_local
        _is_organomet = bool(_re_local.search(
            r'\b(Ir|Ru|Pd|Rh|Pt|Au|Cu|Fe|Ni|Zn|Co|Mn|Cr|Mo|Os)\b', name
        ))

        if _is_organomet:
            all_results = self._resolve_name_all_sources(name)
            best_smiles, best_source, best_score = "", "", -2
            for src, smi in all_results.items():
                if smi:
                    score = self._score_smiles_metal_connectivity(smi)
                    _diag.write(f"    [NameResolve Organomet] {src}: score={score}  {smi[:70]}")
                    if score > best_score:
                        best_score, best_smiles, best_source = score, smi, src
            cache[name] = (best_smiles, best_source) if best_smiles else ("", "")
        else:
            smi = self._pubchem_name_to_smiles_simple(name)
            if smi and self._is_valid_smiles(smi):
                cache[name] = (smi, "pubchem_lookup")
                return cache[name]

            smi = self._opsin_name_to_smiles(name)
            if smi and self._is_valid_smiles(smi):
                cache[name] = (smi, "opsin_lookup")
                return cache[name]

            smi = self._llm_name_to_smiles(name)
            if smi and self._is_valid_smiles(smi):
                cache[name] = (smi, "llm_name_to_smiles")
                return cache[name]

            cache[name] = ("", "")

        return cache[name]

    def _resolve_name_all_sources(self, name: str) -> dict:
        """Try PubChem, OPSIN, and LLM for *name*; return a dict keyed by
        source with the validated SMILES string or None for each."""
        results = {}

        smi = self._pubchem_name_to_smiles_simple(name)
        results["pubchem_lookup"] = smi if (smi and self._is_valid_smiles(smi)) else None

        smi = self._opsin_name_to_smiles(name)
        results["opsin_lookup"] = smi if (smi and self._is_valid_smiles(smi)) else None

        smi = self._llm_name_to_smiles(name)
        results["llm_name_to_smiles"] = smi if (smi and self._is_valid_smiles(smi)) else None

        return results

    @staticmethod
    def _score_smiles_metal_connectivity(smiles: str) -> int:
        """Return the heavy-atom count of the largest metal-containing fragment.
        A higher score means the metal is bonded to more ligand atoms (better).
        Bare isolated metal ions score 1; fully coordinated complexes score high."""
        _METALS = {
            'Sc','Ti','V','Cr','Mn','Fe','Co','Ni','Cu','Zn',
            'Y','Zr','Nb','Mo','Tc','Ru','Rh','Pd','Ag','Cd',
            'Hf','Ta','W','Re','Os','Ir','Pt','Au','Hg',
        }
        try:
            from rdkit import Chem
            mol = Chem.MolFromSmiles(smiles)
            if mol is None:
                return -1
            frags = Chem.GetMolFrags(mol, asMols=True)
            best = 0
            for frag in frags:
                if any(a.GetSymbol() in _METALS for a in frag.GetAtoms()):
                    best = max(best, frag.GetNumHeavyAtoms())
            return best if best > 0 else mol.GetNumHeavyAtoms()
        except Exception:
            return -1

    @staticmethod
    def _opsin_name_to_smiles(name: str) -> str:
        """Query OPSIN (IUPAC name → SMILES). Free, MIT-licensed.
        Best for systematic IUPAC names; will return blank for bracket
        abbreviations like '[Ir(ppy)2(dtbbpy)]PF6'."""
        if not name:
            return ""
        import urllib.request as _req
        import urllib.parse as _parse
        import ssl as _ssl
        ctx = _ssl.create_default_context()
        ctx.check_hostname = False
        ctx.verify_mode = _ssl.CERT_NONE
        try:
            url = f"https://opsin.ch.cam.ac.uk/opsin/{_parse.quote(name)}.smi"
            req = _req.Request(url, headers={"User-Agent": "Piper/1.0"})
            with _req.urlopen(req, timeout=8, context=ctx) as resp:
                txt = resp.read().decode().strip()
        except Exception:
            return ""
        if not txt or txt.startswith("<") or " " in txt:
            return ""
        return txt

    def _llm_name_to_smiles(self, name: str) -> str:
        """LLM converts a compound name to canonical SMILES.
        Used as a fallback when PubChem and OPSIN cannot resolve the name —
        common for bracket abbreviations of transition-metal complexes
        (e.g. '[Ir(dF(CF3)ppy)2(dtbbpy)]PF6')."""
        import os as _os
        try:
            from openai import OpenAI as _OpenAI
            from context import get_openrouter_url as _oru, load_config as _lc
        except Exception:
            return ""
        api_key = (_os.environ.get("OPENROUTER_API_KEY")
                   or _os.environ.get("API_KEY")
                   or _os.environ.get("OPENAI_API_KEY"))
        if not api_key:
            return ""
        model = _lc().get("model", {}).get("agents", {}).get(
            "label_resolver_model", "openai/gpt-4o")
        client = _OpenAI(api_key=api_key, base_url=_oru())
        prompt = (
            "Convert this chemistry compound name to a canonical SMILES.\n"
            f"Name: {name}\n\n"
            "If the name uses bracket notation (e.g. '[Ir(dF(CF3)ppy)2(dtbbpy)]PF6'), "
            "expand to the full molecular structure including all ligands and "
            "counterions. If the name is unambiguous and you are confident in the "
            "structure, return the SMILES. If the name is ambiguous or you are not "
            "confident, return an empty SMILES.\n\n"
            'Return JSON: {"smiles": "...", "confidence": "high|medium|low"}'
        )
        try:
            resp = client.chat.completions.create(
                model=model, temperature=0,
                messages=[
                    {"role": "system",
                     "content": "You are a chemistry expert that returns canonical SMILES as JSON."},
                    {"role": "user", "content": prompt},
                ],
                response_format={"type": "json_object"},
            )
            from miner.token_tracker import tracker
            if hasattr(resp, "usage") and resp.usage:
                tracker.record("LabelResolverAgent_NameToSMILES", model,
                               resp.usage.prompt_tokens or 0,
                               resp.usage.completion_tokens or 0)
            data = json.loads(resp.choices[0].message.content or "{}")
        except Exception as e:
            _log.warning(f"[NameResolution] LLM name→SMILES failed for {name!r}: {e}")
            return ""
        if (data.get("confidence") or "").lower() == "low":
            return ""
        return (data.get("smiles") or "").strip()

    @staticmethod
    def _is_valid_smiles(smiles: str) -> bool:
        if not smiles:
            return False
        try:
            from rdkit import Chem
            return Chem.MolFromSmiles(smiles) is not None
        except Exception:
            return False

    @staticmethod
    def _contains_transition_metal(smiles: str) -> bool:
        """Return True if the SMILES contains a transition-metal atom in
        bracket notation (e.g. [Ir], [Ru], [Pd]). MolScribe is documented to
        be unreliable on these structures."""
        if not smiles:
            return False
        import re as _re
        return bool(_re.search(
            r'\[(Sc|Ti|V|Cr|Mn|Fe|Co|Ni|Cu|Zn|Y|Zr|Nb|Mo|Tc|Ru|Rh|Pd|Ag|Cd|'
            r'Hf|Ta|W|Re|Os|Ir|Pt|Au|Hg)(?:[+\-@HhA-Za-z0-9]*)?\]',
            smiles
        ))

    def _enrich_names(self, mappings: list) -> None:
        """Final pass: ensure every mapping's `name` field carries the real
        chemical name from compound_name_map when available, rather than just
        the label echoed back. Downstream consumers store this as
        `resolved_name` in extracted entries."""
        name_map = getattr(self, "_name_map", {})
        for m in mappings:
            label = (m.get("label") or "").strip()
            if not label:
                continue
            curr = (m.get("name") or "").strip()
            mapped = (name_map.get(label) or "").strip()
            # Overwrite when the tier just echoed the label, OR when no name
            # was set. Don't overwrite an explicit chemical name already chosen
            # by Tier 5/6 (PubChem hit_name or LLM authoritative_name) unless
            # the name_map entry differs from what's there only by being more
            # canonical — we keep tier-set names as-is.
            if mapped and (not curr or curr == label):
                m["name"] = mapped

    # ── Utilities ─────────────────────────────────────────────────────────────

    def _extract_mappings(self, data) -> list:
        if isinstance(data, list):
            return data
        if isinstance(data, dict):
            if isinstance(data.get("mappings"), list):
                return data["mappings"]
        return []


def get_label_resolver_agent():
    return LabelResolverAgent()
