import json
import os
from typing import Dict
from context import load_config, get_openrouter_url
# Import the helper functions from the new utility file
from llm_utils import _post_with_retry, _ollama_with_retry

VALID_QUERY_TYPES = ['general_query', 'list_query', 'filtered_query', 'recommendation_query']

def load_prompt(filename: str) -> str:
    import os
    prompt_path = os.path.join(os.path.dirname(__file__), "prompts", filename)
    try:
        with open(prompt_path, "r", encoding="utf-8") as f:
            return f.read()
    except Exception as e:
        print(f"Failed to load prompt {filename}: {e}")
        return ""


async def _classify_query_with_fallback(query: str, model_override: str = None) -> str:
    """
    A wrapper for classify_query that includes a fallback mechanism.
    """
    try:
        classification = await classify_query(query, model_override=model_override)
    except Exception as e:
        print(f"⚠️ Query classifier raised {type(e).__name__}: {e}. Defaulting to 'general_query'.")
        return 'general_query'
    if classification not in VALID_QUERY_TYPES:
        print(f"⚠️ LLM classifier returned an unexpected value: '{classification}'. Defaulting to 'general_query'.")
        return 'general_query'
    print(f"✅ Query classified as: {classification}")
    return classification


async def classify_query(query: str, model_override: str = None) -> str:
    """
    Classifies the user's query into one of three types using an LLM.
    """
    config = load_config()
    model_config = config.get("model", {})
    mode = model_config.get("mode")
    model_name = model_override or model_config.get("model_name")
    api_key = os.environ.get("OPENROUTER_API_KEY")
    openrouter_url = get_openrouter_url()

    base_prompt = load_prompt("query_classification_prompt.txt")
    prompt = base_prompt.format(query=query)

    print(f"🧠 Classifying query: '{query}'")

    if mode == "local":
        response = await _ollama_with_retry(prompt, model_name)
    else:
        if not api_key:
            raise ValueError("API key not found. Please set OPENROUTER_API_KEY in your .env file for remote mode.")
        chat_url = openrouter_url.rstrip("/") + "/chat/completions"
        headers = {"Authorization": f"Bearer {api_key}"}
        payload = {"model": model_name, "messages": [{"role": "user", "content": prompt}]}
        data = await _post_with_retry(chat_url, headers, payload)
        if "choices" not in data:
            raise RuntimeError(f"Unexpected API response (no 'choices'): {str(data)[:200]}")
        response = data['choices'][0]['message']['content'] or ""

    # ── Log the prompt and response ──
    from miner.prompt_logger import prompt_logger
    try:
        prompt_logger.log("Query_Classifier", model_name, [{"role": "user", "content": prompt}], response)
    except Exception:
        pass

    classification = response.strip().replace("'", "").replace('"', '').lower()
    return classification


async def extract_entities(query: str, model_override: str = None) -> Dict:
    """
    Extracts metadata entities from a user query using an LLM.
    """
    config = load_config()
    model_config = config.get("model", {})
    mode = model_config.get("mode")
    model_name = model_override or model_config.get("model_name")
    api_key = os.environ.get("OPENROUTER_API_KEY")
    openrouter_url = get_openrouter_url()

    base_prompt = load_prompt("entity_extraction_prompt.txt")
    prompt = base_prompt.format(query=query)

    print(f"🧠 Extracting entities from query: '{query}'")

    if mode == "local":
        response = await _ollama_with_retry(prompt, model_name)
    else:
        if not api_key:
            raise ValueError("API key not found. Please set OPENROUTER_API_KEY in your .env file for remote mode.")
        chat_url = openrouter_url.rstrip("/") + "/chat/completions"
        headers = {"Authorization": f"Bearer {api_key}"}
        payload = {"model": model_name, "messages": [{"role": "user", "content": prompt}]}
        data = await _post_with_retry(chat_url, headers, payload)
        if "choices" not in data:
            raise RuntimeError(f"Unexpected API response (no 'choices'): {str(data)[:200]}")
        response = data['choices'][0]['message']['content'] or ""

    # ── Log the prompt and response ──
    from miner.prompt_logger import prompt_logger
    try:
        prompt_logger.log("Entity_Extractor", model_name, [{"role": "user", "content": prompt}], response)
    except Exception:
        pass

    try:
        json_response = response.strip().replace("```json", "").replace("```", "")
        entities = json.loads(json_response)
        # Clean the dictionary by removing any keys where the value is None.
        cleaned_entities = {k: v for k, v in entities.items() if v is not None}
        print(f"✅ Entities extracted: {cleaned_entities}")
        return cleaned_entities
    except json.JSONDecodeError:
        print(f"⚠️ Could not decode JSON from entity extraction response: {response}")
        return {}
