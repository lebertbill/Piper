"""
KGEnrichmentAgent — enriches a built reaction network graph in-place.

Five enrichment passes per article:
  A. PubChem Name Agent        — fill missing compound names via PubChem API
  B. LLM Name Resolution Agent — identify real names for paper-label-only compounds
  C. Context Verification Agent — cross-check extracted entries vs article markdown
  D. Edge Correction Agent      — correct wrong role/edge assignments using LLM
  E. Concept Extraction Agent   — extract article-level transformation/catalyst concept

Usage:
    agent = KGEnrichmentAgent(G, data_root="extracted_data", model_name="openai/gpt-4o-mini")
    stats = asyncio.run(agent.enrich())
"""
from __future__ import annotations

import asyncio
import json
import os
import urllib.parse
from pathlib import Path
from typing import Callable, Optional

import httpx
import networkx as nx


# ── LLM + PubChem helpers ─────────────────────────────────────────────────────

async def _call_llm(prompt: str, model: str) -> str:
    import sys, os as _os
    sys.path.insert(0, str(Path(__file__).parent.parent))
    api_key = _os.getenv("OPENROUTER_API_KEY")
    url = "https://openrouter.ai/api/v1/chat/completions"
    headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}
    payload = {"model": model, "messages": [{"role": "user", "content": prompt}]}
    async with httpx.AsyncClient(timeout=120) as client:
        r = await client.post(url, headers=headers, json=payload)
        r.raise_for_status()
        return r.json()["choices"][0]["message"]["content"]


async def _pubchem_by_name(name: str) -> tuple[str | None, str | None]:
    """Look up SMILES and canonical name by compound name via PubChem."""
    encoded = urllib.parse.quote(name, safe="")
    url = (
        f"https://pubchem.ncbi.nlm.nih.gov/rest/pug/compound/name/"
        f"{encoded}/property/IsomericSMILES,IUPACName,Title/JSON"
    )
    try:
        async with httpx.AsyncClient(timeout=30) as client:
            r = await client.get(url)
            if r.status_code == 200:
                props = (r.json().get("PropertyTable", {})
                         .get("Properties", [{}])[0])
                smiles = props.get("IsomericSMILES")
                canon  = props.get("Title") or props.get("IUPACName")
                return smiles, canon
    except Exception:
        pass
    return None, None


async def _pubchem_name(smiles: str) -> Optional[str]:
    encoded = urllib.parse.quote(smiles, safe="")
    url = (
        f"https://pubchem.ncbi.nlm.nih.gov/rest/pug/compound/smiles/"
        f"{encoded}/property/IUPACName,Title/JSON"
    )
    try:
        async with httpx.AsyncClient(timeout=30) as client:
            r = await client.get(url)
            if r.status_code == 200:
                props = (
                    r.json()
                    .get("PropertyTable", {})
                    .get("Properties", [{}])[0]
                )
                return props.get("Title") or props.get("IUPACName")
    except Exception:
        pass
    return None


# ── Prompt templates ──────────────────────────────────────────────────────────

_VERIFY_PROMPT = """\
You are verifying chemistry reaction data extracted from a paper against the original paper text.

Paper: {paper}
Relevant paper text:
{text_excerpt}

Extracted reaction entries:
{entries_json}

For each entry, do ALL of the following:
1. Check catalyst/reagent/solvent names against the paper text (skip <smiles:...> entries — those are SMILES-only).
2. Check whether yields match the paper text.
3. Flag any obvious role errors (e.g., base listed as catalyst).
4. For any compound shown as <smiles:...> (no name resolved), look in the paper text and try to identify its name.
   If found, include "resolved_names": {{"<smiles_prefix>": "compound name"}} in that entry's result.

Return a JSON array — one object per entry:
[{{
  "entry_id": "1",
  "verified": true,
  "note": "",
  "resolved_names": {{}}
}}, ...]
Only flag genuine issues. If everything looks correct, set verified=true and note="".
"""

_NAME_RESOLUTION_PROMPT = """\
You are a chemistry expert. A reaction table has been extracted from a paper. \
Some compounds are only identified by paper-internal labels (like "Photocatalyst 2", \
"Catalyst A", "Base 1", "1a", "2b") rather than real chemical names.

Paper: {paper}
Paper text excerpt:
{text_excerpt}

Compounds to resolve (each has a SMILES and current aliases):
{compounds_json}

For each compound:
1. Decide if all its aliases are paper-internal labels (e.g. "Photocatalyst 2", "PC1", "1a").
2. If yes — use the SMILES structure and paper context to identify the real compound name.
3. If a real name is already present in aliases, confirm it.

Return a JSON array:
[{{"smiles": "...", "resolved_name": "real compound name or empty string if unknown"}}]
Only return an entry if you can provide a meaningful name. Do not guess — leave resolved_name \
empty if you are not confident.
"""

