import re
import base64
import logging
from pathlib import Path
from typing import Dict, Any, Optional, List

from miner.agents.base_agent import BaseAgent

_log = logging.getLogger(__name__)


def _extract_image_paths(markdown_content: str, base_dir: Optional[str] = None) -> List[str]:
    """
    Extract image paths from markdown ![Image](...) links and resolve them to
    absolute paths. Returns only paths that actually exist on disk.
    """
    raw = re.findall(r'!\[.*?\]\(([^)]+)\)', markdown_content)
    resolved = []
    for p in raw:
        path = Path(p)
        if path.exists():
            resolved.append(str(path.resolve()))
        elif base_dir:
            candidate = Path(base_dir) / p
            if candidate.exists():
                resolved.append(str(candidate.resolve()))
    return resolved


def _encode_image(path: str) -> Optional[str]:
    try:
        with open(path, "rb") as f:
            return base64.b64encode(f.read()).decode("utf-8")
    except Exception:
        return None


_SKILLS_DIR = Path(__file__).parent.parent / "skills"
_IMAGE_CORRECTION_PROMPT = (_SKILLS_DIR / "image_ref_correction.md").read_text().strip()


class AdvancedStructureAgent(BaseAgent):
    """
    Parses detailed document structure including Tables, Schemes, Figures, and
    Product Registries with column-level mappings.

    The agent is multimodal: it receives both the markdown text and the actual
    figure images extracted from the paper. This lets it visually verify which
    figure contains which compound structures, rather than relying solely on the
    text context around a bare image link.
    """

    # Maximum number of figure images to include — keeps prompt size manageable
    MAX_IMAGES = 6

    def __init__(self):
        from context import load_config
        config = load_config()
        model = config.get("model", {}).get("agents", {}).get(
            "advanced_structure_model", "google/gemini-3-flash-preview")
        super().__init__(model=model)

    def _correct_table_image_refs(
        self, data: Dict[str, Any], markdown_content: str
    ) -> Dict[str, Any]:
        """
        Separate focused LLM call that sends the full article markdown and asks the
        model to assign the correct reaction_scheme_ref for every optimization or
        scope table. Runs after the main structure parse so it can fix any wrong
        image paths the primary call produced.

        A fast deterministic pre-pass first handles the common case where a scheme
        image appears in the markdown BETWEEN the table heading line and the first
        pipe row of the table — that image unambiguously belongs to the table.
        """
        # ── Deterministic pre-pass ────────
        lines = markdown_content.splitlines()

        # Build a lookup: table_id (lowercased, stripped) -> item dict
        id_to_item = {
            item.get("id", "").strip().lower(): item
            for item in data.get("data_items", [])
            if item.get("type") == "Table"
        }

        for line_idx, line in enumerate(lines):
            line_stripped = line.strip()
            # Match a table heading line, e.g. "Table 1", "## Table 1.", "Table 1."
            heading_m = re.match(r'^#+?\s*(table\s+\d+[a-zA-Z]?)', line_stripped, re.IGNORECASE)
            if not heading_m:
                # Also catch plain "Table 1" without heading marker
                heading_m = re.match(r'^(table\s+\d+[a-zA-Z]?)[\.\s]', line_stripped, re.IGNORECASE)
            if not heading_m:
                continue
            table_id = heading_m.group(1).strip().lower()
            item = id_to_item.get(table_id)
            if not item:
                continue

            # Scan forward for an image link before the first pipe row
            found_image = None
            for fwd in lines[line_idx + 1:]:
                if fwd.strip().startswith("|"):
                    break  # reached the table pipe rows — stop
                m_img = re.search(r'!\[.*?\]\(([^)]+)\)', fwd)
                if m_img:
                    found_image = m_img.group(1)
                    break

            if found_image:
                current_ref = item.get("reaction_scheme_ref", "")
                # Only override if the found image differs from current assignment
                if not current_ref or found_image not in current_ref:
                    print(f"  [ImageCorrection-Det] '{item.get('id')}': ...{found_image[-65:]}")
                    item["reaction_scheme_ref"] = found_image

        target_items = [
            {"id": item.get("id"), "current_ref": item.get("reaction_scheme_ref")}
            for item in data.get("data_items", [])
            if item.get("type") in ("Table",)
            and (item.get("is_optimization_table") or item.get("is_scope_table")
                 or item.get("contains_extractable_reaction_data"))
        ]
        if not target_items:
            return data

        # Collect all image paths present in the markdown (in document order)
        available_images = re.findall(r'!\[.*?\]\((.+)\)', markdown_content)
        if not available_images:
            return data

        import json
        # Add line numbers so the LLM can identify the closest image to each heading
        numbered_md = "\n".join(f"{i+1:4}: {line}" for i, line in enumerate(markdown_content.splitlines()))

        user_content = (
            f"{_IMAGE_CORRECTION_PROMPT}\n\n"
            f"AVAILABLE_IMAGES (in document order):\n"
            + "\n".join(f"  - {p}" for p in available_images)
            + f"\n\nITEMS TO CORRECT:\n{json.dumps(target_items, indent=2)}"
            f"\n\nFULL ARTICLE MARKDOWN (with line numbers):\n{numbered_md}"
        )

        messages = [
            {"role": "system", "content": "You are a helpful assistant that outputs JSON."},
            {"role": "user",   "content": user_content},
        ]

        try:
            response = self._call_llm(messages, response_format={"type": "json_object"})
            corrections: Dict[str, str] = self._parse_json(response) or {}

            for item in data.get("data_items", []):
                item_id = item.get("id", "")
                corrected = corrections.get(item_id)
                if corrected and corrected != item.get("reaction_scheme_ref"):
                    _log.info(
                        f"[ImageCorrection] '{item_id}': "
                        f"{item.get('reaction_scheme_ref')} → {corrected}"
                    )
                    print(
                        f"  [ImageCorrection] '{item_id}': "
                        f"...{str(corrected)[-65:]}"
                    )
                    item["reaction_scheme_ref"] = corrected

        except Exception as e:
            _log.warning(f"[ImageCorrection] LLM call failed: {e}")

        return data

    def _correct_gallery_image_refs(
        self, data: Dict[str, Any], markdown_content: str
    ) -> Dict[str, Any]:
        """
        Deterministically fix reaction_scheme_ref for Gallery items whose parent table
        is known. The gallery image always appears AFTER the last pipe-row of its table
        in the markdown — this is a layout invariant we can exploit without an LLM call.
        """
        lines = markdown_content.splitlines()

        # Build ordered image list: [(line_index, raw_path), ...]
        images_in_order = []
        for i, line in enumerate(lines):
            m = re.search(r'!\[.*?\]\(([^)]+)\)', line)
            if m:
                images_in_order.append((i, m.group(1)))

        if not images_in_order:
            return data

        items = data.get("data_items", [])

        for item in items:
            if item.get("type") != "Gallery":
                continue
            if not item.get("defined_labels"):
                continue

            # Derive parent table id: "Table 1 Gallery" → "Table 1"
            item_id = item.get("id", "")
            parent_id = re.sub(r'\s*Gallery\s*$', '', item_id, flags=re.IGNORECASE).strip()

            parent_ref = None
            for other in items:
                if other.get("id", "").strip() == parent_id and other.get("type") == "Table":
                    parent_ref = other.get("reaction_scheme_ref", "")
                    break

            if not parent_ref:
                continue

            # Find line number of the parent table's image in markdown
            parent_img_line = None
            for line_idx, img_path in images_in_order:
                if parent_ref.endswith(img_path) or img_path.endswith(parent_ref) or img_path in parent_ref or parent_ref in img_path:
                    parent_img_line = line_idx
                    break

            if parent_img_line is None:
                continue

            # Find the last pipe-row of the table after the parent image
            last_pipe_line = parent_img_line
            for i in range(parent_img_line, len(lines)):
                if lines[i].strip().startswith("|"):
                    last_pipe_line = i

            # First image after the last pipe-row that isn't the parent scheme image
            for line_idx, img_path in images_in_order:
                if line_idx > last_pipe_line and img_path not in parent_ref and parent_ref not in img_path:
                    old_ref = item.get("reaction_scheme_ref", "")
                    if old_ref != img_path:
                        print(f"  [GalleryImageFix] '{item_id}': ...{img_path[-70:]}")
                        item["reaction_scheme_ref"] = img_path
                    break

        return data

    def _supplement_compound_name_map_from_text(
        self, data: Dict[str, Any], markdown_content: str
    ) -> Dict[str, Any]:
        """
        Scan the markdown text for inline name definitions like "compound name (label)"
        and add any missing entries to compound_name_map.
        Covers patterns the LLM typically misses: reagents defined inline in the text body.
        """
        cnm = data.get("compound_name_map")
        if cnm is None:
            cnm = {}
            data["compound_name_map"] = cnm

        # Pattern: "full name (short_label)" where label is alphanumeric/hyphenated
        # e.g. "benzoyl chloride (2b)", "CAT-1 (20 mol%)" -> skip if mol% present
        pattern = re.compile(
            r'([A-Za-z][A-Za-z0-9\s,\'\-\(\)]{2,60}?)\s+\(([A-Za-z0-9][A-Za-z0-9\-]{0,10})\)',
        )
        # Also handle: "label (full name)" with a known-label-first variant — skip for now

        skip_phrases = {"mol%", "mmol", "equiv", "eq.", "eq ", "yield", "entry", "%", "v/v", "M ", "h,", " h"}

        for m in pattern.finditer(markdown_content):
            full_name = m.group(1).strip()
            label = m.group(2).strip()

            # Skip if the captured context looks like a quantity/condition, not a name
            if any(sp in m.group(0) for sp in skip_phrases):
                continue
            # Label should look like a compound id: 1a, 2b, PC-1, etc.
            if not re.match(r'^[A-Za-z0-9][A-Za-z0-9\-]{0,10}$', label):
                continue
            # Full name should contain at least one letter word ≥ 4 chars
            if not re.search(r'[A-Za-z]{4,}', full_name):
                continue
            # Don't overwrite entries already populated by the LLM
            if label not in cnm:
                cnm[label] = full_name
                print(f"  [CompoundNameMap] Added from text: '{label}' → '{full_name}'")

        return data

    def run(self, markdown_content: str, base_dir: Optional[str] = None) -> Dict[str, Any]:
        """
        Parse the markdown content and return a structured dictionary.

        Args:
            markdown_content: Full markdown text of the article.
            base_dir: Directory to resolve relative image paths from the markdown.
        """
        skill_text = (_SKILLS_DIR / "advanced_structure_analysis.md").read_text()
        system_prompt = "You are a helpful assistant that outputs JSON."

        user_content: list = [
            {
                "type": "text",
                "text": (
                    f"{skill_text}\n\n"
                    "The following figure images are provided so you can visually verify "
                    "which figures contain drawn molecular structures and their labels.\n\n"
                    "ARTICLE MARKDOWN:\n"
                    f"{markdown_content}"
                )
            }
        ]

        image_paths = _extract_image_paths(markdown_content, base_dir)
        included = 0
        for img_path in image_paths:
            if included >= self.MAX_IMAGES:
                break
            b64 = _encode_image(img_path)
            if b64:
                user_content.append({
                    "type": "image_url",
                    "image_url": {"url": f"data:image/png;base64,{b64}"}
                })
                included += 1

        if included:
            _log.info(f"AdvancedStructureAgent: sending {included} figure images alongside markdown")

        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user",   "content": user_content},
        ]

        try:
            _log.info(f"AdvancedStructureAgent: calling {self.model}...")
            response = self._call_llm(messages, response_format={"type": "json_object"})
            data = self._parse_json(response)

            if isinstance(data, list) and data:
                data = data[0]

            if not data or not isinstance(data, dict):
                return {"data_items": [], "article_summary": "Failed to parse structure."}

            data = self._correct_table_image_refs(data, markdown_content)
            data = self._correct_gallery_image_refs(data, markdown_content)
            data = self._supplement_compound_name_map_from_text(data, markdown_content)
            return data

        except Exception as e:
            _log.error(f"AdvancedStructureAgent error: {e}")
            return {"data_items": [], "article_summary": f"Error: {e}"}
