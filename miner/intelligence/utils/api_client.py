import time
import logging
import openai
from typing import Any, Callable

_log = logging.getLogger(__name__)

def call_llm_with_retry(client: Any, progress_callback: Callable[[str], None] = None, **kwargs) -> Any:
    """
    Wraps the OpenAI client call with infinite retry logic for rate limits and credit exhaustion.
    
    Args:
        client: The OpenAI client instance.
        progress_callback: A function to report status updates to the UI (e.g., Streamlit).
        **kwargs: Arguments to pass to client.chat.completions.create.
        
    Returns:
        The LLM response object.
    """
    attempt = 0
    base_delay = 60 # Start with 1 minute wait for quota/credit issues
    
    while True:
        try:
            attempt += 1
            response = client.chat.completions.create(**kwargs)
            return response
            
        except openai.RateLimitError as e:
            err_msg = str(e).lower()
            # Distinguish between pure rate limit (too many requests) and credit limit (insufficient quota)
            is_quota_issue = "insufficient_quota" in err_msg or "credit" in err_msg or "balance" in err_msg
            
            status_prefix = "⚠️ API Credit Limit Exceeded" if is_quota_issue else "⚠️ API Rate Limit Hit"
            wait_msg = f"{status_prefix}. Waiting {base_delay}s before retry (Attempt {attempt})..."
            
            _log.warning(wait_msg)
            if progress_callback:
                progress_callback(wait_msg)
            
            time.sleep(base_delay)
            # We don't necessarily want exponential backoff for credit issues, 
            # as the user might take a while to recharge. But we can slightly increase it.
            if not is_quota_issue:
                base_delay = min(base_delay * 2, 300) # Max 5 mins for rate limits
            else:
                base_delay = 60 # Keep it at 1 min for credit checks
                
        except Exception as e:
            err_msg = str(e).lower()
            # OpenRouter or other providers might return generic 429/402 as standard Exceptions
            if "429" in err_msg or "rate limit" in err_msg or "insufficient_quota" in err_msg or "402" in err_msg:
                is_quota_issue = "insufficient_quota" in err_msg or "402" in err_msg or "credit" in err_msg
                
                status_prefix = "⚠️ API Credit Limit Exceeded" if is_quota_issue else "⚠️ API Rate Limit Hit"
                wait_msg = f"{status_prefix}. Waiting {base_delay}s before retry (Attempt {attempt})..."
                
                _log.warning(wait_msg)
                if progress_callback:
                    progress_callback(wait_msg)
                
                time.sleep(base_delay)
                continue
            
            # For other unexpected errors, re-raise
            _log.error(f"❌ Unexpected LLM API Error: {e}")
            raise e