_EDGE_CORRECTION_PROMPT = """\
You are a chemistry expert reviewing role assignments for compounds in a reaction table.
The extraction system assigned roles (catalyst, reagent, base, solvent, reactant, product) \
but some may be wrong.

Paper: {paper}
Paper text:
{text_excerpt}

Reaction entries with current assignments:
{entries_json}

Valid roles: catalyst, photocatalyst, metal_salt, co-catalyst, ligand, base, oxidant, \
reductant, reagent, additive, solvent, reactant, product

Review EACH compound. Flag corrections only when genuinely wrong. Common errors:
- "reagent" when the paper calls it a "base" or "oxidant"
- "catalyst" when used stoichiometrically (>50 mol%) — likely a "reagent"
- "metal_salt" / "ligand" confused with "catalyst"
- "reactant" and "product" swapped
- Solvent used in tiny amount → "additive" instead

Return JSON array (empty [] if nothing needs correcting):
[{{
  "entry_id": "1",
  "smiles": "exact SMILES from input",
  "current_role": "reagent",
  "correct_role": "base",
  "reason": "paper says it acts as a Brønsted base"
}}]
"""

_CONCEPT_PROMPT = """\
Read this chemistry paper excerpt and extract the key scientific information.

Paper title: {paper}
Text: {text}

Return JSON:
{{"transformation": "primary chemical transformation (e.g. C-H arylation)", \
"catalyst_type": "key catalyst (e.g. Ir/Ni dual photoredox)", \
"substrate_class": "primary substrate class (e.g. aryl halides)", \
"application": "brief application description (1 sentence)"}}
"""

_TABLE_SUMMARY_PROMPT = """\
You are a chemistry expert. Below is a reaction table extracted from a paper, \
along with the article text for context.

Paper: {paper}
Table name: {table_name}
Article text excerpt:
{text_excerpt}

Reaction entries in this table:
{entries_summary}

Summarise what this table is investigating. Be concise (2–3 sentences). Include:
1. What variable(s) are being screened (e.g. solvent, photocatalyst, base, temperature)
2. The best result (highest yield entry) and which conditions gave it
3. Any clear trend or conclusion the table demonstrates

Return JSON:
{{"purpose": "what is being screened / optimised", \
"best_entry": "entry id and conditions giving highest yield", \
"conclusion": "main finding or trend", \
"summary": "2-3 sentence plain-English summary"}}
"""


# ── Agent ─────────────────────────────────────────────────────────────────────

