"""
Ingestion pipeline for the extracted_data/ folder.

Single source of truth: the processed markdown file per run.

Each chunk is one of:
  - Text-only:   section prose with no image → Gemini text embedding
  - Multimodal:  section prose that contains an ![Image](...) reference
                 → same text (image tag stripped) + resolved image path
                 → Gemini multimodal embedding (text + image together)

Images are taken directly from the markdown's inline ![Image](path) references
so text and image are always in sync — no separate extraction_results.json lookup.
"""
from __future__ import annotations

import hashlib
import re
from pathlib import Path

from .pdf2chunks import Chunk

# Greedy .+ so paths containing ')' (e.g. Rh(I) in title) are captured fully.
# Used only on single lines via fullmatch — greediness is safe line-by-line.
_IMAGE_LINE_RE = re.compile(r"!\[.*?\]\((.+)\)")
_HEADING_RE = re.compile(r"^(#{1,4})\s+(.+)$", re.MULTILINE)
# Matches the start of a figure/scheme/table label (e.g. "Fig. 1", "Scheme 2", "Table 3")
_CAPTION_ID_RE = re.compile(r"^(Fig\.?\s*\d+[\w.]*|Figure\s*\d+[\w.]*|Scheme\s*\d+[\w.]*|Table\s*\d+[\w.]*)", re.IGNORECASE)


