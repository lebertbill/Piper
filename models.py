import os
import re
import json
import base64
from context import load_config, get_parameters, get_openrouter_url
from typing import List
from langchain_core.prompts import ChatPromptTemplate
from langchain_core.output_parsers import JsonOutputParser
from langchain_openai import ChatOpenAI
from llm_utils import _post_with_retry, _ollama_with_retry

#LLM calling layer for RAG (developed during the initial build phase) ! All the agentic calls should be invocked from here in future version ! (please dont change this code )
config = load_config()
model_name = config["model"]["rag_model_name"]
mode = config["model"]["mode"]  # these should be moved to context.py in future for a neat codebase
openrouter_url = get_openrouter_url()
max_summary_words = config["model"].get("max_summary_words", 250)

def load_prompt(filename: str) -> str:
    import os
    prompt_path = os.path.join(os.path.dirname(__file__), "prompts", filename)
    try:
        with open(prompt_path, "r", encoding="utf-8") as f:
            return f.read()
    except Exception as e:
        print(f"Failed to load prompt {filename}: {e}")
        return ""

async def _invoke_llm(prompt: str, model_name_override: str = None) -> str:
    """
    A centralized helper function to invoke the language model (local or remote).
    """
    target_model = model_name_override or model_name
    if mode == "local":
        return await _ollama_with_retry(prompt, target_model)
    else:
        api_key = os.environ.get("OPENROUTER_API_KEY")
        if not api_key:
            raise ValueError("API key not found. Please set OPENROUTER_API_KEY in your .env file for remote mode.")
        headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json"
        }
        payload = {
            "model": target_model,
            "messages": [{"role": "user", "content": prompt}]
        }

        full_url = openrouter_url
        if not full_url.endswith("/chat/completions"):
            full_url = f"{full_url.rstrip('/')}/chat/completions"
            
        data = await _post_with_retry(full_url, headers, payload)
        content = data['choices'][0]['message']['content'] or ""

        # ── Log the prompt and response (Consistent with user's BaseAgent change) ──
        from miner.prompt_logger import prompt_logger
        try:
            prompt_logger.log("RAG_Query", target_model, [{"role": "user", "content": prompt}], content)
        except Exception:
            pass

        return content


async def _invoke_llm_with_reasoning(prompt: str, model_name_override: str = None) -> tuple[str, str]:
    """
    Like _invoke_llm, but also returns the model's reasoning/thinking separately
    from the final answer text.

    Prefers OpenRouter's structured `reasoning` response field when a model
    populates it. Verified live: qwen/qwen3.5-flash-02-23 returns this field but
    leaves it empty, instead emitting its full chain-of-thought inline in
    `content` terminated by a literal `</think>` tag — so that's used as a
    fallback split when the structured field is empty.
    """
    target_model = model_name_override or model_name
    if mode == "local":
        answer = await _ollama_with_retry(prompt, target_model)
        return answer, ""

    api_key = os.environ.get("OPENROUTER_API_KEY")
    if not api_key:
        raise ValueError("API key not found. Please set OPENROUTER_API_KEY in your .env file for remote mode.")
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json"
    }
    payload = {
        "model": target_model,
        "messages": [{"role": "user", "content": prompt}],
        "reasoning": {"effort": "medium"},
    }

    full_url = openrouter_url
    if not full_url.endswith("/chat/completions"):
        full_url = f"{full_url.rstrip('/')}/chat/completions"

    data = await _post_with_retry(full_url, headers, payload)
    message = data['choices'][0]['message']
    content = message.get('content') or ""
    reasoning = message.get('reasoning') or ""

    if not reasoning and "</think>" in content:
        before, after = content.split("</think>", 1)
        before = before.split("<think>", 1)[-1]  # drop opening tag if present
        reasoning = before.strip()
        content = after.strip()

    from miner.prompt_logger import prompt_logger
    try:
        prompt_logger.log("RAG_Query", target_model, [{"role": "user", "content": prompt}], content)
    except Exception:
        pass

    return content, reasoning


async def _invoke_llm_vision(prompt: str, image_path: str, model_name_override: str = None) -> str:
    """
    Invoke an LLM with both text prompt and an image (base64-encoded).
    Used for summarizing multimodal chunks where data lives in the image (tables, schemes).
    Falls back to text-only if the image cannot be read.
    """
    target_model = model_name_override or model_name
    try:
        with open(image_path, "rb") as f:
            img_b64 = base64.b64encode(f.read()).decode("utf-8")
        ext = image_path.rsplit(".", 1)[-1].lower()
        mime = {"jpg": "image/jpeg", "jpeg": "image/jpeg", "png": "image/png",
                "gif": "image/gif", "webp": "image/webp"}.get(ext, "image/png")
        data_url = f"data:{mime};base64,{img_b64}"
    except Exception as e:
        print(f"[Vision] Could not load image {image_path}: {e} — falling back to text-only.")
        return await _invoke_llm(prompt, model_name_override)

    api_key = os.environ.get("OPENROUTER_API_KEY")
    if not api_key or mode == "local":
        return await _invoke_llm(prompt, model_name_override)

    full_url = openrouter_url
    if not full_url.endswith("/chat/completions"):
        full_url = f"{full_url.rstrip('/')}/chat/completions"

    headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}
    payload = {
        "model": target_model,
        "messages": [{
            "role": "user",
            "content": [
                {"type": "text", "text": prompt},
                {"type": "image_url", "image_url": {"url": data_url}},
            ],
        }],
    }
    data = await _post_with_retry(full_url, headers, payload)
    content = data["choices"][0]["message"]["content"] or ""

    from miner.prompt_logger import prompt_logger
    try:
        prompt_logger.log("RAG_Vision", target_model, [{"role": "user", "content": prompt}], content)
    except Exception:
        pass
    return content


