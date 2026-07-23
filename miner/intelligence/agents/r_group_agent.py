import os
import sys
import json
import base64
import copy
from typing import Any
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FuturesTimeout

import torch
import numpy as np
from PIL import Image
from openai import OpenAI

from context import load_config, get_openrouter_url
from miner.intelligence.core.model_registry import get_toolkit
from miner.intelligence.core.chemietoolkit import utils
from miner.intelligence.core.molnextr.chemistry import _convert_graph_to_smiles
from miner.intelligence.utils.json_utils import repair_json
from miner.intelligence.utils.logging_utils import save_crash_dump
from miner.intelligence.agents.molecular_agent import (
    process_reaction_multiple_products_correctR,
    process_reaction_multiple_products_correctmultiR,
)
from miner.intelligence.agents.reaction_agent import get_reaction_withatoms_correctR


_RXNSCRIBE_TIMEOUT = 900  # 15 minutes — guards against CPU swap stalls


def _build_client():
    api_key = (
        os.environ.get("OPENROUTER_API_KEY")
        or os.environ.get("API_KEY")
        or os.environ.get("OPENAI_API_KEY")
    )
    return OpenAI(api_key=api_key, base_url=get_openrouter_url()), load_config()


# ── Coref parsing ─────────────────────────────────────────────────────────────

def parse_coref_data_with_fallback(data: dict) -> list:
    """
    Converts raw ChemIEToolkit coref pairs into [{smiles, texts, bbox}].
    Unpaired molecules are included with a placeholder label.
    """
    bboxes = data["bboxes"]
    corefs = data["corefs"]
    paired = set()
    results = []

    for i1, i2 in corefs:
        smiles_entry = bboxes[i1] if "smiles" in bboxes[i1] else bboxes[i2]
        text_entry = bboxes[i2] if "text" in bboxes[i2] else bboxes[i1]
        results.append({
            "smiles": smiles_entry.get("smiles", ""),
            "texts": text_entry.get("text", []),
            "bbox": smiles_entry.get("bbox", ()),
        })
        paired.update([i1, i2])

    for idx, entry in enumerate(bboxes):
        if "smiles" in entry and idx not in paired:
            results.append({
                "smiles": entry["smiles"],
                "texts": ["No label detected — recheck image"],
                "bbox": entry.get("bbox", ()),
            })
    return results


def parse_coref_data_with_fallback_with_box(data: dict) -> list:
    """Same as parse_coref_data_with_fallback but preserves bbox coordinates on each result."""
    bboxes = data["bboxes"]
    corefs = data["corefs"]
    paired = set()
    results = []

    for i1, i2 in corefs:
        smiles_entry = bboxes[i1] if "smiles" in bboxes[i1] else bboxes[i2]
        text_entry = bboxes[i2] if "text" in bboxes[i2] else bboxes[i1]
        # use a distinct name so we don't shadow the outer `bboxes` list
        bbox_coords = smiles_entry.get("bbox", [])
        results.append({
            "smiles": smiles_entry.get("smiles", ""),
            "texts": text_entry.get("text", []),
            "bbox": bbox_coords,
        })
        paired.update([i1, i2])

    for idx, entry in enumerate(bboxes):
        if "smiles" in entry and idx not in paired:
            results.append({
                "smiles": entry["smiles"],
                "texts": ["No label detected — recheck image"],
                "bbox": entry.get("bbox", ()),
            })
    return results


# ── Reaction helper ───────────────────────────────────────────────────────────

def get_reaction_from_raw(raw_pred: dict) -> dict:
    """Strips atom-level fields from a raw RxnScribe prediction."""
    result = {}
    for section in ('reactants', 'conditions', 'products'):
        if section not in raw_pred:
            continue
        result[section] = []
        for item in raw_pred[section]:
            if section in ('reactants', 'products'):
                result[section].append({
                    "smiles": item.get("smiles", ""),
                    "bbox": item.get("bbox", []),
                })
            else:
                result[section].append({
                    "text": item.get("text", []),
                    "bbox": item.get("bbox", []),
                    "smiles": item.get("smiles", []),
                })
    return result


def get_reaction(image_path: str) -> dict:
    raw = get_cached_raw_results(image_path)
    if not raw:
        return {}
    return get_reaction_from_raw(raw[0])


# ── Caches (avoid rerunning heavy models on the same image) ───────────────────

