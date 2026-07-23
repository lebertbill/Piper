import logging
import re
from typing import List

from models import _invoke_llm
from context import load_config

_log = logging.getLogger(__name__)


def load_prompt(filename: str) -> str:
    import os
    prompt_path = os.path.join(os.path.dirname(__file__), "prompts", filename)
    try:
        with open(prompt_path, "r", encoding="utf-8") as f:
            return f.read()
    except Exception as e:
        _log.error(f"Failed to load prompt {filename}: {e}")
        return ""


async def summarizer_agent(markdown_content: str) -> str:
    """Summarises the catalysts and reactions described in the document."""
    _log.info("Activating Summarizer Agent...")
    config = load_config()
    model_name = config.get("model", {}).get("agents", {}).get("summarizer_model")
    prompt = load_prompt("paper_summary_prompt.txt").format(markdown_content=markdown_content)
    try:
        summary = await _invoke_llm(prompt, model_name_override=model_name)
    except Exception as e:
        _log.error(f"Summarizer Agent failed: {e}")
        raise
    _log.info("Summarizer Agent finished.")
    return summary


async def image_grabber_agent(markdown_content: str) -> List[str]:
    """
            !Advanced structure parser is more authoritative!
    Selects the relevant figures and tables for chemistry extraction.

    Preference order:
    1. Detect optimization tables by keyword in the markdown and pair them with
       the nearest preceding scheme/figure image.
    2. Fall back to an LLM call when no optimization tables are found.
    """
    _log.info("Activating Image Grabber Agent...")
    config = load_config()
    model_name = config.get("model", {}).get("agents", {}).get("image_grabber_model")

    table_pattern = (
        r'(Table\s+\d+\.?\s*[^\n]*(?:Optimization|Reaction Conditions)[^\n]*)\n\n'
        r'(\|[^\n]+\|(?:\n\|[^\n]+\|)+)'
    )
    table_matches = list(re.finditer(table_pattern, markdown_content, re.IGNORECASE))

    selected_images = []

    for match in table_matches:
        table_caption = match.group(1).strip()
        table_name_match = re.match(r'(Table\s+\d+)', table_caption, re.IGNORECASE)
        if not table_name_match:
            continue

        table_name = table_name_match.group(1)
        selected_images.append(table_name)

        text_before = markdown_content[max(0, match.start() - 2000):match.start()]
        image_matches = list(re.finditer(r'!\[([^\]]*)\]\(([^\)]+)\)', text_before))
        if not image_matches:
            continue

        last_image = image_matches[-1]
        context = text_before[max(0, last_image.start() - 200):last_image.end() + 200]
        scheme_match = re.search(r'(Scheme\s+\d+|Figure\s+\d+)', context, re.IGNORECASE)
        if scheme_match:
            selected_images.insert(0, scheme_match.group(1))
        else:
            filename = last_image.group(2).split('/')[-1].replace('.png', '')
            selected_images.insert(0, f"Image:{filename}")

    if not selected_images:
        prompt = load_prompt("figure_identification_prompt.txt").format(
            markdown_content=markdown_content
        )
        response = await _invoke_llm(prompt, model_name_override=model_name)
        selected_images = [
            img.strip().replace('**', '').replace('*', '').replace('_', '').strip()
            for img in response.split(',')
            if img.strip()
        ]

    _log.info(f"Image Grabber Agent finished. Selected: {selected_images}")
    return selected_images
