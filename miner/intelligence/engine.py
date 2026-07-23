import sys
import os
import torch
import json
import cv2
import base64
import numpy as np
from PIL import Image
from typing import Any
from openai import OpenAI
from dotenv import load_dotenv

load_dotenv()

# Internal Piper Imports
from miner.intelligence.core.chemietoolkit import ChemIEToolkit, utils
from miner.intelligence.core.rxnim import RxnScribe
from miner.intelligence.agents.molecular_agent import process_reaction_multiple_products_correctR
from miner.intelligence.agents.reaction_agent import get_reaction_withatoms_correctR
from miner.intelligence.agents.r_group_agent import (
    process_reaction_table_data, 
    process_reaction_variants,
    extract_full_reaction_schema,
    extract_full_molecular_data
)
from miner.intelligence.utils.json_utils import repair_json
from miner.intelligence.utils.logging_utils import save_crash_dump
from context import load_config, get_openrouter_url

# Lazy singletons for core models
_core_toolkit = None
_reaction_scribe = None

def _get_core_toolkit():
    global _core_toolkit
    if _core_toolkit is None:
        print("[LazyLoad] IntelligenceEngine: Loading ChemIEToolkit...")
        _core_toolkit = ChemIEToolkit(device=torch.device('cpu'))
    return _core_toolkit

def _get_reaction_scribe():
    global _reaction_scribe
    if _reaction_scribe is None:
        ckpt_path = os.path.join(os.path.dirname(__file__), "models", "pix2seq_reaction_full.ckpt")
        print(f"[LazyLoad] IntelligenceEngine: Loading RxnScribe from {ckpt_path}...")
        _reaction_scribe = RxnScribe(ckpt_path, device=torch.device('cpu'))
    return _reaction_scribe

def load_prompt_with_commons(skill_filename: str) -> str:
    """
    Load a prompt from the miner/prompts directory.
    Automatically prepends chemmine_core_rules.md if available.
    """
    base_dir = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    prompts_dir = os.path.join(base_dir, "miner", "prompts")
    skills_dir = os.path.join(base_dir, "miner", "skills")
    
    # Map old prompt names to the newly renamed prompt files in miner/prompts
    mapping = {
        'prompt_final.txt': 'chemmine_orchestration_logic_full_prompt.txt',
        'prompt_final_simple_version.txt': 'chemmine_orchestration_logic_prompt.txt',
        'prompt_plan.txt': 'extraction_planning_prompt.txt',
        'prompt_getreaction.txt': 'reaction_schema_prompt.txt',
        'prompt_getreaction_correctR.txt': 'reaction_schema_correctR_prompt.txt',
        'prompt_getmolecular.txt': 'molecule_extraction_prompt.txt',
        'prompt_getmolecular_correctR.txt': 'molecule_extraction_correctR_prompt.txt',
        'prompt_getmolecular_correctmultiR.txt': 'molecule_extraction_multiR_prompt.txt',
        'prompt.txt': 'r_group_variant_analysis_prompt.txt',
        'prompt_reaction_withR.txt': 'r_group_table_extraction_prompt.txt'
    }
    
    actual_filename = mapping.get(skill_filename, skill_filename)
    
    commons_path = os.path.join(skills_dir, 'chemmine_core_rules.md')
    prompt_path = os.path.join(prompts_dir, actual_filename)

    commons = ""
    if os.path.exists(commons_path):
        with open(commons_path, 'r') as f:
            commons = f.read().strip() + "\n\n"

    if os.path.exists(prompt_path):
        with open(prompt_path, 'r') as f:
            prompt = f.read()
    else:
        print(f"Warning: Prompt file {prompt_path} not found.")
        prompt = ""

    return commons + prompt


def _load_hint(filename: str) -> str:
    """Load a hint prompt snippet from miner/prompts/."""
    base = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    path = os.path.join(base, "miner", "prompts", filename)
    try:
        with open(path, "r", encoding="utf-8") as f:
            return f.read().strip()
    except FileNotFoundError:
        print(f"Warning: hint file not found: {path}")
        return ""