_mol_cache: dict = {}
_rxn_cache: dict = {}


def get_cached_multi_molecular(image_path: str) -> list:
    """
    Runs process_reaction_multiple_products_correctmultiR once per image path.
    Returns the corrected coref list used by the r-group variant agent.
    """
    if image_path not in _mol_cache:
        _mol_cache[image_path] = process_reaction_multiple_products_correctmultiR(image_path)
    return _mol_cache[image_path]


def get_cached_raw_results(image_path: str) -> list:
    """Runs get_reaction_withatoms_correctR once per image path."""
    if image_path not in _rxn_cache:
        _rxn_cache[image_path] = get_reaction_withatoms_correctR(image_path)
    return _rxn_cache[image_path]


# ── Tool functions (called via LLM tool dispatch) ─────────────────────────────

def extract_molecular_text_to_correct(image_path: str) -> list:
    """
    Strips low-level bbox fields from cached coref data, leaving only SMILES and text labels.
    Used as an LLM tool in the variant pipeline.
    """
    coref_results = copy.deepcopy(get_cached_multi_molecular(image_path))
    for item in coref_results:
        for bbox in item.get("bboxes", []):
            for key in ("category", "molfile", "symbols", "atoms", "bonds",
                        "category_id", "score", "corefs", "coords", "edges"):
                bbox.pop(key, None)
    data = coref_results[0] if coref_results else {"bboxes": [], "corefs": []}
    return parse_coref_data_with_fallback(data)


def extract_full_molecular_data(image_path: str) -> list:
    """Full molecule coref extraction — used for flat molecule images (Situation 5)."""
    image = Image.open(image_path).convert('RGB')
    coref_results = get_toolkit().extract_molecule_corefs_from_figures([image], molscribe=True, ocr=True)
    for item in coref_results:
        for bbox in item.get("bboxes", []):
            for key in ("category", "molfile", "symbols", "atoms", "bonds",
                        "category_id", "score", "corefs", "coords", "edges"):
                bbox.pop(key, None)
    if not coref_results:
        return []
    return parse_coref_data_with_fallback(coref_results[0])


def extract_full_reaction_schema(
    image_path: str,
    has_arrow_parameters: bool = False,
    footnote_dependencies: list = None,
    markdown_table_content: str = None,
) -> dict:
    """
    Full RxnScribe + ChemIEToolkit extraction (Situations 3 & 4).
    Wraps reaction_agent.extract_full_reaction_schema via a timeout guard.
    markdown_table_content is forwarded so the reaction agent can use the
    parsed table rows as the authoritative source for per-entry data.
    """
    from miner.intelligence.agents.reaction_agent import extract_full_reaction_schema as _rxn_schema
    return _rxn_schema(image_path, markdown_table_content=markdown_table_content)


def _extract_full_reaction_schema_with_timeout(image_path: str):
    """
    Calls extract_full_reaction_schema with a hard timeout so a stalled CPU model
    doesn't block the whole pipeline indefinitely.
    """
    executor = ThreadPoolExecutor(max_workers=1)
    future = executor.submit(extract_full_reaction_schema, image_path)
    try:
        result = future.result(timeout=_RXNSCRIBE_TIMEOUT)
        executor.shutdown(wait=False)
        return result
    except FuturesTimeout:
        print(f"[Timeout] extract_full_reaction_schema exceeded {_RXNSCRIBE_TIMEOUT}s — skipping OCSR.")
        future.cancel()
        executor.shutdown(wait=False)
        return None
    except Exception as e:
        print(f"[Error] extract_full_reaction_schema failed: {e}")
        executor.shutdown(wait=False)
        return None


# ── Symbol substitution — shared by both table agent paths ───────────────────

