import logging
import os
from typing import Dict, Any, Optional

from miner.agents.base_agent import BaseAgent

_log = logging.getLogger(__name__)


class FixedConditionExtractorAgent(BaseAgent):
    """
    Extracts two types of fixed reaction conditions from article sources:

    1. Fixed conditions — constant across all rows (s table columns.

    2. Per-entry overrides — conditions that differ for solvent, time, temperature,
       light source) stated in surrounding prose but not aspecific entries, defined
       via footnote markers (e.g. [d], [e], [f]) on those entry rows.

    Handles all three footnote source cases:
      - Footnotes defined below the table in the markdown
      - Footnotes visible in the scheme image (the LLM reads them from the image
        passed by the intelligence engine — this agent handles text sources only)
      - Footnotes not explicitly defined but described in the article prose near
        the table

    Input:  paper_context (prose around table), markdown_footnotes (dict from
            table parser), table_markdown (the table text showing footnote markers).
    Output: {"fixed_conditions": {...}, "per_entry_overrides": {"8": {...}, ...}}
    """

    def __init__(self):
        from context import load_config
        config = load_config()
        model = config.get("model", {}).get("agents", {}).get(
            "fixed_condition_extractor_model", "openai/gpt-4o")
        super().__init__(model=model)
        self._prompt = self._load_prompt()

    def _load_prompt(self) -> str:
        prompt_path = os.path.normpath(os.path.join(
            os.path.dirname(__file__), "..", "prompts",
            "fixed_condition_extractor_prompt.txt"
        ))
        try:
            with open(prompt_path, "r", encoding="utf-8") as f:
                return f.read()
        except FileNotFoundError:
            _log.error(f"FixedConditionExtractorAgent: prompt not found at {prompt_path}")
            return ""

    def run(self, paper_context: str, markdown_footnotes: Optional[Dict[str, str]] = None, table_markdown: Optional[str] = None,) -> Dict[str, Any]:
        """
        Extract fixed conditions and per-entry footnote overrides.

        Args:
            paper_context:      Prose snippet from the article around the table.
            markdown_footnotes: {marker: text} dict from the table parser
                                (e.g. {"c": "reaction was run without catalyst"}).
            table_markdown:     Raw markdown of the table (used to identify which
                                entry rows carry which footnote markers).

        Returns:
            {
                "fixed_conditions": {
                    "solvent":     {"text": "..."},
                    "time":        {"text": "", "quantity": N, "unit": "h"},
                    "temperature": {"text": "rt"},
                    "light":       {"text": "..."},
                },
                "per_entry_overrides": {
                    "8":  {"solvent": {"text": "1,2-dichloroethane"}},
                    "11": {"light": None},   # None → remove that condition
                }
            }
        """
        if not paper_context or not self._prompt:
            return {}

        # Build user message combining all available sources
        user_parts = []

        if table_markdown:
            user_parts.append(
                f"TABLE_MARKDOWN (shows footnote markers per entry row):\n\n{table_markdown}\n"
            )

        if markdown_footnotes:
            fn_lines = "\n".join(f"  [{k}] {v}" for k, v in markdown_footnotes.items())
            user_parts.append(
                f"MARKDOWN_FOOTNOTES (definitions extracted from below the table):\n\n{fn_lines}\n"
            )
        else:
            user_parts.append(
                "MARKDOWN_FOOTNOTES: (none found in table markdown — check PAPER_CONTEXT)\n"
            )

        user_parts.append(
            f"PAPER_CONTEXT (surrounding article prose):\n\n{paper_context[:3000]}\n" # !!!!!!!!Need to confirm regarding the length used!!!!!!!
        )

        user_content = self._prompt + "\n\n---\n\n" + "\n\n".join(user_parts)

        messages = [
            {
                "role": "system",
                "content": "You are a precise chemistry data extractor. Output only valid JSON.",
            },
            {"role": "user", "content": user_content},
        ]

        try:
            response = self._call_llm(messages, response_format={"type": "json_object"})
            raw = self._parse_json(response)

            result: Dict[str, Any] = {}

            # ── Fixed conditions ───────────────────────
            fc = raw.get("fixed_conditions") or {}
            fixed: Dict[str, Any] = {}
            for role in ("solvent", "time", "temperature", "light"):
                val = fc.get(role)
                if not val or not isinstance(val, dict):
                    continue
                if val.get("text") is None:
                    continue
                entry: Dict[str, Any] = {"text": val["text"]}
                if val.get("quantity") is not None:
                    try:
                        entry["quantity"] = float(val["quantity"])
                    except (TypeError, ValueError):
                        pass
                if val.get("unit"):
                    entry["unit"] = str(val["unit"])
                fixed[role] = entry
            if fixed:
                result["fixed_conditions"] = fixed

            # ── Per-entry overrides ────────────────────────────
            peo = raw.get("per_entry_overrides") or {}
            overrides: Dict[str, Any] = {}
            for eid, roles in peo.items():
                if not isinstance(roles, dict):
                    continue
                entry_overrides: Dict[str, Any] = {}
                for role, val in roles.items():
                    if val is None:
                        # null → remove this condition from that entry
                        entry_overrides[role] = None
                    elif isinstance(val, dict) and val.get("text") is not None:
                        cond: Dict[str, Any] = {"text": val["text"]}
                        if val.get("quantity") is not None:
                            try:
                                cond["quantity"] = float(val["quantity"])
                            except (TypeError, ValueError):
                                pass
                        if val.get("unit"):
                            cond["unit"] = str(val["unit"])
                        entry_overrides[role] = cond
                if entry_overrides:
                    overrides[str(eid)] = entry_overrides
            if overrides:
                result["per_entry_overrides"] = overrides

            if result:
                fixed_keys = list(result.get("fixed_conditions", {}).keys())
                override_entries = list(result.get("per_entry_overrides", {}).keys())
                _log.info(
                    f"[FixedConditionExtractor] fixed={fixed_keys}  "
                    f"overrides for entries {override_entries}"
                )
                print(
                    f"  [FixedConditionExtractor] fixed={fixed_keys}  "
                    f"per-entry overrides={override_entries}"
                )
            return result

        except Exception as e:
            _log.warning(f"[FixedConditionExtractor] LLM call failed: {e}")
            return {}