def _build_context_hints(
    has_arrow_parameters: bool = False,
    arrow_mapping: str = None,
    has_r_group_substitution: bool = False,
    footnote_dependencies: list = None,
    image_table_relationship: str = "both",
    has_structure_drawings: bool = False,
    structure_drawing_labels: list = None,
    text_content: str = None,
    is_scope_table: bool = False,
) -> str:
    """
    Assembles a context block from pre-written hint prompt files and appends it
    to planning and completion prompts. Each active flag loads its corresponding
    hint file from miner/prompts/, optionally filling in dynamic placeholders,
    so the LLM receives precise, document-specific extraction instructions.
    """
    hints = []

    if image_table_relationship == "scheme_only":
        hints.append(_load_hint("hint_scheme_only.txt"))
    elif image_table_relationship == "text_only":
        hints.append(_load_hint("hint_text_only.txt"))
    # "both" is the default — no hint needed

    if has_arrow_parameters:
        hint = _load_hint("hint_arrow_parameters.txt")
        if arrow_mapping:
            hint = hint.replace(
                "{arrow_mapping_line}",
                f"The structure parser noted: \"{arrow_mapping}\"\n"
            )
        else:
            hint = hint.replace("{arrow_mapping_line}", "")
        hints.append(hint)

    if has_r_group_substitution:
        hints.append(_load_hint("hint_r_group_substitution.txt"))

    if footnote_dependencies:
        hint = _load_hint("hint_footnote_columns.txt")
        hint = hint.replace("{columns}", ", ".join(footnote_dependencies))
        hints.append(hint)

    if has_structure_drawings:
        hint = _load_hint("hint_structure_drawings.txt")
        if structure_drawing_labels:
            labels_line = (
                f"The structure parser identified the following labelled drawings: "
                f"{', '.join(structure_drawing_labels)}.\n"
            )
        else:
            labels_line = ""
        hint = hint.replace("{labels_line}", labels_line)
        hints.append(hint)

    if is_scope_table:
        hints.append(
            "SCOPE TABLE: This table is a substrate scope table, not an optimization table.\n"
            "Each row represents a different substrate variant with a fixed reaction scheme above.\n"
            "Extract the substrate label/structure and the reported yield for each entry.\n"
            "Do NOT try to extract condition variations — the conditions are fixed across all rows."
        )

    if not hints:
        return ""
    return "\n\n" + "\n\n".join(hints) + "\n"


