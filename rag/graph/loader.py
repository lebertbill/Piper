"""
Walks extracted_data/ trees and yields all (paper, table, entries) tuples.

Run selection priority (per paper):
  1. per_paper_runs[paper_title]  — explicit per-paper override
  2. preferred_run                — preferred run name (global, falls back if not present)
  3. First run found alphabetically
"""
import json
from pathlib import Path


def get_available_runs(data_root: str | Path) -> dict[str, list[str]]:
    """
    Returns {paper_title: [run_name, ...]} for every paper that has at least
    one extraction_results.json.  Run names are sorted alphabetically.
    """
    data_root = Path(data_root)
    result: dict[str, list[str]] = {}

    for result_path in sorted(data_root.rglob("extraction_results.json")):
        # structure: data_root / paper_title / run_name / processed / extraction_results.json
        if len(result_path.parts) < 4:
            continue
        paper_title = result_path.parents[2].name
        run_name = result_path.parents[1].name
        result.setdefault(paper_title, [])
        if run_name not in result[paper_title]:
            result[paper_title].append(run_name)

    # Sort run lists alphabetically
    for paper in result:
        result[paper].sort()

    return result


def _pick_run(
    available_runs: list[str],
    preferred_run: str | None,
) -> str:
    """Pick the best run name from available_runs given a global preference."""
    if not available_runs:
        return ""
    if preferred_run and preferred_run in available_runs:
        return preferred_run
    # Prefer any run that isn't 'default_run' if a preference was stated
    if preferred_run:
        non_default = [r for r in available_runs if r != "default_run"]
        if non_default:
            return non_default[0]
    return available_runs[0]


def load_all(
    data_root: str | Path,
    preferred_run: str | None = None,
    per_paper_runs: dict[str, str] | None = None,
) -> list[dict]:
    """
    Returns a flat list of table records:
      {paper, run, table_name, table_caption, entries, source_path}

    Args:
        data_root:      root folder containing paper subfolders
        preferred_run:  global preferred run name (e.g. "gpt_5_mini_1").
                        Used for every paper unless overridden.
        per_paper_runs: {paper_title: run_name} overrides per paper.
    """
    data_root = Path(data_root)
    per_paper_runs = per_paper_runs or {}

    available = get_available_runs(data_root)
    records = []

    for paper_title, runs in sorted(available.items()):
        # Determine which run to use for this paper
        if paper_title in per_paper_runs:
            chosen_run = per_paper_runs[paper_title]
        else:
            chosen_run = _pick_run(runs, preferred_run)

        if not chosen_run:
            continue

        result_path = (
            data_root / paper_title / chosen_run / "processed" / "extraction_results.json"
        )
        if not result_path.exists():
            # Fallback: first available run
            for run in runs:
                candidate = data_root / paper_title / run / "processed" / "extraction_results.json"
                if candidate.exists():
                    result_path = candidate
                    chosen_run = run
                    break
            else:
                continue

        try:
            raw = json.loads(result_path.read_text(encoding="utf-8"))
        except Exception:
            continue

        table_records = raw if isinstance(raw, list) else [raw]

        for rec in table_records:
            entries = rec.get("entries") or []
            if not entries:
                continue
            records.append(
                {
                    "paper": paper_title,
                    "run": chosen_run,
                    "table_name": rec.get("table_name", ""),
                    "table_caption": rec.get("table_caption", ""),
                    "entries": entries,
                    "source_path": str(result_path),
                }
            )

    return records
