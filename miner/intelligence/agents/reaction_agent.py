import os
import json
import base64
from typing import Any

import torch
from PIL import Image
from openai import OpenAI

from context import load_config, get_openrouter_url
from miner.intelligence.core.model_registry import get_toolkit, get_rxnscribe
from miner.intelligence.core.molnextr.chemistry import _convert_graph_to_smiles
from miner.intelligence.utils.json_utils import repair_json
from miner.intelligence.utils.logging_utils import save_crash_dump
from miner.intelligence.utils.api_client import call_llm_with_retry


def _build_client():
    api_key = (
        os.environ.get("OPENROUTER_API_KEY")
        or os.environ.get("API_KEY")
        or os.environ.get("OPENAI_API_KEY")
    )
    return OpenAI(api_key=api_key, base_url=get_openrouter_url()), load_config()


# ── Tool functions (called by the LLM via tool dispatch) ──────────────────────

def get_reaction(image_path: str) -> dict:
    """RxnScribe prediction for a single image — reactants, conditions, products with SMILES and bbox."""
    raw = get_rxnscribe().predict_image_file(image_path, molscribe=True, ocr=True)
    if not raw:
        return {}
    result = {}
    for section in ('reactants', 'conditions', 'products'):
        if section not in raw[0]:
            continue
        result[section] = []
        for item in raw[0][section]:
            if section in ('reactants', 'products'):
                result[section].append({
                    "smiles": item.get("smiles", ""),
                    "bbox": item.get("bbox", []),
                    "symbols": item.get("symbols", []),
                })
            else:
                entry = {"bbox": item.get("bbox", [])}
                if "smiles" in item:
                    entry["smiles"] = item.get("smiles", "")
                if "text" in item:
                    entry["text"] = item.get("text", [])
                result[section].append(entry)
    return result


def extract_full_reaction_schema(image_path: str, markdown_table_content: str = None) -> dict:
    """
    Full RxnScribe prediction plus ChemIEToolkit coref data.
    Returns {reaction_prediction: [...], molecule_coref: [...]}.
    """
    raw = get_rxnscribe().predict_image_file(image_path, molscribe=True, ocr=True)
    for rxn in raw:
        for section in ("reactants", "products", "conditions"):
            for entry in rxn.get(section, []):
                coords = entry.get("coords")
                if isinstance(coords, list):
                    entry["coords"] = [[round(v, 3) for v in pt] for pt in coords]
                for key in ("molfile", "atoms", "bonds"):
                    entry.pop(key, None)

    image = Image.open(image_path).convert('RGB')
    coref_results = get_toolkit().extract_molecule_corefs_from_figures([image], molscribe=True, ocr=True)
    for item in coref_results:
        for bbox in item.get("bboxes", []):
            for key in ("category", "molfile", "symbols", "atoms", "bonds",
                        "category_id", "score", "corefs", "coords", "edges"):
                bbox.pop(key, None)

    parsed = _parse_corefs(coref_results[0] if coref_results else {"bboxes": [], "corefs": []})
    return {"reaction_prediction": raw, "molecule_coref": parsed}


def _parse_corefs(data: dict) -> list:
    """Converts ChemIEToolkit coref pairs into [{smiles, texts, bbox}] format."""
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


# ── Internal pipeline ─────────────────────────────────────────────────────────

def _get_reaction_full(image_path: str) -> list:
    """Raw RxnScribe output with atom-level data intact — used for symbol back-projection."""
    return get_rxnscribe().predict_image_file(image_path, molscribe=True, ocr=True)


def _update_symbols_from_llm(llm_corrections: dict, raw_rxn: dict) -> dict:
    """
    Projects LLM-corrected atom symbols back into the raw RxnScribe output
    and regenerates SMILES from the corrected graph.
    Matching is done by bbox coordinates.
    """
    sym_map = {}
    for section in ('reactants', 'products'):
        for item in llm_corrections.get(section, []):
            sym_map[tuple(item['bbox'])] = item.get('symbols', [])

    for section in ('reactants', 'products'):
        for item in raw_rxn.get(section, []):
            bbox_key = tuple(item.get('bbox', []))
            if bbox_key not in sym_map:
                continue
            symbols = sym_map[bbox_key]
            item['symbols'] = symbols
            if 'atoms' in item:
                if len(item['atoms']) != len(symbols):
                    print(f"Warning: symbol/atom count mismatch at bbox {bbox_key}")
                else:
                    for atom, sym in zip(item['atoms'], symbols):
                        atom['atom_symbol'] = sym
            if 'coords' in item and 'edges' in item:
                smiles, molfile, _ = _convert_graph_to_smiles(item['coords'], symbols, item['edges'])
                item['smiles'] = smiles
                item['molfile'] = molfile
    return raw_rxn