def RunPiperIntelligence(
    image_path: str,
    text_content: str = None,
    model_override: str = None,
    has_arrow_parameters: bool = False,
    arrow_mapping: str = None,
    has_r_group_substitution: bool = False,
    footnote_dependencies: list = None,
    image_table_relationship: str = "both",
    has_structure_drawings: bool = False,
    structure_drawing_labels: list = None,
    progress_callback: Any = None,
    paper_context: str = None,
    is_scope_table: bool = False,
    expected_row_count: int = None,
) -> dict:
    """
    Main entry point for the Integrated Chemical Intelligence Engine.
    Routes to the correct extraction agent based on image content and pre-computed flags
    from the advanced structure parser.

    Args:
        image_path: Path to the scheme/table image.
        text_content: Markdown table text extracted from the document (may be None).
        has_arrow_parameters: True when reagents/conditions appear above/below the reaction
            arrow and need to be extracted individually per row.
        arrow_mapping: Human-readable description of which arrow-level parameters map to
            which table columns (e.g. "Photocatalyst and base vary per row").
        has_r_group_substitution: True when the scheme uses explicit R-group variable
            notation and the table enumerates substituent values per row.
        footnote_dependencies: Column names whose cell values reference table footnotes.
        image_table_relationship: One of "both", "scheme_only", or "text_only".
            Controls how the engine weights image vs. text for row extraction.
        has_structure_drawings: True when the image contains additional molecular
            structures drawn outside the main reaction arrow (e.g. reagent boxes,
            corner legend, inset structures).
        structure_drawing_labels: Labels of those additional drawn structures,
            if any were identified by the structure parser.
        progress_callback: Optional callback for UI progress updates.
    """
    config = load_config()
    model_name = model_override or config.get("model", {}).get("agents", {}).get("reaction_model", "x-ai/grok-4.1-fast")

    API_KEY = (
        os.environ.get("OPENROUTER_API_KEY") or
        os.environ.get("API_KEY") or
        os.environ.get("OPENAI_API_KEY")
    )
    BASE_URL = get_openrouter_url()
    client_kwargs = {"base_url": BASE_URL}
    if API_KEY:
        client_kwargs["api_key"] = API_KEY
    client = OpenAI(**client_kwargs)

    def encode_image(img_path: str):
        with open(img_path, "rb") as f:
            return base64.b64encode(f.read()).decode('utf-8')

    base64_image = encode_image(image_path)

    # ── Tool definitions ──────────────────────────────────────────────────────
    tools = [
        {
            'type': 'function',
            'function': {
                'name': 'process_reaction_variants',
                'description': 'Handle Situation 1: Reaction diagrams with variant product sets and graphical tables.',
                'parameters': {
                    'type': 'object',
                    'properties': {'image_path': {'type': 'string'}},
                    'required': ['image_path'],
                    'additionalProperties': False,
                },
            },
        },
        {
            'type': 'function',
            'function': {
                'name': 'process_reaction_table_data',
                'description': 'Handle Situation 2: Reaction diagrams with text-based R-group substitution tables.',
                'parameters': {
                    'type': 'object',
                    'properties': {'image_path': {'type': 'string'}},
                    'required': ['image_path'],
                    'additionalProperties': False,
                },
            },
        },
        {
            'type': 'function',
            'function': {
                'name': 'extract_full_reaction_schema',
                'description': 'Handle Situation 3 & 4: Optimization tables or diagrams without R-group substitution.',
                'parameters': {
                    'type': 'object',
                    'properties': {'image_path': {'type': 'string'}},
                    'required': ['image_path'],
                    'additionalProperties': False,
                },
            },
        },
        {
            'type': 'function',
            'function': {
                'name': 'extract_full_molecular_data',
                'description': 'Handle Situation 5: Flat molecule images with no reaction arrows.',
                'parameters': {
                    'type': 'object',
                    'properties': {'image_path': {'type': 'string'}},
                    'required': ['image_path'],
                    'additionalProperties': False,
                },
            },
        },
    ]

    # ── Build context hints from flags ────────────────────────────────────────
    # These are appended to the planning prompt so the LLM chooses the right tool
    # and knows which extraction constraints apply.
    context_hints = _build_context_hints(
        has_arrow_parameters=has_arrow_parameters,
        arrow_mapping=arrow_mapping,
        has_r_group_substitution=has_r_group_substitution,
        footnote_dependencies=footnote_dependencies,
        image_table_relationship=image_table_relationship,
        has_structure_drawings=has_structure_drawings,
        structure_drawing_labels=structure_drawing_labels or [],
        text_content=text_content,
        is_scope_table=is_scope_table,
    )

    # ── Load prompts ──────────────────────────────────────────────────────────
    prompt_main = load_prompt_with_commons('chemmine_orchestration_logic_prompt.txt')
    prompt_plan = load_prompt_with_commons('extraction_planning_prompt.txt')

    user_content = [
        {'type': 'text', 'text': prompt_plan + context_hints},
        {'type': 'image_url', 'image_url': {'url': f'data:image/png;base64,{base64_image}'}},
    ]
    if text_content:
        user_content.append({
            'type': 'text',
            'text': f"Markdown table (use for row reference):\n\n{text_content}\n",
        })
    if paper_context:
        user_content.append({
            'type': 'text',
            'text': (
                f"PAPER_CONTEXT (surrounding article text — use this to identify fixed "
                f"conditions such as solvent, temperature, and time that are not shown "
                f"as table columns but are mentioned in the text):\n\n{paper_context}\n"
            ),
        })

    messages = [
        {'role': 'system', 'content': 'You are a professional chemical intelligence assistant.'},
        {'role': 'user', 'content': user_content},
    ]

    # ── Phase 1: Planning ─────────────────────────────────────────────────────
    # tool_choice="required" forces the model to always call one of the 4
    # extraction tools instead of returning plain text, preventing the silent
    # fallback that produces empty entries when the model opts out.
    try:
        response = client.chat.completions.create(
            model=model_name, temperature=0, messages=messages,
            tools=tools, tool_choice="required"
        )
    except Exception:
        # Some models/providers don't support tool_choice="required" — fall back
        # to the unconstrained call.
        response = client.chat.completions.create(
            model=model_name, temperature=0, messages=messages, tools=tools
        )

    from miner.prompt_logger import prompt_logger
    try:
        prompt_logger.log("PiperIntelligence_Plan", model_name, messages,
                          response.choices[0].message.content or "tool call")
    except Exception:
        pass
    from miner.token_tracker import tracker
    if hasattr(response, "usage") and response.usage:
        tracker.record("PiperIntelligence_Plan", model_name,
                       response.usage.prompt_tokens or 0,
                       response.usage.completion_tokens or 0)

    TOOL_MAP = {
        'process_reaction_variants': process_reaction_variants,
        'process_reaction_table_data': process_reaction_table_data,
        'extract_full_reaction_schema': extract_full_reaction_schema,
        'extract_full_molecular_data': extract_full_molecular_data,
    }

    if not response or not response.choices or not response.choices[0].message.tool_calls:
        # Model returned plain text instead of a tool call (tool_choice not honoured,
        # or provider doesn't support it). Default to extract_full_reaction_schema so
        # extraction still runs rather than silently returning empty entries.
        content = response.choices[0].message.content if response and response.choices else ""
        print(f"[PiperIntelligence] Plan returned no tool call — defaulting to "
              f"extract_full_reaction_schema. LLM response: {str(content)[:200]}")
        try:
            tool_result = extract_full_reaction_schema(
                image_path,
                has_arrow_parameters=has_arrow_parameters,
                footnote_dependencies=footnote_dependencies,
                markdown_table_content=text_content,
            )
            results = [{
                'role': 'tool',
                'content': json.dumps({'image_path': image_path,
                                       'extract_full_reaction_schema': tool_result}),
                'tool_call_id': 'fallback',
            }]
        except Exception as e:
            print(f"[PiperIntelligence] Fallback extraction also failed: {e}")
            return {"reactions": []}

    tool_calls = response.choices[0].message.tool_calls
    results = []

    for tool_call in tool_calls:
        tool_name = tool_call.function.name
        tool_call_id = tool_call.id

        try:
            repair_json(tool_call.function.arguments)
        except json.JSONDecodeError as e:
            save_crash_dump("PiperIntelligence (Tool Args)", model_name, config,
                            tool_call.function.arguments, e)
            raise ValueError(f"Malformed tool arguments from {model_name}.")

        if tool_name not in TOOL_MAP:
            raise ValueError(f"Unknown tool called: {tool_name}")

        # Forward all extraction flags to every tool that accepts them
        if tool_name == 'process_reaction_table_data':
            tool_result = TOOL_MAP[tool_name](
                image_path,
                markdown_table_content=text_content,
                has_arrow_parameters=has_arrow_parameters,
                footnote_dependencies=footnote_dependencies,
                has_r_group_substitution=has_r_group_substitution,
            )
        elif tool_name in ('process_reaction_variants', 'extract_full_reaction_schema'):
            tool_result = TOOL_MAP[tool_name](
                image_path,
                has_arrow_parameters=has_arrow_parameters,
                footnote_dependencies=footnote_dependencies,
                markdown_table_content=text_content,
            )
        else:
            tool_result = TOOL_MAP[tool_name](image_path)

        results.append({
            'role': 'tool',
            'content': json.dumps({'image_path': image_path, tool_name: tool_result}),
            'tool_call_id': tool_call_id,
        })

    # ── Phase 2: Final Resolution ─────────────────────────────────────────────
    final_user_content = [
        {'type': 'text', 'text': prompt_main + context_hints},
        {'type': 'image_url', 'image_url': {'url': f'data:image/png;base64,{base64_image}'}},
    ]
    row_count_hint = ""
    if expected_row_count and expected_row_count > 0:
        # Authoritative count from the advanced-structure parser (read directly
        # from the image, so more reliable than the often-incomplete Markdown).
        row_count_hint = (
            f" The table has EXACTLY {expected_row_count} data rows —"
            f" you MUST extract all {expected_row_count} rows."
            " Do not stop early. Count the rows in the image carefully before responding."
        )
    elif text_content:
        _data_rows = [
            ln for ln in text_content.splitlines()
            if ln.strip().startswith("|")
            and not ln.strip().startswith("|---")
            and not ln.strip().startswith("|===")
        ]
        _n_data = max(0, len(_data_rows) - 1)
        if _n_data > 0:
            row_count_hint = (
                f" The Markdown table shows at least {_n_data} data rows —"
                " extract ALL rows visible in the image, do not stop early even if rows look similar."
            )

    # Always include the markdown table in the prompt when available — the
    # row_count_hint and authoritative_source guide the LLM on what to trust.
    if text_content:
        final_user_content.append({
            'type': 'text',
            'text': f"Context: Use the following Markdown table for multi-row extraction.\n\n{text_content}\n",
        })

    if text_content and image_table_relationship == "scheme_only":
        # Scheme image only shows the reaction arrow; the markdown holds the actual row data.
        authoritative_source = (
            "When a Markdown table is provided, it is the AUTHORITATIVE source for per-entry data "
            "(entry numbers, varying condition values, yields). "
            "Extract EXACTLY the rows in that table — do not invent entries not present in it. "
            "Use the image only to identify the reaction scheme, fixed conditions, and compound structures."
        )
    else:
        authoritative_source = (
            "Use the image as the authoritative source — extract every visible row."
        )

    schema_reminder = (
        "CRITICAL: Your final response MUST be a single JSON object with a 'reactions' key."
        f"{row_count_hint} {authoritative_source} "
        "Generate ONE reaction entry per data row. "
        "Do NOT stop early, merge rows, or skip any row — including rows that look similar to previous ones. "
        "Every row with a different catalyst, base, solvent, or time value is a distinct entry. "
        "Stopping before the last visible row is a failure."
    )

    completion_messages = [
        {'role': 'system', 'content': f'You are a professional chemical intelligence assistant. {schema_reminder}'},
        {'role': 'user', 'content': final_user_content},
        response.choices[0].message,
        *results
    ]

    response = client.chat.completions.create(
        model=model_name,
        messages=completion_messages,
        temperature=0,
        max_tokens=16384
    )

    try:
        content = response.choices[0].message.content or "LLM Final Response"
        prompt_logger.log("PiperIntelligence_Resolution", model_name, completion_messages, content)
    except Exception:
        pass
    from miner.token_tracker import tracker
    if hasattr(response, "usage") and response.usage:
        tracker.record("PiperIntelligence_Resolution", model_name,
                       response.usage.prompt_tokens or 0,
                       response.usage.completion_tokens or 0)

    raw_content = response.choices[0].message.content
    try:
        gpt_output = repair_json(raw_content)
        if isinstance(gpt_output, list):
            gpt_output = {"reactions": gpt_output}
        elif isinstance(gpt_output, dict) and "reactions" not in gpt_output:
            gpt_output = {"reactions": gpt_output.get("entries", [])}
    except json.JSONDecodeError as e:
        save_crash_dump("PiperIntelligence (Final)", model_name, completion_messages, raw_content, e)
        raise ValueError(f"Failed to parse final output from {model_name}.")

    # ── PREFER TOOL-EXTRACTED SMILES over LLM-generated ones ──
    # Build a lookup from the Phase 1 tool results (RxnScribe / ChemIEToolkit)
    # and use it to fill in any empty or wildcard SMILES in the Phase 2 output.
    _gpt_output_backfill_from_tools(gpt_output, results)

    return gpt_output