def replace_symbols_and_generate_smiles(input1: dict, input2) -> dict:
    """
    Merges LLM-provided atom symbol corrections (input2) into the raw RxnScribe
    prediction (input1) and regenerates SMILES via the graph converter.

    input1: raw reaction dict with 'reactants' and 'products' from RxnScribe
    input2: LLM output — either a list of reactions or {"reactions": [...]}
    """
    reactions_out = {"reactions": []}

    if isinstance(input2, list):
        reactions_list = input2
    elif isinstance(input2, dict):
        reactions_list = input2.get('reactions', [])
    else:
        print(f"Warning: unexpected type for LLM reaction output: {type(input2)}")
        return reactions_out

    input1_reactants = input1.get('reactants', [])
    input1_products = input1.get('products', [])

    for reaction in reactions_list:
        if not isinstance(reaction, dict):
            continue

        new_rxn = {
            "reaction_id": reaction.get('reaction_id', 'unknown'),
            "reactants": [],
            "conditions": reaction.get('conditions', []),
            "products": [],
            "additional_info": reaction.get('additional_info', []),
        }

        for j, reactant in enumerate(reaction.get('reactants', [])):
            mol = _resolve_mol(reactant, input1_reactants, j)
            if mol:
                new_rxn["reactants"].append(mol)

        for k, product in enumerate(reaction.get('products', [])):
            mol = _resolve_mol(product, input1_products, k)
            if mol:
                new_rxn["products"].append(mol)

        reactions_out['reactions'].append(new_rxn)

    return reactions_out


def _resolve_mol(llm_mol, template_list: list, idx: int) -> dict:
    """
    Resolves a single molecule from the LLM output, using the RxnScribe template
    for graph-based SMILES regeneration when needed.
    """
    if idx < len(template_list):
        template = template_list[idx]
        if isinstance(llm_mol, str):
            # LLM returned a label string — fall back to template SMILES
            syms = template.get('symbols', [])
            if 'smiles' in template:
                smiles = template['smiles']
            elif 'coords' in template and 'edges' in template:
                smiles, _, _ = _convert_graph_to_smiles(template['coords'], syms, template['edges'])
            else:
                smiles = ""
            return {"smiles": smiles, "symbols": syms}
        elif isinstance(llm_mol, dict):
            if llm_mol.get('smiles'):
                return {"smiles": llm_mol['smiles'], "symbols": llm_mol.get('symbols', [])}
            elif 'symbols' in llm_mol:
                syms = llm_mol['symbols']
                if 'coords' in template and 'edges' in template:
                    smiles, _, _ = _convert_graph_to_smiles(template['coords'], syms, template['edges'])
                else:
                    smiles = template.get('smiles', "")
                return {"smiles": smiles, "symbols": syms}
            else:
                print(f"Warning: molecule at index {idx} has neither smiles nor symbols.")
    else:
        # LLM found an extra molecule beyond what RxnScribe predicted
        if isinstance(llm_mol, dict) and llm_mol.get('smiles'):
            return {"smiles": llm_mol['smiles'], "symbols": llm_mol.get('symbols', [])}
    return None


# ── Situation 1: Variant product sets / graphical tables ──────────────────────

