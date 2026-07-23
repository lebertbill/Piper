"""
visualheist_extractor.py — Piper interface module for TF-ID/visualheist table detection.

This module:
1. Ensures the isolated visualheist venv exists (creates/installs it if not).
2. Runs `miner/visualheist_worker.py` as a subprocess inside that venv.
3. Parses the JSON output and returns the best matching table image path.

Integration point: called from document_processor.py as a fallback when
TableTextExtractorAgent fails to return usable table text.
"""

import json
import logging
import subprocess
import sys
from pathlib import Path
from typing import Optional

_log = logging.getLogger(__name__)

# ── Paths ──────────────────────────────────────────────────────────────────────
_PROJECT_ROOT = Path(__file__).parent.parent
_VENV_DIR = _PROJECT_ROOT / "venv_visualheist"
_VENV_PYTHON = _VENV_DIR / "bin" / "python"
_WORKER_SCRIPT = Path(__file__).parent / "visualheist_worker.py"
_REQUIREMENTS = Path(__file__).parent / "visualheist_requirements.txt"

_DEFAULT_MODEL_ID = "shixuanleong/visualheist-base"


# ── Venv management ────────────────────────────────────────────────────────────

def _venv_is_ready() -> bool:
    """Check whether the isolated venv exists and has the required packages."""
    if not _VENV_PYTHON.exists():
        return False
    # Quick sanity-check: can we import transformers at the right version?
    try:
        result = subprocess.run(
            [str(_VENV_PYTHON), "-c",
             "import transformers; assert transformers.__version__ < '4.50', transformers.__version__"],
            capture_output=True, timeout=10
        )
        return result.returncode == 0
    except Exception:
        return False


def ensure_venv() -> bool:
    """
    Create the isolated visualheist venv and install pinned requirements if needed.
    Returns True if ready, False if setup failed.
    """
    if _venv_is_ready():
        return True

    _log.info("visualheist: Setting up isolated venv at %s ...", _VENV_DIR)
    print(f"  -> [visualheist] Creating isolated venv: {_VENV_DIR} ...")

    try:
        # Create venv using the current interpreter (Python 3.x)
        result = subprocess.run(
            [sys.executable, "-m", "venv", str(_VENV_DIR)],
            capture_output=True, timeout=60
        )
        if result.returncode != 0:
            _log.error("visualheist: venv creation failed: %s", result.stderr.decode())
            return False

        # Install pinned requirements
        pip = str(_VENV_DIR / "bin" / "pip")
        result = subprocess.run(
            [pip, "install", "-r", str(_REQUIREMENTS), "-q"],
            capture_output=True, timeout=600  # Model weights are large
        )
        if result.returncode != 0:
            _log.error("visualheist: pip install failed: %s", result.stderr.decode())
            return False

        _log.info("visualheist: venv ready.")
        print("  -> [visualheist] Venv ready.")
        return True

    except Exception as e:
        _log.error("visualheist: venv setup error: %s", e)
        return False


# ── Detection heuristic ────────────────────────────────────────────────────────

def is_table_text_useful(extracted_text: str) -> bool:
    """
    Returns True if the table text extracted by TableTextExtractorAgent
    contains enough structured data to be useful.

    Falls through to the visualheist fallback (or vision extraction) when False.
    """
    if not extracted_text or len(extracted_text.strip()) < 30:
        return False
        
    lines = [l.strip() for l in extracted_text.splitlines() if len(l.strip()) > 5]
    table_rows = [l for l in lines if "|" in l]
    
    # Needs at least 3 pipe-separated rows to be a valid markdown table
    if len(table_rows) < 3:
        return False
        
    # Check for obvious docling corruption (duplicated words in cells)
    # e.g. "PC1 PC1", "25 25", "61 61"
    import re
    corruption_score = 0
    for row in table_rows[:4]: # check first few rows
        # find duplicated tokens >1 char long
        dupes = re.findall(r'\b([a-zA-Z0-9]{2,})\s+\1\b', row)
        corruption_score += len(dupes)
        
    if corruption_score >= 2:
        return False # Highly likely to be a mangled table
        
    # A proper markdown table has pipe separators
    has_pipe_rows = "|" in extracted_text and extracted_text.count("|") >= 6
    return has_pipe_rows


