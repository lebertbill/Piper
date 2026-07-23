import json
import re
import logging
from json_repair import repair_json as json_repair_engine

logger = logging.getLogger(__name__)

def clean_json_string(s: str) -> str:
    """Removes markdown code blocks and extraneous whitespace."""
    if not s:
        return ""
    # Remove markdown code blocks (```json ... ```)
    if "```" in s:
        # Try to find content between ```json and ``` or just ``` and ```
        match = re.search(r'```(?:json)?\s*(.*?)\s*```', s, re.DOTALL)
        if match:
            s = match.group(1)
        else:
            # Fallback if only one side of backticks is present
            s = s.replace("```json", "").replace("```", "")
    
    return s.strip()

def repair_json(s: str) -> dict:
    """
    Attempts to fix common LLM JSON syntax errors using 'json-repair' library.
    Returns a dictionary or raises json.JSONDecodeError if unrepairable.
    """
    if not isinstance(s, str):
        return s
        
    s = clean_json_string(s)
    
    if not s:
        raise json.JSONDecodeError("Model returned an empty string or null instead of JSON.", s, 0)
    
    # Use json-repair engine to handle missing brackets, trailing commas, missing quotes, etc.
    try:
        # return_objects=True ensures we get a Python dict/list directly
        result = json_repair_engine(s, return_objects=True)
        
        # json-repair might return an empty string or None if it fails completely
        if result is None or (isinstance(result, str) and not result and s):
            # If it failed, let's try to raise a standard JSONDecodeError for the caller to handle
            return json.loads(s)
            
        return result
    except Exception as e:
        # Fallback to standard json.loads to get a proper JSONDecodeError with line/col info
        # This is useful for the caller to log exactly where the error is.
        return json.loads(s, strict=False)