def _gpt_output_backfill_from_tools(gpt_output: dict, tool_results: list) -> None:
    """
    Mutates gpt_output in-place.
    Stores SMILES from every method in dedicated keys (smiles_llm, smiles_ocsr),
    then selects the best one as the final `smiles`:
      - OCSR (ChemIEToolkit coref or RxnScribe) if RDKit-valid  → preferred
      - LLM vision SMILES                                         → fallback
    """
    import re as _re
    try:
        from rdkit import Chem as _Chem
    except ImportError:
        _Chem = None

    def _rdkit_valid(s):
        if not s or not _Chem:
            return False
        try:
            return _Chem.MolFromSmiles(s) is not None
        except Exception:
            return False

    # ── Collect label→SMILES and ordered SMILES from tool results ──
    label_to_smiles: dict = {}   # text label → best SMILES from OCSR tool
    ordered_smiles: list = []    # reactants then products in tool order

    for r in tool_results:
        try:
            content = json.loads(r.get("content", "{}"))
        except Exception:
            continue

        tool_result = None
        for key in ("extract_full_reaction_schema", "extract_full_molecular_data",
                    "process_reaction_table_data", "process_reaction_variants"):
            if key in content:
                tool_result = content[key]
                break

        if not tool_result:
            continue

        # molecule_coref: [{smiles, texts, bbox}]  — ChemIEToolkit output
        for item in tool_result.get("molecule_coref", []):
            s = item.get("smiles", "")
            if s and "*" not in s:
                for t in item.get("texts", []):
                    if t and t.strip():
                        label_to_smiles[t.strip()] = s

        # reaction_prediction: [{reactants:[{smiles,bbox}], products:[{smiles,bbox}]}]
        for rxn in tool_result.get("reaction_prediction", []):
            for mol in rxn.get("reactants", []) + rxn.get("products", []):
                s = mol.get("smiles", "")
                if s and "*" not in s:
                    ordered_smiles.append(s)

    if not label_to_smiles and not ordered_smiles:
        return  # no tool data to work with

    # ── Store all SMILES sources and select best for `smiles` key ──
    ordered_idx = 0
    for rxn in gpt_output.get("reactions", []):
        for mol_list in (rxn.get("reactants", []), rxn.get("products", [])):
            for mol in mol_list:
                existing_llm = mol.get("smiles", "")

                # Store LLM SMILES in dedicated key
                if existing_llm:
                    mol["smiles_llm"] = existing_llm

                ocsr_smiles = None
                ocsr_source = None

                # 1. Always try label/text match against ChemIEToolkit coref
                for field in ("label", "text"):
                    raw = (mol.get(field) or "").strip()
                    if not raw:
                        continue
                    m = _re.match(r"([A-Za-z0-9]+)", raw)
                    tok = m.group(1) if m else raw
                    if tok in label_to_smiles:
                        ocsr_smiles = label_to_smiles[tok]
                        ocsr_source = "ocsr_coref"
                        break
                    # numeric-base fallback: "3a" → "3"
                    base = _re.sub(r"[a-z]+$", "", tok)
                    if base and base != tok and base in label_to_smiles:
                        ocsr_smiles = label_to_smiles[base]
                        ocsr_source = "ocsr_coref"
                        break

                # 2. Positional match from RxnScribe if coref didn't match
                if not ocsr_smiles and ordered_idx < len(ordered_smiles):
                    ocsr_smiles = ordered_smiles[ordered_idx]
                    ocsr_source = "ocsr_rxnscribe"

                # Store OCSR result in dedicated key
                if ocsr_smiles:
                    mol["smiles_ocsr"] = ocsr_smiles

                # Select best SMILES: OCSR if RDKit-valid, else LLM
                if ocsr_smiles and _rdkit_valid(ocsr_smiles):
                    mol["smiles"] = ocsr_smiles
                    mol["smiles_source"] = ocsr_source
                elif existing_llm and "*" not in existing_llm:
                    mol["smiles"] = existing_llm
                    # smiles_source left unset → orchestrator will tag as llm_vision
                elif ocsr_smiles:
                    # OCSR present but invalid — store it, keep LLM as final
                    mol["smiles"] = existing_llm or ""

                ordered_idx += 1

if __name__ == "__main__":
    if len(sys.argv) > 1:
        img_path = sys.argv[1]
        print(json.dumps(RunPiperIntelligence(img_path), indent=4))
    else:
        print("Usage: python engine.py <image_path>")