def _short_citation(source: dict) -> str:
    """
    Build a short human-readable citation from chunk metadata.
    Falls back to parsing the folder-name title when authors/year are absent.
    """
    authors = source.get("authors", "") or ""
    year = source.get("year", "") or ""
    title = source.get("title", "") or source.get("paper", "")

    # If we have real authors, use last name of first author
    if authors:
        first = authors.split(",")[0].strip()
        last_name = first.split()[-1] if first else ""
        suffix = f" et al., {year}" if year else " et al."
        return f"{last_name}{suffix}"

    folder = title or ""
    # "Author[et al.] - YEAR"
    m = re.match(r'^([A-Za-z]+(?:\s+et\s+al\.?)?)[\s\-]+(\d{4})\b', folder)
    if m:
        return f"{m.group(1)}, {m.group(2)}"
    # Extract first author name (before underscore or space-dash)
    author_m = re.match(r'^([A-Za-z]+)', folder)
    first_author = author_m.group(1) if author_m else ""
    # Extract year: any 20XX anywhere in string (DOI suffixes like .202500240 include year)
    year_m = re.search(r'(20\d{2})', folder)
    yr = year_m.group(1) if year_m else year
    if first_author:
        return f"{first_author}, {yr}" if yr else first_author
    return folder[:50] if folder else "Unknown"


async def summarize_text(question: str, text: str, source: dict, model_source=None, model_name_override=None) -> str:
    title = source.get("title", "")
    authors = source.get("authors", "")
    journal = source.get("journal", "")
    publisher = source.get("publisher", "")
    year = source.get("year", "")
    doi = source.get("DOI", "")
    item_type = source.get("item_type", "")
    image_path = source.get("image_path", "")

    base_prompt = load_prompt("chunk_summary_prompt.txt")
    prompt = base_prompt.format(
        max_summary_words=max_summary_words,
        text=text,
        title=title,
        authors=authors,
        item_type=item_type,
        journal=journal,
        publisher=publisher,
        year=year,
        doi=doi,
        question=question
    )

    # For multimodal chunks (figures, tables, schemes), send the image alongside the text.
    # This is critical: reaction tables and scope figures store data only in the image,
    # so text-only summarization returns empty/irrelevant content.
    if image_path and item_type == "multimodal":
        import os as _os
        if _os.path.exists(image_path):
            raw = await _invoke_llm_vision(prompt, image_path, model_name_override)
        else:
            raw = await _invoke_llm(prompt, model_name_override)
    else:
        raw = await _invoke_llm(prompt, model_name_override)

    if not raw:
        return ""
    # Prepend citation header in Python — reliable, no LLM formatting needed
    cite = _short_citation(source)
    return f"[{cite}] {raw.strip()}"

def _format_conversation_history(messages: list, max_exchanges: int = 3) -> str:
    """Format the last N user/assistant exchange pairs as a context prefix."""
    recent = messages[-(max_exchanges * 2):]
    lines = ["Prior conversation (for context only — answer the current question below):"]
    for msg in recent:
        role = "User" if msg["role"] == "user" else "Assistant"
        content = msg["content"]
        if role == "Assistant" and len(content) > 600:
            content = content[:600] + "…"
        elif role == "User" and len(content) > 200:
            content = content[:200] + "…"
        lines.append(f"{role}: {content}")
    return "\n".join(lines)