class KGEnrichmentAgent:
    """Enriches a NetworkX MultiDiGraph reaction network in-place."""

    NAME = "KG Enrichment Agent"

    # Sub-agent identities
    PUBCHEM_AGENT         = "PubChem Name Agent"
    OPENCLATURA_AGENT     = "Openclatura IUPAC Agent"
    NAME_LLM_AGENT        = "LLM Name Resolution Agent"
    VERIFY_AGENT          = "Context Verification Agent"
    EDGE_AGENT            = "Edge Correction Agent"
    CONCEPT_AGENT         = "Concept Extraction Agent"
    TABLE_SUMMARY_AGENT   = "Table Summary Agent"

    def __init__(
        self,
        G: nx.MultiDiGraph,
        data_root: str | Path,
        model_name: str,
        run_preference: str = "",
    ) -> None:
        self.G = G
        self.data_root = Path(data_root)
        self.model_name = model_name
        self.run_preference = run_preference

    # ── Public API ─────────────────────────────────────────────────────────────

    async def enrich(self, progress_callback: Optional[Callable[[str], None]] = None,
                      force: bool = False) -> dict:
        """
        Enrich all articles. Returns summary stats dict.

        Resumable: each Article node is stamped enriched=True (+ enriched_at
        timestamp) once its enrichment pass completes, and the graph is saved
        after every article not just at the end so an interrupted run
        (closed tab, error, etc.) doesn't lose prior progress. Subsequent runs
        skip nodes already marked enriched=True unless force=True.
        """
        import time as _time

        articles = [
            (node, data["title"])
            for node, data in self.G.nodes(data=True)
            if data.get("node_type") == "Article"
        ]
        # De-dupe by title in case of stray duplicate nodes, keep first occurrence
        seen_titles = set()
        deduped = []
        for node, title in articles:
            if title in seen_titles:
                continue
            seen_titles.add(title)
            deduped.append((node, title))
        articles = deduped

        total = dict(n_articles=0, n_skipped_already_enriched=0,
                     n_names_filled=0, n_iupac_names_filled=0,
                     n_entries_verified=0, n_concepts_added=0, n_tables_summarised=0)

        for node, paper in articles:
            if not force and self.G.nodes[node].get("enriched"):
                total["n_skipped_already_enriched"] += 1
                if progress_callback:
                    progress_callback(f"Skipping (already enriched): {paper}")
                continue

            if progress_callback:
                progress_callback(f"Enriching: {paper}")
            result = await self._enrich_article(paper, cb=progress_callback)
            total["n_articles"] += 1
            total["n_names_filled"] += result.get("names_filled", 0)
            total["n_iupac_names_filled"] += result.get("iupac_names_filled", 0)
            total["n_entries_verified"] += result.get("entries_verified", 0)
            total["n_concepts_added"] += result.get("concept_added", 0)
            total["n_tables_summarised"] += result.get("tables_summarised", 0)

            self.G.nodes[node]["enriched"] = True
            self.G.nodes[node]["enriched_at"] = _time.strftime("%Y-%m-%d %H:%M:%S")
            self._save_graph()  # incremental save — survives interruption

        return total

    # ── Per-article orchestration ──────────────────────────────────────────────

    async def _enrich_article(self, paper: str, cb: Optional[Callable] = None) -> dict:
        md_path = self._find_markdown(paper)
        md_text = md_path.read_text(encoding="utf-8", errors="replace") if md_path else ""

        if cb:
            cb(f"  [{self.PUBCHEM_AGENT}] filling compound names via PubChem…")
        names_filled = await self._fill_compound_names(paper)

        if cb:
            cb(f"  [{self.OPENCLATURA_AGENT}] generating IUPAC names for unnamed compounds…")
        iupac_names_filled = self._fill_names_with_openclatura(paper)

        entries_verified = 0
        concept_added = 0
        tables_summarised = 0

        if md_text:
            if cb:
                cb(f"  [{self.NAME_LLM_AGENT}] resolving paper-label compounds from article context…")
            await self._resolve_names_with_llm(paper, md_text)
            if cb:
                cb(f"  [{self.VERIFY_AGENT}] cross-checking entries against article text…")
            entries_verified = await self._verify_entries(paper, md_text)
            if cb:
                cb(f"  [{self.EDGE_AGENT}] correcting edge directions and role assignments…")
            await self._correct_edges_with_llm(paper, md_text)
            if cb:
                cb(f"  [{self.CONCEPT_AGENT}] extracting article concept…")
            concept_added = int(await self._add_concept_node(paper, md_text))
            if cb:
                cb(f"  [{self.TABLE_SUMMARY_AGENT}] summarising each table…")
            tables_summarised = await self._summarise_tables(paper, md_text)
        else:
            if cb:
                cb(f"  No markdown found for {paper} — skipping LLM agents")

        return {
            "names_filled": names_filled,
            "iupac_names_filled": iupac_names_filled,
            "entries_verified": entries_verified,
            "concept_added": concept_added,
            "tables_summarised": tables_summarised,
        }

    # ── A0. Openclatura IUPAC name filling ────────────────────────────────────

    def _fill_names_with_openclatura(self, paper: str) -> int:
        """Generate IUPAC names via openclatura for compounds that PubChem could not name.

        Runs synchronously (openclatura is CPU-bound, no I/O).
        Skips compounds that already have a pubchem_name or iupac_name.
        Skips text-only nodes (no smiles_canonical) and organometallics that
        openclatura cannot handle (r.ok == False).

        Stores result as iupac_name on the node and appends it to aliases.
        """
        try:
            from openclatura import name as _oc_name
        except ImportError:
            print("[OpenclaturaAgent] openclatura not installed — skipping IUPAC pass.")
            return 0

        from .utils import _is_paper_label

        count = 0
        for node, data in self.G.nodes(data=True):
            if data.get("node_type") != "Compound":
                continue
            if paper not in data.get("papers", []):
                continue
            # Skip if already named by PubChem or a previous openclatura run
            if data.get("pubchem_name") or data.get("iupac_name"):
                continue
            # Skip text-only nodes (no SMILES)
            smiles = data.get("smiles_canonical", "")
            if not smiles:
                continue
            # Skip if aliases already contain a real (non-paper-label) name
            aliases = data.get("aliases", [])
            if any(a and not _is_paper_label(str(a).strip()) for a in aliases):
                continue

            try:
                result = _oc_name(smiles)
            except Exception as e:
                print(f"[OpenclaturaAgent] failed for {smiles[:40]!r}: {e}")
                continue

            if result.ok and result.name:
                iupac = result.name
                self.G.nodes[node]["iupac_name"] = iupac
                if iupac not in aliases:
                    self.G.nodes[node].setdefault("aliases", []).append(iupac)
                count += 1
                print(f"[OpenclaturaAgent] {smiles[:40]!r} → {iupac!r}")

        return count

    # ── A. PubChem name filling ────────────────────────────────────────────────


    async def _fill_compound_names(self, paper: str) -> int:
        """Fill missing compound names for compounds belonging to this paper."""
        from .utils import _is_paper_label

        count = 0
        for node, data in self.G.nodes(data=True):
            if data.get("node_type") != "Compound":
                continue
            if paper not in data.get("papers", []):
                continue

            # Check whether any alias is already a meaningful name
            aliases = data.get("aliases", [])
            has_good_name = any(
                a and not _is_paper_label(str(a).strip())
                for a in aliases
            )
            if has_good_name and data.get("pubchem_name"):
                continue

            smiles = data.get("smiles_canonical", "")
            if not smiles:
                continue

            await asyncio.sleep(0.2)  # PubChem rate-limit buffer
            name = await _pubchem_name(smiles)
            if name:
                if name not in aliases:
                    self.G.nodes[node].setdefault("aliases", []).append(name)
                self.G.nodes[node]["pubchem_name"] = name
                count += 1

        # Pass 2: look up by name for text-only compound nodes
        for node, data in self.G.nodes(data=True):
            if data.get("node_type") != "Compound":
                continue
            if not data.get("no_smiles"):
                continue
            if paper not in data.get("papers", []):
                continue
            if data.get("pubchem_name") and data.get("smiles_canonical"):
                continue  # already resolved

            # Try each alias as a name query
            names_to_try = [a for a in data.get("aliases", []) if a and len(a) > 2]
            for name in names_to_try[:3]:
                await asyncio.sleep(0.2)
                smiles, canon = await _pubchem_by_name(name)
                if smiles:
                    self.G.nodes[node]["smiles_canonical"] = smiles
                    self.G.nodes[node]["no_smiles"] = False
                    if canon:
                        self.G.nodes[node]["pubchem_name"] = canon
                        if canon not in self.G.nodes[node].get("aliases", []):
                            self.G.nodes[node].setdefault("aliases", []).append(canon)
                    count += 1
                    break

        return count

    # ── B. LLM name resolution ────────────────────────────────────────────────

    async def _resolve_names_with_llm(self, paper: str, md_text: str) -> int:
        """
        For compounds in this paper that only have paper-internal labels
        (e.g. 'Photocatalyst 2', '1a', 'PC1'), ask the LLM to identify
        the real compound name from the paper text and SMILES structure.
        Returns count of names resolved.
        """
        from .utils import _is_paper_label

        # Collect compounds that still lack a real name after PubChem pass
        candidates = []
        for node, data in self.G.nodes(data=True):
            if data.get("node_type") != "Compound":
                continue
            if paper not in data.get("papers", []):
                continue
            if data.get("pubchem_name") or data.get("context_resolved_name"):
                continue  # already resolved
            aliases = data.get("aliases", [])
            all_labels = all(_is_paper_label(str(a).strip()) for a in aliases if a)
            if all_labels:
                candidates.append({
                    "node": node,
                    "smiles": data.get("smiles_canonical", ""),
                    "aliases": [str(a) for a in aliases if a],
                })

        if not candidates:
            return 0

        # Batch LLM call — send up to 30 at once
        batch_size = 30
        count = 0
        for i in range(0, len(candidates), batch_size):
            batch = candidates[i: i + batch_size]
            compounds_json = json.dumps(
                [{"smiles": c["smiles"], "aliases": c["aliases"]} for c in batch],
                indent=2,
            )
            prompt = _NAME_RESOLUTION_PROMPT.format(
                paper=paper,
                text_excerpt=self._relevant_excerpt(md_text, window=5000),
                compounds_json=compounds_json,
            )
            try:
                raw = await _call_llm(prompt, self.model_name)
                raw = raw.strip()
                if raw.startswith("```"):
                    raw = "\n".join(raw.split("\n")[1:]).split("```")[0]
                results = json.loads(raw)
            except Exception:
                continue

            # Map SMILES → resolved name
            smiles_to_name = {
                r.get("smiles", ""): r.get("resolved_name", "")
                for r in results
                if isinstance(r, dict) and r.get("resolved_name", "").strip()
            }

            for c in batch:
                name = smiles_to_name.get(c["smiles"], "")
                if name and not _is_paper_label(name.strip()):
                    node = c["node"]
                    self.G.nodes[node]["context_resolved_name"] = name.strip()
                    if name not in self.G.nodes[node].get("aliases", []):
                        self.G.nodes[node].setdefault("aliases", []).append(name.strip())
                    count += 1

        return count

    async def _verify_entries(self, paper: str, md_text: str) -> int:
        """Verify extracted entries against markdown text. Returns count verified."""
        from .utils import _is_paper_label

        # Gather all reaction nodes for this paper, grouped by table
        tables: dict[str, list[dict]] = {}
        for node, data in self.G.nodes(data=True):
            if data.get("node_type") != "Reaction":
                continue
            if data.get("paper") != paper:
                continue
            tbl = data.get("table", "unknown")
            tables.setdefault(tbl, []).append((node, data))

        if not tables:
            return 0

        total_verified = 0

        for tbl, node_pairs in tables.items():
            text_excerpt = self._relevant_excerpt(md_text, table_name=tbl, window=6000)
            # Build a compact entry summary for the LLM
            entries_summary = []
            for rxn_node, rxn_data in node_pairs:
                entry = {
                    "entry_id": rxn_data.get("entry_id", "?"),
                    "yield": rxn_data.get("yield"),
                    "temperature": rxn_data.get("temperature"),
                    "catalyst": [],
                    "reagents": [],
                    "solvents": [],
                }
                for _, nbr, ed in self.G.out_edges(rxn_node, data=True):
                    et = ed.get("edge_type", "")
                    nbr_data = self.G.nodes[nbr]
                    if nbr_data.get("node_type") != "Compound":
                        continue
                    # Pick best display name
                    aliases = nbr_data.get("aliases", [])
                    good_names = [a for a in aliases if a and not _is_paper_label(str(a).strip())]
                    smiles = nbr_data.get("smiles_canonical") or ""
                    resolved_name = nbr_data.get("context_resolved_name") or nbr_data.get("pubchem_name") or ""
                    if good_names:
                        display = good_names[0]
                    elif resolved_name:
                        display = resolved_name
                    elif smiles:
                        display = f"<smiles:{smiles[:30]}>"
                    else:
                        # text-only compound — skip, already has label_text as alias
                        continue
                    if et == "USES_CATALYST":
                        entry["catalyst"].append(display)
                    elif et == "USES_REAGENT":
                        entry["reagents"].append(display)
                    elif et == "CONDUCTED_IN":
                        entry["solvents"].append(display)
                entries_summary.append(entry)

            if not entries_summary:
                continue

            prompt = _VERIFY_PROMPT.format(
                paper=paper,
                text_excerpt=text_excerpt,
                entries_json=json.dumps(entries_summary, indent=2),
            )

            try:
                raw = await _call_llm(prompt, self.model_name)
                # Parse JSON — may be wrapped in markdown code block
                raw = raw.strip()
                if raw.startswith("```"):
                    raw = "\n".join(raw.split("\n")[1:])
                    raw = raw.split("```")[0]
                results = json.loads(raw)
            except Exception:
                results = []

            # Apply results to Reaction nodes + resolve any newly found compound names
            id_to_result = {str(r.get("entry_id", "")): r for r in results if isinstance(r, dict)}
            for rxn_node, rxn_data in node_pairs:
                eid = str(rxn_data.get("entry_id", "?"))
                res = id_to_result.get(eid, {})
                self.G.nodes[rxn_node]["verified"] = bool(res.get("verified", False))
                self.G.nodes[rxn_node]["verification_note"] = res.get("note", "")
                total_verified += 1

                # Apply LLM-resolved names for SMILES-only compounds
                resolved = res.get("resolved_names", {})
                if resolved:
                    for _, nbr, _ in self.G.out_edges(rxn_node, data=True):
                        nbr_data = self.G.nodes[nbr]
                        if nbr_data.get("node_type") != "Compound":
                            continue
                        smiles = nbr_data.get("smiles_canonical") or ""
                        # Match by SMILES prefix (or compound name for text-only nodes)
                        if smiles:
                            prefix = f"<smiles:{smiles[:30]}>"
                        else:
                            _lbl = (nbr_data.get("label_text") or
                                    (nbr_data.get("aliases") or [None])[0] or "")
                            prefix = _lbl.strip()
                        for key, name in resolved.items():
                            if key in prefix or prefix in key:
                                if name and name not in nbr_data.get("aliases", []):
                                    self.G.nodes[nbr].setdefault("aliases", []).append(name)
                                    self.G.nodes[nbr]["context_resolved_name"] = name
                                break

        return total_verified

    # ── D. Edge correction ────────────────────────────────────────────────────

    async def _correct_edges_with_llm(self, paper: str, md_text: str) -> int:
        """
        Ask LLM to review role assignments for every reaction in the paper
        and correct any wrong USES_CATALYST / USES_REAGENT / CONDUCTED_IN edges.
        Returns count of edges corrected.
        """
        from .builder import _CATALYST_ROLES, _REAGENT_ROLES, _SOLVENT_ROLES

        _ROLE_TO_EDGE = {
            **{r: "USES_CATALYST" for r in _CATALYST_ROLES},
            **{r: "USES_REAGENT"  for r in _REAGENT_ROLES},
            **{r: "CONDUCTED_IN"  for r in _SOLVENT_ROLES},
            "reactant": "HAS_REACTANT",
            "product":  "HAS_PRODUCT",
        }

        # Build compact entry summaries grouped by table
        tables: dict[str, list[tuple]] = {}
        for node, data in self.G.nodes(data=True):
            if data.get("node_type") != "Reaction" or data.get("paper") != paper:
                continue
            tbl = data.get("table", "unknown")
            tables.setdefault(tbl, []).append((node, data))

        total_corrected = 0

        for tbl, node_pairs in tables.items():
            entries_summary = []
            for rxn_node, rxn_data in node_pairs:
                entry: dict = {"entry_id": rxn_data.get("entry_id", "?"), "compounds": []}
                for _, nbr, ed in self.G.out_edges(rxn_node, data=True):
                    ntype = self.G.nodes[nbr].get("node_type", "")
                    if ntype != "Compound":
                        continue
                    etype = ed.get("edge_type", "")
                    specific = ed.get("specific_role", "")
                    smiles = self.G.nodes[nbr].get("smiles_canonical") or ""
                    _nd = self.G.nodes[nbr]
                    name = (_nd.get("pubchem_name") or
                            _nd.get("context_resolved_name") or
                            _nd.get("label_text") or
                            (_nd.get("aliases") or [None])[0] or
                            (smiles[:30] if smiles else nbr))
                    entry["compounds"].append({
                        "smiles": smiles,
                        "name": name,
                        "current_role": specific or etype.lower(),
                        "edge_type": etype,
                    })
                if entry["compounds"]:
                    entries_summary.append(entry)

            if not entries_summary:
                continue

            prompt = _EDGE_CORRECTION_PROMPT.format(
                paper=paper,
                text_excerpt=self._relevant_excerpt(md_text, table_name=tbl, window=5000),
                entries_json=json.dumps(entries_summary, indent=2),
            )

            try:
                raw = await _call_llm(prompt, self.model_name)
                raw = raw.strip()
                if raw.startswith("```"):
                    raw = "\n".join(raw.split("\n")[1:]).split("```")[0]
                corrections = json.loads(raw)
            except Exception:
                continue

            if not corrections:
                continue

            # Apply corrections
            entry_map = {str(rxn_data.get("entry_id", "?")): rxn_node
                         for rxn_node, rxn_data in node_pairs}

            for corr in corrections:
                if not isinstance(corr, dict):
                    continue
                eid     = str(corr.get("entry_id", ""))
                smiles  = corr.get("smiles", "")
                old_role = corr.get("current_role", "").lower()
                new_role = corr.get("correct_role", "").lower()
                reason   = corr.get("reason", "")

                if not (eid and smiles and new_role and old_role != new_role):
                    continue

                rxn_node = entry_map.get(eid)
                if not rxn_node:
                    continue

                # Find the compound node by SMILES
                nbr_key = None
                for _, nbr, ed in list(self.G.out_edges(rxn_node, data=True)):
                    if self.G.nodes[nbr].get("smiles_canonical", "") == smiles:
                        nbr_key = nbr
                        break

                if not nbr_key:
                    continue

                new_edge_type = _ROLE_TO_EDGE.get(new_role)
                if not new_edge_type:
                    continue

                # Remove all existing edges from rxn → compound
                edges_to_remove = [
                    (rxn_node, nbr_key, k)
                    for k, ed in self.G.get_edge_data(rxn_node, nbr_key, default={}).items()
                ]
                for u, v, k in edges_to_remove:
                    if self.G.has_edge(u, v, k):
                        self.G.remove_edge(u, v, k)

                # Add corrected edge
                self.G.add_edge(
                    rxn_node, nbr_key,
                    edge_type=new_edge_type,
                    specific_role=new_role,
                    corrected_by_agent=True,
                    correction_reason=reason,
                )
                total_corrected += 1

        # Rebuild compound roles after corrections
        if total_corrected > 0:
            from .builder import _enrich_compound_roles
            _enrich_compound_roles(self.G)

        return total_corrected

    # ── E. Concept node ────────────────────────────────────────────────────────

    async def _add_concept_node(self, paper: str, md_text: str) -> bool:
        """Extract and attach a Concept node for the article. Returns True if added."""
        concept_key = f"CONCEPT__{paper}"
        if concept_key in self.G:
            return False  # already enriched

        text_excerpt = self._relevant_excerpt(md_text, window=3000)
        prompt = _CONCEPT_PROMPT.format(paper=paper, text=text_excerpt)

        try:
            raw = await _call_llm(prompt, self.model_name)
            raw = raw.strip()
            if raw.startswith("```"):
                raw = "\n".join(raw.split("\n")[1:])
                raw = raw.split("```")[0]
            concept_data = json.loads(raw)
        except Exception:
            return False

        self.G.add_node(
            concept_key,
            node_type="Concept",
            transformation=concept_data.get("transformation", ""),
            catalyst_type=concept_data.get("catalyst_type", ""),
            substrate_class=concept_data.get("substrate_class", ""),
            application=concept_data.get("application", ""),
            label=concept_data.get("transformation", paper)[:60],
        )

        art_key = f"ARTICLE__{paper}"
        if art_key in self.G:
            self.G.add_edge(art_key, concept_key, edge_type="ABOUT")

        return True

    # ── F. Table summaries ────────────────────────────────────────────────────

    async def _summarise_tables(self, paper: str, md_text: str) -> int:
        """Generate a plain-English summary for each Table node in the paper."""
        # Find all Table nodes for this paper
        table_nodes = [
            (node, data)
            for node, data in self.G.nodes(data=True)
            if data.get("node_type") == "Table" and data.get("paper") == paper
        ]
        if not table_nodes:
            return 0

        added = 0

        for tbl_key, tbl_data in table_nodes:
            if tbl_data.get("summary"):
                continue  # already summarised

            table_name = tbl_data.get("table_name", tbl_key)

            # Build a compact entries summary for the prompt
            rxn_nodes = [
                (n, d) for n, d in self.G.nodes(data=True)
                if d.get("node_type") == "Reaction"
                and d.get("paper") == paper
                and d.get("table") == table_name
            ]
            if not rxn_nodes:
                continue

            entry_lines = []
            for rxn_node, rxn_data in sorted(rxn_nodes, key=lambda x: str(x[1].get("entry_id", ""))):
                eid  = rxn_data.get("entry_id", "?")
                yld  = rxn_data.get("yield")
                temp = rxn_data.get("temperature")
                time_ = rxn_data.get("time")
                cats, reagents, solvents = [], [], []
                for _, nbr, ed in self.G.out_edges(rxn_node, data=True):
                    et = ed.get("edge_type", "")
                    nd = self.G.nodes[nbr]
                    name = (nd.get("pubchem_name") or
                            nd.get("context_resolved_name") or
                            nd.get("label_text") or
                            (nd.get("aliases") or [None])[0] or "?")
                    if et == "USES_CATALYST":
                        cats.append(f"{name} ({ed.get('specific_role','')})")
                    elif et == "USES_REAGENT":
                        reagents.append(f"{name} ({ed.get('specific_role','')})")
                    elif et == "CONDUCTED_IN":
                        solvents.append(name)
                parts = [f"Entry {eid}"]
                if yld is not None:
                    parts.append(f"yield={yld}%")
                if temp is not None:
                    parts.append(f"temp={temp}°C")
                if time_ is not None:
                    parts.append(f"time={time_}h")
                if cats:
                    parts.append(f"catalyst={', '.join(cats)}")
                if reagents:
                    parts.append(f"reagents={', '.join(reagents)}")
                if solvents:
                    parts.append(f"solvent={', '.join(solvents)}")
                entry_lines.append(" | ".join(parts))

            entries_summary = "\n".join(entry_lines)
            prompt = _TABLE_SUMMARY_PROMPT.format(
                paper=paper,
                table_name=table_name,
                text_excerpt=self._relevant_excerpt(md_text, table_name=table_name, window=5000),
                entries_summary=entries_summary,
            )

            try:
                raw = await _call_llm(prompt, self.model_name)
                raw = raw.strip()
                if raw.startswith("```"):
                    raw = "\n".join(raw.split("\n")[1:]).split("```")[0]
                result = json.loads(raw)
            except Exception:
                continue

            # Write summary fields onto the Table node
            self.G.nodes[tbl_key]["summary"]     = result.get("summary", "")
            self.G.nodes[tbl_key]["purpose"]     = result.get("purpose", "")
            self.G.nodes[tbl_key]["best_entry"]  = result.get("best_entry", "")
            self.G.nodes[tbl_key]["conclusion"]  = result.get("conclusion", "")
            added += 1

        return added

    # ── Utilities ──────────────────────────────────────────────────────────────

    @staticmethod
    def _relevant_excerpt(md_text: str, table_name: str = "", window: int = 6000) -> str:
        """Return the most relevant slice of the paper markdown.

        Strategy:
        1. If table_name given, find the heading/mention of that table and
           return [anchor - 500 : anchor + window].
        2. Otherwise find the first results/optimization heading and return
           from there.
        3. Fall back to the first `window` characters (abstract + intro).

        Always includes the first 800 chars (abstract) prepended so the LLM
        has paper-level context regardless of where the anchor lands.
        """
        abstract = md_text[:800]

        # Search anchors — in priority order
        anchors: list[int] = []

        import re as _re
        if table_name:
            # Direct table mention: "Table 2", "Table S2", etc.
            for m in _re.finditer(_re.escape(table_name), md_text, _re.IGNORECASE):
                anchors.append(max(0, m.start() - 300))

        if not anchors:
            # Results / optimization section headings
            for pat in (
                r"(?m)^#+\s*(result|optimiz|screen|condition|solvent|catalyst|scope)",
                r"(?m)^#+\s*(table\s+\d)",
                r"(?m)^#+\s*(experimental)",
            ):
                m = _re.search(pat, md_text, _re.IGNORECASE)
                if m:
                    anchors.append(max(0, m.start() - 200))
                    break

        if anchors:
            start = anchors[0]
            body = md_text[start: start + window]
            # Prepend abstract if the anchor is far from the start
            if start > 1000:
                return abstract + "\n...\n" + body
            return body

        return md_text[:window]

    def _find_markdown(self, paper: str) -> Optional[Path]:
        """Find the processed .md file for a paper."""
        paper_dir = self.data_root / paper
        if not paper_dir.is_dir():
            return None

        # Prefer the run matching run_preference, otherwise first found
        candidates: list[Path] = []
        for run_dir in paper_dir.iterdir():
            if not run_dir.is_dir():
                continue
            processed_dir = run_dir / "processed"
            if not processed_dir.is_dir():
                continue
            for md in processed_dir.glob("*.md"):
                candidates.append(md)

        if not candidates:
            return None

        if self.run_preference:
            pref_matches = [
                p for p in candidates
                if self.run_preference in str(p)
            ]
            if pref_matches:
                return pref_matches[0]

        return candidates[0]

    def _save_graph(self) -> None:
        """Persist the graph back to the standard location."""
        try:
            from .builder import save_graph
            out_path = Path(__file__).parent.parent / "kg" / "graph" / "reaction_network.json"
            out_path.parent.mkdir(parents=True, exist_ok=True)
            save_graph(self.G, str(out_path))
        except Exception:
            pass
