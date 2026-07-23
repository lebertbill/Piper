import os
import json
import base64
import copy
from typing import Any

import torch
import numpy as np
from PIL import Image
from openai import OpenAI

from context import load_config, get_openrouter_url
from miner.intelligence.core.model_registry import get_toolkit, get_rxnscribe
from miner.intelligence.core.molnextr.chemistry import _convert_graph_to_smiles
from miner.intelligence.utils.json_utils import repair_json
from miner.intelligence.utils.logging_utils import save_crash_dump


# ── Helpers shared by all three pipeline variants ─────
def _build_client():
    api_key = (
        os.environ.get("OPENROUTER_API_KEY")
        or os.environ.get("API_KEY") # Not used
        or os.environ.get("OPENAI_API_KEY") # Not used
    )
    return OpenAI(api_key=api_key, base_url=get_openrouter_url()), load_config()


def _get_coref_list(image_path: str) -> list:
    """Raw ChemIEToolkit output for an image — list of coref dicts."""
    image = Image.open(image_path).convert('RGB')
    return get_toolkit().extract_molecule_corefs_from_figures([image], molscribe=True, ocr=True)


def _merge_atom_symbols(llm_corrections: list, coref_data: list) -> list:
    """Copies LLM-corrected atom symbols into the OCSR coref data in-place."""
    for item1, item2 in zip(llm_corrections, coref_data):
        bboxes1 = item1.get('bboxes', [])
        bboxes2 = item2.get('bboxes', [])
        if len(bboxes1) != len(bboxes2):
            print("Warning: bbox count mismatch between LLM output and coref data.")
            continue
        for b1, b2 in zip(bboxes1, bboxes2):
            if 'symbols' not in b1:
                continue
            b2['symbols'] = b1['symbols']
            for atom, sym in zip(b2.get('atoms', []), b1['symbols']):
                atom['atom_symbol'] = sym
    return coref_data


def _merge_corefs_with_symbols(llm_corrections: list, coref_data: list) -> list:
    """
    Rebuilds the coref structure using LLM bbox assignments.
    Used for multi-R reactions where the standard positional merge isn't reliable.
    """
    results = []
    for item1, item2 in zip(llm_corrections, coref_data):
        orig_bboxes = item2.get('bboxes', [])
        orig_corefs = item2.get('corefs', [])
        coord2idx = {tuple(bb['bbox']): i for i, bb in enumerate(orig_bboxes)}

        new_bboxes = []
        for bb1 in item1.get('bboxes', []):
            coord = tuple(bb1['bbox'])
            if coord not in coord2idx:
                raise ValueError(f"Mol bbox {coord} not found in OCSR output.")
            bb_new = copy.deepcopy(orig_bboxes[coord2idx[coord]])
            if 'symbols' in bb1:
                bb_new['symbols'] = bb1['symbols']
                for atom, sym in zip(bb_new.get('atoms', []), bb1['symbols']):
                    atom['atom_symbol'] = sym
            if 'text' in bb1:
                bb_new['text'] = bb1['text']
            bb_new['bbox'] = bb1['bbox']
            new_bboxes.append(bb_new)

        coord2new: dict = {}
        for idx, bb in enumerate(new_bboxes):
            coord2new.setdefault(tuple(bb['bbox']), []).append(idx)

        new_corefs = []
        for group in orig_corefs:
            label_coord = tuple(orig_bboxes[group[-1]]['bbox'])
            new_label_idx = coord2new[label_coord][-1]
            for mol_idx in group[:-1]:
                mol_coord = tuple(orig_bboxes[mol_idx]['bbox'])
                for new_mol_idx in coord2new[mol_coord]:
                    new_corefs.append([new_mol_idx, new_label_idx])

        new_item = copy.deepcopy(item2)
        new_item['bboxes'] = new_bboxes
        new_item['corefs'] = new_corefs
        results.append(new_item)
    return results


def _regen_smiles(data: list, conversion_fn) -> list:
    """Re-runs graph-to-SMILES on every bbox that has the required atom graph fields."""
    for item in data:
        for bbox in item.get('bboxes', []):
            if all(k in bbox for k in ('coords', 'symbols', 'edges')):
                smiles, molfile, _ = conversion_fn(bbox['coords'], bbox['symbols'], bbox['edges'])
                bbox['smiles'] = smiles
                bbox['molfile'] = molfile
    return data