def process_reaction_variants(
    image_path: str,
    has_arrow_parameters: bool = False,
    footnote_dependencies: list = None,
    markdown_table_content: str = None,
    progress_callback: Any = None,
) -> dict:
    """
    Handles reaction images with variant product sets (Situation 1).
    Uses ChemIEToolkit for molecule identification and RxnScribe for the reaction backbone,
    then asks the LLM to map labels to the correct SMILES for each variant.
    """
    from miner.intelligence.engine import load_prompt_with_commons, _build_context_hints
    from miner.prompt_logger import prompt_logger
    from miner.token_tracker import tracker

    client, config = _build_client()
    model_name = config.get("model", {}).get("agents", {}).get("r_group_model", "openai/gpt-4o")

    with open(image_path, "rb") as fh:
        b64 = base64.b64encode(fh.read()).decode('utf-8')

    tools = [
        {
            'type': 'function',
            'function': {
                'name': 'extract_molecular_text_to_correct',
                'description': 'Extracts SMILES and text corefs from molecules in the image.',
                'parameters': {
                    'type': 'object',
                    'properties': {
                        'image_path': {'type': 'string', 'description': 'Path to the reaction image.'}
                    },
                    'required': ['image_path'],
                    'additionalProperties': False,
                },
            },
        },
        {
            'type': 'function',
            'function': {
                'name': 'get_reaction',
                'description': 'Get reactants, conditions, and products from a reaction image.',
                'parameters': {
                    'type': 'object',
                    'properties': {
                        'image_path': {'type': 'string', 'description': 'Path to the reaction image.'}
                    },
                    'required': ['image_path'],
                    'additionalProperties': False,
                },
            },
        },
    ]

    context_hints = _build_context_hints(
        has_arrow_parameters=has_arrow_parameters,
        footnote_dependencies=footnote_dependencies,
    )
    prompt = load_prompt_with_commons('prompt.txt') + context_hints
    user_content = [
        {'type': 'text', 'text': prompt},
        {'type': 'image_url', 'image_url': {'url': f'data:image/png;base64,{b64}'}},
    ]
    messages = [
        {'role': 'system', 'content': 'You are a helpful assistant.'},
        {'role': 'user', 'content': user_content},
    ]

    resp1 = client.chat.completions.create(
        model=model_name, temperature=0, messages=messages, tools=tools,
    )
    try:
        prompt_logger.log("RGroupAgent_ToolCall", model_name, messages,
                          resp1.choices[0].message.content or "tool call")
    except Exception:
        pass
    if hasattr(resp1, 'usage') and resp1.usage:
        tracker.record("RGroupAgent_ToolCall", model_name,
                       resp1.usage.prompt_tokens or 0, resp1.usage.completion_tokens or 0)

    _empty = {"reaction_template": {"reactants": [], "products": []}, "reactions": {}, "original_molecule_list": []}

    if not resp1 or not resp1.choices:
        print("Error: invalid API response from variant agent (phase 1).")
        return _empty

    assistant_msg = resp1.choices[0].message
    if not assistant_msg.tool_calls:
        print("Warning: no tool calls from variant agent — returning empty structure.")
        return _empty

    tool_map = {
        'extract_molecular_text_to_correct': lambda img: extract_molecular_text_to_correct(img),
        'get_reaction': get_reaction,
    }

    tool_results = []
    for tc in assistant_msg.tool_calls:
        try:
            repair_json(tc.function.arguments)
        except json.JSONDecodeError as e:
            save_crash_dump("R-group Variant (Tool Args)", model_name, config, tc.function.arguments, e)
            raise ValueError(f"Malformed tool args from {model_name}: {e}")
        if tc.function.name not in tool_map:
            raise ValueError(f"Unknown tool: {tc.function.name}")
        result = tool_map[tc.function.name](image_path)
        tool_results.append({
            'role': 'tool',
            'content': json.dumps({'image_path': image_path, tc.function.name: result}),
            'tool_call_id': tc.id,
        })

    phase2_messages = [
        {'role': 'system', 'content': 'You are a helpful assistant.'},
        {'role': 'user', 'content': user_content},
        assistant_msg,
        *tool_results,
    ]
    resp2 = client.chat.completions.create(
        model=model_name, temperature=0, messages=phase2_messages,
    )
    try:
        prompt_logger.log("RGroupAgent_Completion", model_name, phase2_messages,
                          resp2.choices[0].message.content or "")
    except Exception:
        pass
    if hasattr(resp2, 'usage') and resp2.usage:
        tracker.record("RGroupAgent_Completion", model_name,
                       resp2.usage.prompt_tokens or 0, resp2.usage.completion_tokens or 0)

    if not resp2 or not resp2.choices or not resp2.choices[0].message.content:
        print("Error: empty response from variant agent (phase 2).")
        return _empty

    try:
        gpt_output = repair_json(resp2.choices[0].message.content)
    except json.JSONDecodeError as e:
        save_crash_dump("R-group Variant (Main)", model_name, config, resp2.choices[0].message.content, e)
        raise ValueError(f"Malformed JSON from {model_name}: {e}")

    # Gather coref and reaction data (from cache — no repeated model runs)
    coref_results = get_cached_multi_molecular(image_path)
    raw_results = get_cached_raw_results(image_path)
    if not raw_results:
        reaction_results = [{"reactions": [{"reactants": [], "conditions": [], "products": []}]}]
    else:
        rxn = {
            "reactants": raw_results[0].get('reactants', []),
            "conditions": raw_results[0].get('conditions', []),
            "products": raw_results[0].get('products', []),
        }
        reaction_results = [{"reactions": [rxn]}]

    # Pull flat SMILES arrays from the RxnScribe reaction
    reactants_array, products_array = [], []
    if reaction_results and reaction_results[0].get('reactions'):
        first_rxn = reaction_results[0]['reactions'][0]
        reactants_array = [r['smiles'] for r in first_rxn.get('reactants', []) if 'smiles' in r]
        products_array = [p['smiles'] for p in first_rxn.get('products', []) if 'smiles' in p]

    # Map each LLM label back to its full atom data for SMILES lookup
    smiles_details = {}
    for smiles in gpt_output if isinstance(gpt_output, list) else []:
        for detail in coref_results:
            for bbox in detail.get('bboxes', []):
                if bbox.get('smiles') == smiles:
                    smiles_details[smiles] = {k: bbox.get(k) for k in
                                              ('category', 'bbox', 'category_id', 'score', 'molfile', 'atoms', 'bonds')}
                    break

    backed_out = utils.backout_without_coref(
        reaction_results, coref_results, gpt_output, smiles_details, get_toolkit().molscribe
    )
    backed_out.sort(key=lambda x: x[2])

    extracted_rxns = {}
    for reactants, products_, label in backed_out:
        extracted_rxns[label] = {'reactants': reactants, 'products': products_}

    # Strip heavy fields before returning
    for item in coref_results:
        for bbox in item.get("bboxes", []):
            for key in ("category", "molfile", "symbols", "atoms", "bonds",
                        "category_id", "score", "corefs", "coords", "edges"):
                bbox.pop(key, None)

    data = coref_results[0] if coref_results else {"bboxes": [], "corefs": []}
    parsed = parse_coref_data_with_fallback(data)

    result = {
        "reaction_template": {"reactants": reactants_array, "products": products_array},
        "reactions": dict(sorted(extracted_rxns.items())),
        "original_molecule_list": parsed,
    }
    return result