_RXN_TOOL = [{
    'type': 'function',
    'function': {
        'name': 'get_reaction',
        'description': 'Get reactants, conditions, and products with SMILES and bboxes from a reaction image.',
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


def _run_rxn_pipeline(image_path: str, prompt_name: str, log_prefix: str,
                      progress_callback: Any = None) -> list:
    """
    Two-phase LLM + RxnScribe pipeline.
    Phase 1 — LLM calls get_reaction to retrieve the OCSR prediction.
    Phase 2 — LLM corrects atom symbols using visual context.
    The corrected symbols are back-projected into the raw prediction and SMILES regenerated.
    """
    from miner.intelligence.engine import load_prompt_with_commons
    from miner.prompt_logger import prompt_logger

    client, config = _build_client()
    model_name = config.get("model", {}).get("agents", {}).get("reaction_model", "openai/gpt-4o")

    with open(image_path, "rb") as fh:
        b64 = base64.b64encode(fh.read()).decode('utf-8')

    prompt = load_prompt_with_commons(prompt_name)
    user_content = [
        {'type': 'text', 'text': prompt},
        {'type': 'image_url', 'image_url': {'url': f'data:image/png;base64,{b64}'}},
    ]
    messages = [
        {'role': 'system', 'content': 'You are a helpful assistant.'},
        {'role': 'user', 'content': user_content},
    ]

    # Phase 1: LLM calls RxnScribe tool
    resp1 = call_llm_with_retry(
        client=client, progress_callback=progress_callback,
        model=model_name, temperature=0, messages=messages, tools=_RXN_TOOL,
    )
    try:
        prompt_logger.log(f"{log_prefix}_ToolCall", model_name, messages,
                          resp1.choices[0].message.content or "tool call")
    except Exception:
        pass

    if not resp1 or not resp1.choices or not resp1.choices[0].message.tool_calls:
        print(f"Warning: no tool calls from {log_prefix} Phase 1.")
        return []

    assistant_msg = resp1.choices[0].message
    tool_results = []
    for tc in assistant_msg.tool_calls:
        try:
            repair_json(tc.function.arguments)
        except json.JSONDecodeError as e:
            save_crash_dump(f"{log_prefix} (Tool Args)", model_name, config, tc.function.arguments, e)
            raise ValueError(f"Malformed tool args from {model_name}: {e}")
        result = get_reaction(image_path)
        tool_results.append({
            'role': 'tool',
            'content': json.dumps({'image_path': image_path, 'get_reaction': result}),
            'tool_call_id': tc.id,
        })

    # Phase 2: LLM corrects symbols using visual context
    phase2_messages = [
        {'role': 'system', 'content': 'You are a helpful assistant.'},
        {'role': 'user', 'content': user_content},
        assistant_msg,
        *tool_results,
    ]
    resp2 = call_llm_with_retry(
        client=client, progress_callback=progress_callback,
        model=model_name, temperature=0, messages=phase2_messages,
    )
    try:
        prompt_logger.log(f"{log_prefix}_Completion", model_name, phase2_messages,
                          resp2.choices[0].message.content or "")
    except Exception:
        pass

    if not resp2 or not resp2.choices or not resp2.choices[0].message.content:
        print(f"Warning: empty response from {log_prefix} Phase 2.")
        return []

    try:
        llm_output = repair_json(resp2.choices[0].message.content)
    except json.JSONDecodeError as e:
        save_crash_dump(f"{log_prefix} (Main)", model_name, config, resp2.choices[0].message.content, e)
        raise ValueError(f"Malformed JSON from {model_name}: {e}")

    # Back-project corrected symbols into raw RxnScribe output
    raw_full = _get_reaction_full(image_path)
    raw_item = raw_full[0] if raw_full else {}
    return [_update_symbols_from_llm(llm_output, raw_item)]


# ── Public pipeline functions ─────────────────────────────────────────────────

def get_reaction_withatoms(image_path: str, supporting_image_paths: list = None,
                           progress_callback: Any = None) -> list:
    return _run_rxn_pipeline(image_path, 'prompt_getreaction.txt', 'RxnAgent_Generic', progress_callback)


def get_reaction_withatoms_correctR(image_path: str, supporting_image_paths: list = None,
                                    progress_callback: Any = None,
                                    model_name: str = "openai/gpt-4o") -> list:
    return _run_rxn_pipeline(image_path, 'prompt_getreaction_correctR.txt', 'RxnAgent_CorrectR', progress_callback)
