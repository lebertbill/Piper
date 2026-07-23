import hashlib
import json
import os
from typing import Tuple

CONTEXT_FILE = "context.json"


def load_config() -> dict:
    """Load runtime configuration from context.json. This is highly important, most of the settings and configs are stored in context.json """
    with open(CONTEXT_FILE) as f:
        return json.load(f)


def get_parameters() -> Tuple[int, int, int, int]:
    """Return chunking and embedding parameters which are required for RAG config."""
    config = load_config()
    chunk_params = config.get("chunking", {})
    chunk_size = chunk_params.get("chunk_words", 800)
    chunk_overlap = chunk_params.get("chunk_overlap_words", 120)
    max_summary_words = chunk_params.get("max_summary_words", 250)
    return chunk_size, chunk_overlap, max_summary_words, 0


def get_openrouter_url() -> str:
    """Return the base OpenRouter API URL (without the /chat/completions suffix)."""
    config = load_config()
    url = config["model"].get("openrouter_url", "https://openrouter.ai/api/v1/chat/completions")
    if url.endswith("/chat/completions"):
        url = url.replace("/chat/completions", "")
    return url


def get_data_paths(base_folder_path: str) -> Tuple[str, str, str, str, str]:
    """
    !! Derive all data-store paths from the watched folder path.
    The data store directory name is deterministic from the folder path hash so
    different watched folders never collide. !!
    """
    config = load_config()
    chunk_by_doc = config.get("chunk_by_document", False)

    folder_hash = hashlib.sha256(base_folder_path.encode()).hexdigest()[:10]
    data_dir = f"data_store_{folder_hash}" + ("_fulldoc" if chunk_by_doc else "")
    os.makedirs(data_dir, exist_ok=True)

    return (
        os.path.join(data_dir, "chroma_db"),
        os.path.join(data_dir, "processed_files.json"),
        os.path.join(data_dir, "failed_files.json"),
        os.path.join(data_dir, "results.db"),
        os.path.join(data_dir, "extracted_results.xlsx"),
    )