async def generate_web_background(question: str, model_name_override: str = None) -> str | None:
    """
    Small, separate background-primer agent — NOT part of the corpus-grounded answer
    pipeline. Uses OpenRouter's "web" plugin (works for any model via server-side
    search injection, unlike the newer server-tool approach whose officially listed
    supported models don't include this app's default synthesis model) to optionally
    add a short general-knowledge primer alongside the paper-grounded answer.

    Returns a short primer string, or None if no background was warranted, the
    call failed, or the model declined (replied "NONE"). Never raises — any error
    is caught and logged so the real corpus-grounded answer always ships regardless.
    """
    if mode == "local":
        return None  # OpenRouter-only feature, no local equivalent

    api_key = os.environ.get("OPENROUTER_API_KEY")
    if not api_key:
        return None

    target_model = model_name_override or model_name
    cfg = load_config().get("model", {})
    max_results = cfg.get("web_search_max_results", 5)
    max_iterations = cfg.get("web_search_max_iterations", 3)

    base_prompt = load_prompt("web_background_prompt.txt")
    if not base_prompt:
        return None
    prompt = base_prompt.format(question=question)

    headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}
    payload = {
        "model": target_model,
        "messages": [{"role": "user", "content": prompt}],
        "plugins": [{"id": "web", "max_results": max_results}],
    }

    full_url = openrouter_url
    if not full_url.endswith("/chat/completions"):
        full_url = f"{full_url.rstrip('/')}/chat/completions"

    for attempt in range(max_iterations):
        try:
            data = await _post_with_retry(full_url, headers, payload)
            content = (data['choices'][0]['message'].get('content') or "").strip()
            # Same models that leak chain-of-thought into content for the main
            # synthesis calls (see _invoke_llm_with_reasoning) do it here too —
            # strip the thinking preamble so the primer text is clean.
            if "</think>" in content:
                content = content.split("</think>", 1)[1].strip()
        except Exception as e:
            print(f"[WebBackground] attempt {attempt + 1}/{max_iterations} failed: {e}")
            content = ""

        if content:
            from miner.prompt_logger import prompt_logger
            try:
                prompt_logger.log("RAG_WebBackground", target_model, [{"role": "user", "content": prompt}], content)
            except Exception:
                pass
            return None if content.upper() == "NONE" else content

    return None


async def generate_recommendation_answer(
    question: str,
    top_summaries: List[str],
    model_name_override: str = None,
    conversation_history: list | None = None,
) -> tuple[str, str]:
    """Returns (answer, reasoning)."""
    joined_summaries = "\n\n---\n\n".join(top_summaries)
    base_prompt = load_prompt("recommendation_synthesis_prompt.txt")
    prompt = base_prompt.format(question=question, joined_summaries=joined_summaries)
    if conversation_history:
        prompt = _format_conversation_history(conversation_history) + "\n\n---\n\n" + prompt
    return await _invoke_llm_with_reasoning(prompt, model_name_override)


async def generate_final_answer(
    question: str,
    top_summaries: List[str],
    model_name_override: str = None,
    conversation_history: list | None = None,
) -> tuple[str, str]:
    """Returns (answer, reasoning)."""
    # Do NOT deduplicate by paper — different chunks from the same paper often contain entirely different data (e.g. one chunk = image caption, another = table rows with yields).
    # Removing a chunk just because the paper already appeared would drop critical data.
    joined_summaries = "\n\n---\n\n".join(top_summaries)
    base_prompt = load_prompt("final_answer_prompt.txt")
    prompt = base_prompt.format(question=question, joined_summaries=joined_summaries)
    if conversation_history:
        prompt = _format_conversation_history(conversation_history) + "\n\n---\n\n" + prompt
    return await _invoke_llm_with_reasoning(prompt, model_name_override)

def _parse_json_from_response(response: str) -> list:
    """
    A robust function to find and parse a JSON array from a models raw text response.
    It handles markdown code fences and other surrounding text.
    """
    try:
        json_start = response.find('[')
        json_end = response.rfind(']')

        if json_start != -1 and json_end != -1:
            json_str = response[json_start:json_end + 1]
            return json.loads(json_str)
        return []
    except (json.JSONDecodeError, IndexError):
        print(f"⚠️ Robust JSON parsing failed for response: {response}")
        return []

async def extract_structured_data(question: str, context: str) -> list:
    """
    Uses an LLM to extract structured data from a text chunk based on a question.
    Relies on a model that supports function calling or reliable JSON output.
    """
    prompt_template = load_prompt("data_extraction_prompt.txt")
    prompt = ChatPromptTemplate.from_template(prompt_template)
    extraction_model_name = config["model"].get("extraction_model", model_name)
    # print(prompt)
    print(f"[INFO] Using extraction model: {extraction_model_name}")

    extraction_model = ChatOpenAI(
        model=extraction_model_name,
        api_key=os.environ.get("OPENROUTER_API_KEY"),
        base_url=openrouter_url.replace("/chat/completions", ""), \
        temperature=0
    )

    chain = prompt | extraction_model

    try:
        # The result from the model is a content string
        input_payload = {"question": question, "context": context}
        # print(f"Sending prompt to LLM for extraction:\n---\n{prompt.format(**input_payload)}\n---") 
        result_content = await chain.ainvoke(input_payload)
        # Use our robust parser to extract the JSON
        print(f"LLM Raw Response for Extraction:\n---\n{result_content.content}\n---")
        parsed_json = _parse_json_from_response(result_content.content)
        return parsed_json
    except (json.JSONDecodeError, TypeError, ValueError) as e:
        print(f"Failed to parse JSON from model output: {e}")
        return [] # Return empty list on failure