import os
import json
import datetime
import logging

_log = logging.getLogger(__name__)

def save_crash_dump(agent_name: str, model_name: str, payload: dict, response_content: str, error: Exception):
    """
    Saves a detailed crash dump to logs/crash_dumps/ for debugging malformed LLM responses.
    """
    # Create logs directory if it doesn't exist
    dump_dir = os.path.join(os.getcwd(), "logs", "crash_dumps")
    os.makedirs(dump_dir, exist_ok=True)
    
    timestamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    safe_agent_name = agent_name.replace(" ", "_").lower()
    filename = f"crash_{safe_agent_name}_{timestamp}.json"
    filepath = os.path.join(dump_dir, filename)
    
    dump_data = {
        "timestamp": timestamp,
        "agent": agent_name,
        "model": model_name,
        "error_type": type(error).__name__,
        "error_message": str(error),
        "payload": payload,
        "raw_response": response_content
    }
    
    try:
        with open(filepath, "w", encoding="utf-8") as f:
            json.dump(dump_data, f, indent=4, ensure_ascii=False)
        _log.info(f"💾 Crash dump saved to: {filepath}")
        print(f"DEBUG: Crash dump saved to {filepath}")
    except Exception as e:
        _log.error(f"❌ Failed to save crash dump: {e}")

def log_llm_error(agent_name: str, model_name: str, error_msg: str, response_text: str = None):
    # !! Deprecated. Remove in the next version. !!
    """Standardized logging for LLM errors."""
    full_msg = f"[{agent_name}] Model '{model_name}' Error: {error_msg}"
    if response_text is not None:
        # Log only first 100 chars to avoid cluttering main logs
        snippet = (response_text[:100] + '...') if len(response_text) > 100 else response_text
        full_msg += f" | Response Snippet: {snippet}"
    
    _log.error(full_msg)
    print(full_msg)