def _content_hash(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()[:16]


def _resolve_image(rel_path: str, md_dir: Path) -> str | None:
    """Resolve markdown-relative image path to absolute. Returns None if not found."""
    candidate = md_dir / rel_path
    if candidate.exists():
        return str(candidate)
    return None


def _split_sections(md_text: str) -> list[dict]:
    """
    Split markdown into heading-based sections.
    Returns list of {heading: str, text: str} — text includes raw image tags.
    """
    positions = [(m.start(), m.group(2).strip()) for m in _HEADING_RE.finditer(md_text)]

    if not positions:
        return [{"heading": "Document", "text": md_text.strip()}]

    sections = []
    if positions[0][0] > 0:
        pre = md_text[: positions[0][0]].strip()
        if pre:
            sections.append({"heading": "Abstract / Preamble", "text": pre})

    for i, (pos, heading) in enumerate(positions):
        end = positions[i + 1][0] if i + 1 < len(positions) else len(md_text)
        body = md_text[pos:end]
        body = re.sub(r"^#{1,4}\s+.+\n?", "", body, count=1).strip()
        if body:
            sections.append({"heading": heading, "text": body})

    return sections


def _chunks_from_section(
    section_text: str,
    heading: str,
    md_dir: Path,
    paper: str,
    run: str,
    start_idx: int,
    chunk_words: int,
    chunk_overlap: int,
) -> list[Chunk]:
    """
    Converts one markdown section into Chunk objects, processing line by line.

    Line-by-line processing is immune to the ')' inside path issue (e.g.
    paper titles containing 'Rh(I)') that breaks regex-split approaches.

    - Lines that are purely an image reference → flush accumulated prose as a
      multimodal chunk with that image attached.
    - All other lines → accumulate as prose, split into word chunks at the end
      or when an image is encountered.
    """
    chunks: list[Chunk] = []
    idx = start_idx
    base_meta = {"paper": paper, "run": run, "title": paper, "section": heading}

    def make_text_chunks(prose: str) -> None:
        nonlocal idx
        words = prose.split()
        if not words:
            return
        step = max(1, chunk_words - chunk_overlap)
        for start in range(0, len(words), step):
            chunk_text = " ".join(words[start: start + chunk_words]).strip()
            if not chunk_text:
                continue
            meta = {
                **base_meta,
                "chunk_index": idx,
                "content_hash": _content_hash(paper + heading + chunk_text[:50]),
                "item_type": "text",
                "image_path": None,
            }
            chunks.append(Chunk(text=chunk_text, name=f"{paper} §{heading}", metadata=meta))
            idx += 1

    def _extract_caption(lines: list[str]) -> tuple[str, str]:
        """
        Given the accumulated lines before an image tag, extract the figure caption.
        Returns (figure_id, full_caption) — e.g. ("Fig. 1", "Fig. 1. Continuous flow...").
        Handles both single-line captions and two-line "label / description" patterns.
        """
        # Work backward through non-empty lines
        non_empty = [l.strip() for l in lines if l.strip()]
        if not non_empty:
            return "", ""
        last = non_empty[-1]
        m = _CAPTION_ID_RE.match(last)
        if m:
            figure_id = m.group(1).strip().rstrip(".")
            return figure_id, last
        # Two-line pattern: label on second-to-last, description on last
        if len(non_empty) >= 2:
            prev = non_empty[-2]
            m2 = _CAPTION_ID_RE.match(prev)
            if m2:
                figure_id = m2.group(1).strip().rstrip(".")
                full_caption = f"{prev} {last}"
                return figure_id, full_caption
        return "", ""

    def make_image_chunk(prose: str, image_path: str | None, figure_id: str = "", figure_caption: str = "") -> None:
        nonlocal idx
        # Build chunk text: caption first, then any surrounding prose context
        if figure_caption:
            text = figure_caption
            # Append remaining prose (stripped of the caption itself) for context
            rest = prose.replace(figure_caption, "").strip()
            if rest:
                text = f"{figure_caption}\n{rest}"
        else:
            text = prose.strip() or heading
        if not text:
            return
        meta = {
            **base_meta,
            "chunk_index": idx,
            "content_hash": _content_hash(paper + heading + text[:50]),
            "item_type": "multimodal",
            "image_path": image_path,
            "figure_id": figure_id,
            "figure_caption": figure_caption,
        }
        label = figure_id or heading
        chunks.append(Chunk(text=text, name=f"{paper} §{label}", metadata=meta))
        idx += 1

    pending_lines: list[str] = []
    # Store pending image info when caption appears AFTER the image tag (common in PDFs)
    pending_image: tuple[str | None, str] | None = None  # (resolved_path, prose)

    all_lines = section_text.split("\n")
    for i, line in enumerate(all_lines):
        img_match = _IMAGE_LINE_RE.fullmatch(line.strip()) if line.strip() else None

        if img_match:
            # If there's already a deferred image waiting for a caption, flush it now
            if pending_image is not None:
                make_image_chunk(pending_image[1], pending_image[0])
                pending_image = None
            prose = " ".join(pending_lines).strip()
            resolved = _resolve_image(img_match.group(1), md_dir)
            figure_id, figure_caption = _extract_caption(pending_lines)
            if figure_caption:
                make_image_chunk(prose, resolved, figure_id=figure_id, figure_caption=figure_caption)
                pending_lines = []
            else:
                # Caption may appear on the next non-blank line(s) — defer this image
                pending_image = (resolved, prose)
                pending_lines = []
        else:
            if pending_image is not None and line.strip():
                # This line is the first non-blank content after an image → treat as caption.
                # Strip leading | characters (markdown table rows start with |)
                clean_line = re.sub(r"^\|+\s*", "", line.strip()).split("|")[0].strip()
                cap_id, cap_text = _extract_caption([clean_line])
                if not cap_text:
                    cap_text = clean_line or line.strip()
                    cap_id = ""
                make_image_chunk(pending_image[1], pending_image[0],
                                 figure_id=cap_id, figure_caption=cap_text)
                pending_image = None
                pending_lines.append(line)  # keep caption in prose for following text chunk
            else:
                pending_lines.append(line)

    # Flush any deferred image without a caption
    if pending_image is not None:
        make_image_chunk(pending_image[1], pending_image[0])
    # Flush any remaining prose as text-only chunks
    if pending_lines:
        make_text_chunks(" ".join(pending_lines).strip())

    return chunks


def chunk_markdown_file(
    md_path: Path,
    paper_title: str,
    run_name: str,
    extra_metadata: dict | None = None,
    chunk_words: int = 600,
    chunk_overlap: int = 120,
) -> list[Chunk]:
    """
    Chunks a processed markdown file into Chunk objects.

    Text sections → overlapping word chunks (text-only embedding).
    Sections with inline ![Image](...) --> one chunk per image with surrounding
    text as context (multimodal embedding — text + image together).

    Images are resolved relative to the markdown file's directory so paths
    always match regardless of where the project is run from.
    """
    md_text = md_path.read_text(encoding="utf-8")
    md_dir = md_path.parent
    sections = _split_sections(md_text)

    chunks: list[Chunk] = []
    idx = 0

    for section in sections:
        new_chunks = _chunks_from_section(
            section_text=section["text"],
            heading=section["heading"],
            md_dir=md_dir,
            paper=paper_title,
            run=run_name,
            start_idx=idx,
            chunk_words=chunk_words,
            chunk_overlap=chunk_overlap,
        )
        idx += len(new_chunks)
        chunks.extend(new_chunks)

    if extra_metadata:
        for c in chunks:
            c.metadata.update(extra_metadata)

    return chunks


# ── Main ingestion function ───────────────────────────────────────────────────

def ingest_extracted_data(
    data_root: str | Path,
    run_selection: dict[str, str],
    retriever,
    chunk_words: int = 600,
    chunk_overlap: int = 120,
    force_recreate: bool = False,
) -> dict:
    """
    Ingests selected runs from extracted_data/ into the EmbeddingRetriever.

    For each paper/run: chunks the markdown — text sections get text embeddings,
    sections containing images get multimodal embeddings (text + image together).

    Args:
        data_root:     path to extracted_data/ folder
        run_selection: {paper_title: run_name} — one run per paper
        retriever:     EmbeddingRetriever instance
        chunk_words:   words per text chunk
        chunk_overlap: overlap between adjacent text chunks
        force_recreate: wipe and rebuild the index from scratch

    Returns:
        {n_papers, n_text_chunks, n_image_chunks, n_total}
    """
    data_root = Path(data_root)
    all_chunks: list[Chunk] = []
    n_papers = 0

    for paper_title, run_name in sorted(run_selection.items()):
        processed_dir = data_root / paper_title / run_name / "processed"
        if not processed_dir.exists():
            print(f"  [skip] {paper_title} / {run_name} — processed/ not found")
            continue

        md_files = list(processed_dir.glob("*.md"))
        if not md_files:
            print(f"  [skip] {paper_title} / {run_name} — no .md file")
            continue

        md_path = md_files[0]
        paper_chunks = chunk_markdown_file(
            md_path, paper_title, run_name,
            chunk_words=chunk_words,
            chunk_overlap=chunk_overlap,
        )

        if paper_chunks:
            n_text = sum(1 for c in paper_chunks if c.metadata.get("item_type") == "text")
            n_img = sum(1 for c in paper_chunks if c.metadata.get("item_type") == "multimodal")
            all_chunks.extend(paper_chunks)
            n_papers += 1
            print(f"  {paper_title[:55]:<57}  {n_text} text + {n_img} multimodal chunks")

    if not all_chunks:
        print("No chunks produced — check run_selection and extracted_data/ structure.")
        return {"n_papers": 0, "n_text_chunks": 0, "n_image_chunks": 0, "n_total": 0}

    n_text_total = sum(1 for c in all_chunks if c.metadata.get("item_type") == "text")
    n_img_total = sum(1 for c in all_chunks if c.metadata.get("item_type") == "multimodal")

    print(f"\nTotal: {n_papers} papers, {n_text_total} text + {n_img_total} multimodal chunks")
    print("Embedding with Gemini Embedding 2…")

    retriever.create_or_load_index(all_chunks, force_recreate=force_recreate)

    return {
        "n_papers": n_papers,
        "n_text_chunks": n_text_total,
        "n_image_chunks": n_img_total,
        "n_total": len(all_chunks),
    }
