import os
import json
from typing import Any
from openai import OpenAI
from context import load_config, get_openrouter_url

# Maximum characters to send to the abbreviation LLM (keeps costs/latency low)
_MAX_INPUT_CHARS = 15_000

def get_abbreviation_agent(text_content: str, progress_callback: Any = None) -> dict:
    """
    Extracts abbreviations and their full names from the provided text content.
    
    Args:
        text_content (str): The text content (e.g., markdown table or full paper text).
        progress_callback (callable, optional): UI 。
        
    Returns:
        dict: A dictionary mapping abbreviations to their full names.
              Returns empty dict on any failure.
    """
    if not text_content or not text_content.strip():
        return {}

    config = load_config()
    api_key = os.environ.get("OPENROUTER_API_KEY") or os.environ.get("OPENAI_API_KEY") or config.get("api_key")
    base_url = get_openrouter_url()
    
    if not api_key:
        try:
            from dotenv import load_dotenv
            load_dotenv()
            api_key = os.environ.get("OPENROUTER_API_KEY") or os.environ.get("OPENAI_API_KEY")
        except ImportError:
            pass

    if not api_key:
        print("Warning: API Key not found. Abbreviation agent skipped.")
        return {}

    client = OpenAI(
        api_key=api_key,
        base_url=base_url
    )

    # Truncate very long texts to avoid context window issues
    truncated_text = text_content[:_MAX_INPUT_CHARS]
    if len(text_content) > _MAX_INPUT_CHARS:
        truncated_text += "\n... [truncated]"

    # Load prompt from file
    import os
    prompt_path = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(__file__))), "prompts", "abbreviation_extraction_prompt.txt")
    try:
        with open(prompt_path, "r", encoding="utf-8") as f:
            base_prompt = f.read()
            prompt = base_prompt.format(truncated_text=truncated_text)
    except Exception as e:
        print(f"Failed to load prompt: {e}")
        prompt = f"Identify abbreviations in: {truncated_text}"
    
    model_name = config.get("model", {}).get("agents", {}).get("text_reaction_model", "openai/gpt-5-nano")
    
    messages = [
        {"role": "system", "content": "You are a chemistry assistant. Return only valid JSON."},
        {"role": "user", "content": prompt}
    ]
    
    try:
        response = client.chat.completions.create(
            model=model_name,
            messages=messages,
            temperature=0,
            max_tokens=2000
        )

        # ── Log the prompt and response ──
        from miner.prompt_logger import prompt_logger
        try:
            content = response.choices[0].message.content or "LLM Call (no direct content)"
            prompt_logger.log("AbbrAgent_Completion", model_name, messages, content)
        except Exception:
            pass
        raw_content = response.choices[0].message.content
        
        # ── Track token usage ──
        from miner.token_tracker import tracker
        if hasattr(response, 'usage') and response.usage:
            tracker.record(
                "AbbreviationAgent",
                model_name,
                response.usage.prompt_tokens or 0,
                response.usage.completion_tokens or 0,
            )
        
        if not raw_content or not raw_content.strip():
            print("Warning: Abbreviation agent returned empty response. Using empty dict.")
            return {}

        from miner.intelligence.utils.json_utils import repair_json

        result = repair_json(raw_content)
        if isinstance(result, dict):
            # Filter out any non-string values
            cleaned = {k: v for k, v in result.items() if isinstance(k, str) and isinstance(v, str)}
            print(f"  -> Abbreviation agent extracted {len(cleaned)} abbreviations.")
            return cleaned
        else:
            print(f"Warning: Abbreviation agent returned non-dict: {type(result)}. Skipping.")
            return {}

    except Exception as e:
        from miner.intelligence.utils.logging_utils import save_crash_dump
        save_crash_dump("Abbreviation Agent", model_name, {"prompt_length": len(prompt)}, str(e), e)
        print(f"Warning: Abbreviation agent failed ({type(e).__name__}: {e}). Continuing with empty dict.")
        return {}

