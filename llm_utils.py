import httpx
import asyncio
from langchain_ollama import OllamaLLM

async def _post_with_retry(url, headers, payload, max_retries=5, base_delay=2):
    """Helper: POST request with retry & exponential backoff."""
    delay = base_delay
    for attempt in range(max_retries):
        async with httpx.AsyncClient(timeout=60) as client:
            try:
                response = await client.post(url, headers=headers, json=payload)
                if response.status_code == 429:
                    print(f"⚠️ Rate limit hit (attempt {attempt+1}/{max_retries}). Retrying in {delay} seconds...")
                    await asyncio.sleep(delay)
                    delay *= 2
                    continue
                if 500 <= response.status_code < 600:
                    print(f"⚠️ Server error {response.status_code} (attempt {attempt+1}/{max_retries}). Retrying in {delay} seconds...")
                    await asyncio.sleep(delay)
                    delay *= 2
                    continue
                response.raise_for_status()
                if not response.content:
                    print(f"⚠️ Empty response body (attempt {attempt+1}/{max_retries}, status {response.status_code}). Retrying in {delay}s...")
                    await asyncio.sleep(delay)
                    delay *= 2
                    continue
                try:
                    data = response.json()
                except Exception as e:
                    print(f"⚠️ JSON decode failed (attempt {attempt+1}/{max_retries}, status {response.status_code}). Body: {response.text[:300]!r}. Retrying in {delay}s...")
                    await asyncio.sleep(delay)
                    delay *= 2
                    continue

                if isinstance(data, dict) and "error" in data and "choices" not in data:
                    err_msg = data["error"].get("message", str(data["error"])) if isinstance(data["error"], dict) else str(data["error"])
                    err_code = data["error"].get("code", 0) if isinstance(data["error"], dict) else 0
                    print(f"⚠️ API error in response body (attempt {attempt+1}/{max_retries}): [{err_code}] {err_msg[:200]}")
                    # 5xx-equivalent or rate-limit codes → retry; 4xx (bad request, auth) → fail fast
                    if err_code in (429, 529) or str(err_code).startswith("5"):
                        await asyncio.sleep(delay)
                        delay *= 2
                        continue
                    raise RuntimeError(f"API error [{err_code}]: {err_msg}")
                return data
            except httpx.ReadTimeout:
                print(f"⚠️ Read timeout (attempt {attempt+1}/{max_retries}). Retrying in {delay} seconds...")
                await asyncio.sleep(delay)
                delay *= 2
                continue

    raise RuntimeError("Max retries exceeded for API request.")


async def _ollama_with_retry(prompt, model, max_retries=3, delay=3):
    """Retry wrapper for local Ollama calls to avoid runner crashes. ! Local llm is not used and should be deprecated in future."""
    for attempt in range(max_retries):
        try:
            llm = OllamaLLM(model=model)
            return await llm.ainvoke(prompt)
        except Exception as e:
            print(f"⚠️ Ollama error (attempt {attempt+1}/{max_retries}): {e}")
            await asyncio.sleep(delay)
    raise RuntimeError("Ollama failed after multiple retries.")