# ── Situation 2: Text-based R-group substitution tables ───────────────────────

def process_reaction_table_data(
    image_path: str,
    markdown_table_content: str = None,
    supporting_image_paths: list = None,
    footnote_dependencies: list = None,
    has_arrow_parameters: bool = False,
    has_r_group_substitution: bool = False,
    progress_callback: Any = None,
    model_name: str = "openai/gpt-4o",
) -> dict:
    """
    Handles optimization tables with R-group text substitutions (Situation 2).
    Runs RxnScribe via tool call, then asks the LLM to map each table row's
    symbol substitutions onto the OCSR-derived reaction backbone.
    """
    from miner.intelligence.engine import load_prompt_with_commons, _build_context_hints
    from miner.prompt_logger import prompt_logger
    from miner.token_tracker import tracker

    print(f"DEBUG: Sending image to LLM: {image_path}")
    client, config = _build_client()
    model_name = config.get("model", {}).get("agents", {}).get("r_group_model", "openai/gpt-4o")

    with open(image_path, "rb") as fh:
        b64 = base64.b64encode(fh.read()).decode('utf-8')

    context_hints = _build_context_hints(
        has_arrow_parameters=has_arrow_parameters,
        has_r_group_substitution=has_r_group_substitution,
        footnote_dependencies=footnote_dependencies,
    )
    prompt = load_prompt_with_commons('prompt_reaction_withR.txt') + context_hints

    if markdown_table_content:
        prompt += f"\n\nMarkdown table (use this for accurate row extraction):\n{markdown_table_content}"

    user_content = [
        {'type': 'text', 'text': prompt},
        {'type': 'image_url', 'image_url': {'url': f'data:image/png;base64,{b64}'}},
    ]

    if supporting_image_paths:
        user_content.append({'type': 'text', 'text': "Reference images defining structures/labels used in this table:"})
        for path in supporting_image_paths:
            if path and os.path.exists(path):
                with open(path, "rb") as fh:
                    s_b64 = base64.b64encode(fh.read()).decode('utf-8')
                user_content.append({'type': 'image_url', 'image_url': {'url': f'data:image/png;base64,{s_b64}'}})

    messages = [
        {'role': 'system', 'content': 'You are a helpful assistant.'},
        {'role': 'user', 'content': user_content},
    ]

    tools = [{
        'type': 'function',
        'function': {
            'name': 'extract_full_reaction_schema',
            'description': 'Get reactants, conditions, and products from a reaction image.',
            'parameters': {
                'type': 'object',
                'properties': {
                    'image_path': {'type': 'string', 'description': 'Path to the reaction image.'}
                },
                'required': ['image_path'],
                'additionalProperties': False,
            },
        },
    }]

    resp1 = client.chat.completions.create(
        model=model_name, temperature=0,
        messages=messages, tools=tools, tool_choice="required",
    )
    try:
        prompt_logger.log("RGroupAgent_TableToolCall", model_name, messages,
                          resp1.choices[0].message.content or "tool call")
    except Exception:
        pass
    if hasattr(resp1, 'usage') and resp1.usage:
        tracker.record("RGroupAgent_TableToolCall", model_name,
                       resp1.usage.prompt_tokens or 0, resp1.usage.completion_tokens or 0)

    _empty = {"reaction_template": {"reactants": [], "products": []}, "reactions": {}, "original_molecule_list": []}

    if not resp1 or not resp1.choices:
        print("Error: invalid API response from table agent (phase 1).")
        return _empty

    # Fallback: LLM returned JSON directly instead of a tool call
    if not resp1.choices[0].message.tool_calls:
        print("Warning: no tool call from table agent — attempting JSON fallback.")
        try:
            raw_content = resp1.choices[0].message.content or ""
            if raw_content.strip().startswith("```"):
                raw_content = raw_content.strip().split("```")[1]
                if raw_content.startswith("json"):
                    raw_content = raw_content[4:]
            llm_json = repair_json(raw_content)
        except json.JSONDecodeError as e:
            save_crash_dump("R-group Table (Fallback)", model_name, config, raw_content, e)
            llm_json = {}

        tool_result = _extract_full_reaction_schema_with_timeout(image_path)
        if not tool_result:
            return _empty

        reactions_list = (tool_result.get("reaction_prediction")
                          if isinstance(tool_result, dict) and "reaction_prediction" in tool_result
                          else tool_result)
        if not reactions_list:
            return _empty
        input1 = reactions_list[0] if isinstance(reactions_list, list) else {}
        if not input1:
            return _empty
        return replace_symbols_and_generate_smiles(input1, llm_json)

    # Normal path: execute the tool call
    tc = resp1.choices[0].message.tool_calls[0]
    # Trim overlong tool call IDs (some providers return > 40 chars)
    if tc.id and len(tc.id) > 40:
        tc.id = tc.id[-40:]

    tool_args_raw = tc.function.arguments
    if "```json" in tool_args_raw:
        tool_args_raw = tool_args_raw.split("```json")[-1].split("```")[0].strip()
    elif "```" in tool_args_raw:
        tool_args_raw = tool_args_raw.split("```")[1].split("```")[0].strip()
    try:
        repair_json(tool_args_raw)
    except json.JSONDecodeError as e:
        raise ValueError(f"Malformed tool args from {model_name}: {e}")

    tool_result = _extract_full_reaction_schema_with_timeout(image_path)
    if tool_result is None:
        print("[Timeout] extract_full_reaction_schema timed out — returning empty.")
        return _empty

    tool_result_msg = {
        'role': 'tool',
        'content': json.dumps({'image_path': image_path, 'extract_full_reaction_schema': tool_result}),
        'tool_call_id': tc.id,
    }

    phase2_messages = [
        {'role': 'system', 'content': 'You are a helpful assistant.'},
        {'role': 'user', 'content': user_content},
        resp1.choices[0].message,
        tool_result_msg,
    ]
    resp2 = client.chat.completions.create(
        model=model_name, temperature=0, messages=phase2_messages,
    )
    try:
        prompt_logger.log("RGroupAgent_TableCompletion", model_name, phase2_messages,
                          resp2.choices[0].message.content or "")
    except Exception:
        pass
    if hasattr(resp2, 'usage') and resp2.usage:
        tracker.record("RGroupAgent_TableCompletion", model_name,
                       resp2.usage.prompt_tokens or 0, resp2.usage.completion_tokens or 0)

    reaction_preds = tool_result.get('reaction_prediction', tool_result)
    if isinstance(reaction_preds, str):
        try:
            reaction_preds = repair_json(reaction_preds)
        except json.JSONDecodeError as e:
            raise ValueError(f"Malformed OCSR output: {e}")

    if not reaction_preds:
        print("Warning: no reactions in tool result — returning empty.")
        return _empty

    if not resp2 or not resp2.choices:
        print("Error: invalid API response from table agent (phase 2).")
        return _empty

    input1 = reaction_preds[0] if isinstance(reaction_preds, list) else reaction_preds
    try:
        input2 = repair_json(resp2.choices[0].message.content)
    except json.JSONDecodeError as e:
        save_crash_dump("R-group Table (Final)", model_name, phase2_messages, resp2.choices[0].message.content, e)
        raise ValueError(f"Malformed JSON from {model_name}: {e}")

    return replace_symbols_and_generate_smiles(input1, input2)