# ── Main interface ─────────────────────────────────────────────────────────────

def extract_table_image(
    pdf_path: str,
    heading: str,
    processed_output_path: Path,
    model_id: str = _DEFAULT_MODEL_ID,
) -> Optional[Path]:
    """
    Run visualheist on the PDF and return the path of the best matching table crop.

    Strategy:
    - Run detection on the whole PDF (or nearby pages if page hint available).
    - Select the detection with label == 'table'. If multiple tables found,
      pick the one with the largest area (heuristic: largest = main optimization table).
    - Return the saved crop path, or None on failure.

    :param pdf_path: Absolute path to the source PDF.
    :param heading: Table heading string (used for filename and logging only).
    :param processed_output_path: Directory to save the cropped image.
    :param model_id: HuggingFace model ID for the TF-ID variant to use.
    :return: Path to the cropped image, or None if detection failed.
    """
    if not ensure_venv():
        _log.warning("visualheist: Venv not ready — skipping fallback.")
        return None

    _log.info("visualheist: Running detection on %s", pdf_path)
    print(f"  -> [visualheist] Running table detection on PDF...")

    try:
        result = subprocess.run(
            [
                str(_VENV_PYTHON),
                str(_WORKER_SCRIPT),
                "--pdf-path", pdf_path,
                "--output-dir", str(processed_output_path),
                "--model-id", model_id,
            ],
            capture_output=True,
            timeout=600,  # 10 min max for large PDFs with many pages
        )
    except subprocess.TimeoutExpired:
        _log.error("visualheist: Worker subprocess timed out.")
        return None
    except Exception as e:
        _log.error("visualheist: Subprocess error: %s", e)
        return None

    if result.returncode != 0:
        stderr_msg = result.stderr.decode("utf-8", errors="replace")[:500]
        _log.error("visualheist: Worker failed (exit %d): %s", result.returncode, stderr_msg)
        return None

    # Parse JSON output from worker
    try:
        stdout = result.stdout.decode("utf-8", errors="replace").strip()
        detections = json.loads(stdout)
    except json.JSONDecodeError as e:
        _log.error("visualheist: Failed to parse worker output: %s\nRaw: %s", e, stdout[:300])
        return None

    if not detections:
        _log.warning("visualheist: No tables or figures detected in PDF.")
        return None

    # Filter to only table detections
    tables = [d for d in detections if d["label"] == "table"]
    if not tables:
        _log.warning("visualheist: No 'table' detections found (found: %s)",
                     [d["label"] for d in detections])
        return None

    # Sort all detected tables by document order (page, then top-to-bottom y1).
    # Then pick the N-th table where N comes from the heading (e.g. "Table 2" → index 1).
    # This is more reliable than picking the largest, which always returns the biggest
    # table in the document (often the substrate scope, not the optimization table).
    import re as _re
    tables_sorted = sorted(tables, key=lambda d: (d["page"], d["bbox"][1]))
    heading_match = _re.search(r'\b(\d+)\b', heading)
    target_idx = (int(heading_match.group(1)) - 1) if heading_match else 0
    if target_idx >= len(tables_sorted):
        _log.warning("visualheist: Heading '%s' implies index %d but only %d tables found — using largest",
                     heading, target_idx, len(tables_sorted))
        best = max(tables_sorted, key=lambda d: (d["bbox"][2]-d["bbox"][0])*(d["bbox"][3]-d["bbox"][1]))
    else:
        best = tables_sorted[target_idx]
    saved_path = Path(best["saved_path"])

    if not saved_path.exists():
        _log.error("visualheist: Saved crop not found: %s", saved_path)
        return None

    _log.info("visualheist: Returning table crop: %s (page %d)", saved_path.name, best["page"])
    print(f"  -> [visualheist] ✅ Table crop: {saved_path.name} (page {best['page']})")
    return saved_path