# Tool spec sent to the LLM in Phase 1
_MOL_TOOL = [{
    'type': 'function',
    'function': {
        'name': 'extract_molecular_data_with_atoms',
        'description': 'Extracts SMILES, atom symbols, and text corefs from a reaction image.',
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


def _run_mol_pipeline(image_path: str, prompt_name: str, log_prefix: str, multi_r: bool = False) -> list:
    """
    Two-phase LLM + OCSR pipeline shared by all three public mol-agent functions.
    Phase 1 — LLM calls the OCSR tool to get raw coref data.
    Phase 2 — LLM interprets the result and corrects atom symbols.
    The corrected symbols are then merged back into the raw coref data and SMILES are regenerated.
    """
    from miner.intelligence.engine import load_prompt_with_commons
    from miner.prompt_logger import prompt_logger
    from miner.token_tracker import tracker

    client, config = _build_client()
    model_name = config.get("model", {}).get("agents", {}).get("molecular_model", "openai/gpt-4o")

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

    # Phase 1: LLM selects and calls the OCSR tool
    resp1 = client.chat.completions.create(
        model=model_name, temperature=0,
        response_format={'type': 'json_object'},
        messages=messages, tools=_MOL_TOOL,
    )
    try:
        prompt_logger.log(f"{log_prefix}_ToolCall", model_name, messages,
                          resp1.choices[0].message.content or "tool call")
    except Exception:
        pass

    tool_results = []
    for tc in (resp1.choices[0].message.tool_calls or []):
        try:
            repair_json(tc.function.arguments)
        except json.JSONDecodeError as e:
            save_crash_dump(f"{log_prefix} (Tool Args)", model_name, config, tc.function.arguments, e)
            raise ValueError(f"Malformed tool args from {model_name}: {e}")
        ocsr_result = extract_molecular_data_with_atoms(image_path)
        tool_results.append({
            'role': 'tool',
            'content': json.dumps({'image_path': image_path, 'extract_molecular_data_with_atoms': ocsr_result}),
            'tool_call_id': tc.id,
        })

    # Phase 2: LLM interprets OCSR output and corrects atom symbols
    phase2_messages = [
        {'role': 'system', 'content': 'You are a helpful assistant.'},
        {'role': 'user', 'content': user_content},
        resp1.choices[0].message,
        *tool_results,
    ]
    resp2 = client.chat.completions.create(
        model=model_name, temperature=0,
        response_format={'type': 'json_object'},
        messages=phase2_messages,
    )
    try:
        prompt_logger.log(f"{log_prefix}_Completion", model_name, phase2_messages,
                          resp2.choices[0].message.content or "")
    except Exception:
        pass

    try:
        llm_output = [repair_json(resp2.choices[0].message.content)]
    except json.JSONDecodeError as e:
        save_crash_dump(f"{log_prefix} (Main)", model_name, config, resp2.choices[0].message.content, e)
        raise ValueError(f"Malformed JSON from {model_name}: {e}")

    # Merge LLM symbol corrections into raw OCSR coref data, then regenerate SMILES
    coref_data = _get_coref_list(image_path)
    merge_fn = _merge_corefs_with_symbols if multi_r else _merge_atom_symbols
    merged = merge_fn(llm_output, coref_data)
    return _regen_smiles(merged, _convert_graph_to_smiles)


# ── Public tool functions (called by other agents via tool dispatch) ──────

def get_multi_molecular(image_path: str) -> str:
    """Returns raw coref data for all molecules in the image as a JSON string."""
    image = Image.open(image_path).convert('RGB')
    coref_results = get_toolkit().extract_molecule_corefs_from_figures([image], molscribe=True, ocr=True)
    for item in coref_results:
        for bbox in item.get("bboxes", []):
            for key in ("category", "molfile", "symbols", "atoms", "bonds",
                        "category_id", "score", "corefs"):
                bbox.pop(key, None)
    return json.dumps(coref_results)


def extract_molecular_text_to_correct(image_path: str) -> list:
    """Strips low-level fields from coref data, leaving only SMILES and text labels."""
    coref_results = get_toolkit().extract_molecule_corefs_from_figures(
        [Image.open(image_path).convert('RGB')], molscribe=True, ocr=True
    )
    for item in coref_results:
        for bbox in item.get("bboxes", []):
            for key in ("category", "bbox", "molfile", "symbols", "atoms", "bonds",
                        "category_id", "score", "corefs"):
                bbox.pop(key, None)
    return json.dumps(coref_results)


def extract_molecular_data_with_atoms(image_path: str) -> str:
    """Full coref extraction with atom-level data — used as the OCSR tool in Phase 1."""
    image = Image.open(image_path).convert('RGB')
    coref_results = get_toolkit().extract_molecule_corefs_from_figures([image], molscribe=True, ocr=True)
    for item in coref_results:
        for bbox in item.get("bboxes", []):
            for key in ("coords", "edges", "molfile", "atoms", "bonds",
                        "category_id", "score", "corefs"):
                bbox.pop(key, None)
    return json.dumps(coref_results)


# ── Public pipeline functions ───────────────────────

def process_reaction_multiple_products(image_path: str, progress_callback: Any = None) -> list:
    return _run_mol_pipeline(image_path, 'prompt_getmolecular.txt', 'MolAgent_Generic')


def process_reaction_multiple_products_correctR(image_path: str, progress_callback: Any = None) -> list:
    return _run_mol_pipeline(image_path, 'prompt_getmolecular_correctR.txt', 'MolAgent_CorrectR')


def process_reaction_multiple_products_correctmultiR(image_path: str, progress_callback: Any = None) -> list:
    return _run_mol_pipeline(image_path, 'prompt_getmolecular_correctmultiR.txt', 'MolAgent_MultiR', multi_r=True)
