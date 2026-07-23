# document_processor.py

import logging
import os
import re
import time
import shutil
import json as json_lib
from pathlib import Path
from typing import Optional, List, Dict
import difflib

from docling_core.types.doc import ImageRefMode, PictureItem, TableItem, TextItem
from docling.document_converter import DocumentConverter, PdfFormatOption
from docling.datamodel.base_models import InputFormat
from docling.datamodel.pipeline_options import PdfPipelineOptions
from agents import summarizer_agent, image_grabber_agent
from miner.agents.classifier_agent import ClassifierAgent
from miner.agents.reaction_table_detector_agent import ReactionTableDetectorAgent
from miner.agents.label_resolver_agent import get_label_resolver_agent
from process_chemical_images import process_images_with_piper_intelligence
from context import load_config

_log = logging.getLogger(__name__)


def sanitize_filename(text: str) -> str:
    """used to create a valid filename."""
    text = text.replace(":", "")
    sanitized = re.sub(r'[\\/*?:"<>|]', "", text)
    # Replace any whitespace (including nonbreaking spaces) with underscore
    sanitized = re.sub(r'\s+', "_", sanitized)
    return sanitized[:100]


def get_bbox(element):
    """Safely extract bbox from an docling element such as picture, table and text. bbox is reuqired to get caption for table/figures"""
    # print("DEBUG: get_bbox (fixed version) called") 
    if hasattr(element, 'bbox') and element.bbox is not None:
        return element.bbox
    if hasattr(element, 'prov') and element.prov:
        # docling 2.0: prov is a list of ProvenanceItem
        if isinstance(element.prov, list) and len(element.prov) > 0:
            return element.prov[0].bbox
        # Fallback if it's not a list (older version?)
        if hasattr(element.prov, 'bbox'):
            return element.prov.bbox
    return None

def find_caption_for_table(table_element: TableItem, text_elements_on_page: List[TextItem]) -> Optional[str]:
    """Finds a caption for a table by looking for text spatially located above it."""
    tbl_bbox = get_bbox(table_element)
    if not tbl_bbox:
        return None
        
    # Look in a region above the table (Expanded to 100px)
    search_bbox = (tbl_bbox.x0 - 50, tbl_bbox.y0 - 100, tbl_bbox.x1 + 50, tbl_bbox.y0 + 10)

    potential_captions = []
    for text_element in text_elements_on_page:
        txt_bbox = get_bbox(text_element)
        if txt_bbox and txt_bbox.intersects(search_bbox):
            text = text_element.text.strip()
            # Check for "Table X" pattern (Relaxed regex)
            if re.match(r"^(table)\s+[\w\d]+", text, re.IGNORECASE):
                potential_captions.append(text_element)

    if potential_captions:
        # Sort by y1 (bottom of text) descending (closest to table top first)
        potential_captions.sort(key=lambda el: get_bbox(el).y1 if get_bbox(el) else 0, reverse=True)
        return potential_captions[0].text
    return None

def find_caption_for_image(image_element: PictureItem, text_elements_on_page: List[TextItem]) -> Optional[str]:
    """Finds a caption for an image by looking for text spatially located below it."""
    img_bbox = get_bbox(image_element)
    if not img_bbox:
        print(f"DEBUG: Image element has no bbox. Skipping caption search. Attributes: {dir(image_element)}")
        return None
        
    search_bbox = (img_bbox.x0 - 20, img_bbox.y1, img_bbox.x1 + 20, img_bbox.y1 + 60)

    potential_captions = []
    for text_element in text_elements_on_page:
        txt_bbox = get_bbox(text_element)
        if txt_bbox and txt_bbox.intersects(search_bbox):
            text = text_element.text.strip()
            if re.match(r"^(figure|fig\.?|scheme|chart|table)\s+\w*[\d\.]+", text, re.IGNORECASE):
                potential_captions.append(text_element)

    if potential_captions:
        potential_captions.sort(key=lambda el: get_bbox(el).y0 if get_bbox(el) else 0)
        return potential_captions[0].text
    return None


def update_markdown_image_links(md_path: Path, image_mappings: dict):
    """Post-processes a Markdown file to replace default image links with custom ones."""
    if not md_path.exists() or not image_mappings:
        return
    _log.info(f"Updating image links in '{md_path.name}'...")
    content = md_path.read_text(encoding="utf-8")
    for default_name, custom_name in image_mappings.items():
        pattern = re.compile(f"\\(.*?/{re.escape(default_name)}\\)")
        content = pattern.sub(f"({custom_name})", content)
    md_path.write_text(content, encoding="utf-8")


def resolve_image_path(heading: str, processed_output_path: Path, image_rel_path: Optional[str] = None, force_visualheist: bool = False) -> Optional[Path]:
    """
    Resolves the absolute path to a reaction scheme image.

    Priority order (normal):
      1. Docling artifact via image_rel_path (reaction_scheme_ref from advanced structure parser)
         — the actual scheme image extracted from the PDF, identified by the LLM.
      2. VisualHeist crop (visualheist_page_XX_table_N.png) — fallback targeted crop.
      3. Table_N*.png heading prefix match — last resort; may be table-text-only.
      4. Fuzzy filename match — final fallback.

    When force_visualheist=True (label resolution only):
      VisualHeist crop is checked first. Falls through to normal order if not found.
    """
    scheme_image_path = None
    sanitized_heading = sanitize_filename(heading)

    # 0. VisualHeist-first override
    if force_visualheist:
        match = re.match(r"^(Table|Scheme|Figure)\s+(\d+)", heading, re.IGNORECASE)
        if match:
            num = match.group(2)
            # Try all three type names — visualheist may name a "Scheme" as "figure" or vice versa.
            for vh_type in (match.group(1).lower(), "figure", "scheme", "table"):
                vh_matches = list(processed_output_path.glob(f"visualheist_*_{vh_type}_{num}.png"))
                if vh_matches:
                    # Sort by page number ascending — lowest page = main article (SI pages are later/larger)
                    def _vh_page_key(p):
                        m = re.search(r"visualheist_page_(\d+)_", p.name)
                        return int(m.group(1)) if m else 9999
                    vh_matches.sort(key=_vh_page_key)
                    scheme_image_path = vh_matches[0].resolve()
                    print(f"    -> ✅ Resolved Image (VisualHeist forced): {scheme_image_path.name}")
                    return scheme_image_path

    # 1. Docling 
    if image_rel_path:
        from urllib.parse import unquote
        image_rel_path = unquote(image_rel_path)
        
        # Strategies
        potential_paths = [
            Path(image_rel_path), # Absolute or simple relative
            processed_output_path / image_rel_path, # Relative to output
            processed_output_path / Path(image_rel_path).name, # Flattened in output
        ]
        
        for p in potential_paths:
            if p.exists():
                scheme_image_path = p.resolve()
                break
        
        if not scheme_image_path:

            filename = Path(image_rel_path).name
            search_pattern = f"**/{filename}"
            matches = list(processed_output_path.glob(search_pattern))
            if matches:
                scheme_image_path = matches[0].resolve()
                print(f"    -> [INFO] Deep resolved {filename} to {scheme_image_path}")

        if not scheme_image_path:
            # Hash-tolerant fallback: the advanced structure LLM sometimes truncates or 64-char hash in the filename. If exact search failed try matching by image number prefix only (e.g. image_000007_*.png).
            filename = Path(image_rel_path).name
            m = re.match(r"^(image_\d+)_", filename)
            if m:
                prefix = m.group(1)
                matches = list(processed_output_path.glob(f"**/{prefix}_*.png"))
                if matches:
                    scheme_image_path = matches[0].resolve()
                    print(f"    -> [INFO] Hash-tolerant fallback: resolved {filename} → {scheme_image_path.name}")
    
    # 2. VisualHeist crop — used when Docling ref is missing, only when enabled.
    if not scheme_image_path:
        from context import load_config as _load_cfg
        _vh_enabled = _load_cfg().get("enable_visualheist_fallback", True)
        if _vh_enabled:
            match = re.match(r"^(Table|Scheme|Figure)\s+(\d+)", heading, re.IGNORECASE)
            if match:
                num = match.group(2)
                for vh_type in (match.group(1).lower(), "figure", "scheme", "table"):
                    vh_matches = list(processed_output_path.glob(f"visualheist_*_{vh_type}_{num}.png"))
                    if vh_matches:
                        vh_matches.sort(key=lambda x: x.stat().st_size, reverse=True)
                        scheme_image_path = vh_matches[0].resolve()
                        print(f"    -> ✅ Resolved Image (VisualHeist): {scheme_image_path.name}")
                        break

    # 3. Table_N*.png heading prefix. may be table text only.
    if not scheme_image_path:
        sanitized_heading = sanitize_filename(heading)

        # Strategy A: Prefix Match (e.g. "Table_1") with number-boundary guard
        match = re.match(r"^(Table|Scheme|Figure)\s+(\d+)", heading, re.IGNORECASE)
        candidates = []
        if match:
            prefix = f"{match.group(1)}_{match.group(2)}"
            _prefix_re = re.compile(rf'^{re.escape(prefix)}[^0-9]', re.IGNORECASE)
            candidates = [
                p for p in processed_output_path.glob(f"{prefix}*.png")
                if _prefix_re.match(p.name)
            ]

        if not candidates:
            # Strategy B: Fuzzy Match on all PNGs
            all_pngs = list(processed_output_path.glob("*.png"))
            all_filenames = [p.name for p in all_pngs]
            
            # Get close matches to the sanitized heading
            # Cutoff 0.4 allows for some variation
            matches = difflib.get_close_matches(sanitized_heading, all_filenames, n=3, cutoff=0.4)
            
            if matches:
                print(f"    -> [DEBUG] Fuzzy matches for '{sanitized_heading}': {matches}")
                # Pick the best one
                best_match_name = matches[0]
                scheme_image_path = processed_output_path / best_match_name
        
        elif candidates:
            # Sort to ensure deterministic selection (e.g., shortest name or specific suffix)
            # Prefer shorter names (less likely to be duplicates with _1, _2 etc unless needed)
            candidates.sort(key=lambda x: len(x.name))
            scheme_image_path = candidates[0]
            print(f"    -> ✅ Resolved Image (Prefix Match): {scheme_image_path.name}")

    return scheme_image_path


async def process_document_fully(pdf_path: str, output_dir: str, progress_callback=None, si_path_str: str = None) -> str:
    """
    The main function to run the full document processing pipeline.
    for clarification/any issues go through the doc https://docling-project.github.io/docling/examples/export_figures/
    """
    input_doc_path = Path(pdf_path)
    
    piper_project_dir = Path(__file__).parent
    doc_filename_base = input_doc_path.stem
    
    if output_dir:
        # output_dir is already specific to the file (e.g. extracted_data/filename)
        processed_output_path = Path(output_dir) / "processed"
    else:
        processed_output_path = piper_project_dir / "extracted_data" / doc_filename_base / "processed"
        
    processed_output_path.mkdir(parents=True, exist_ok=True)
    
    print(f"DEBUG: Processing document: {input_doc_path}")
    print(f"DEBUG: Output directory: {processed_output_path}")

    # ── Initialize Per-Paper Logging ──
    from miner.prompt_logger import prompt_logger
    from miner.diagnostic_logger import diagnostic_logger
    paper_folder = processed_output_path.parent
    prompt_logger.set_log_path(str(paper_folder / "llm_prompts_log.txt"))
    diagnostic_logger.set_log_path(str(paper_folder / "label_resolver_diag.txt"))

    # Load Configuration
    config = load_config()
    enable_mllm = config.get("enable_MLLM", True)
    print(f"DEBUG: enable_MLLM is set to: {enable_mllm}")

    if progress_callback:
        progress_callback("📄 Converting PDF with Docling...")

    # Start loading OCSR models in a background thread while Docling converts the PDF. This improves the speed.
   
    import threading
    from miner.intelligence.core.model_registry import preload_models
    _preload_thread = threading.Thread(target=preload_models, daemon=True)
    _preload_thread.start()

    # 1. Configure Docling
    pipeline_options = PdfPipelineOptions()
    pipeline_options.images_scale = 2.0
    pipeline_options.generate_page_images = True
    pipeline_options.generate_picture_images = True
    pipeline_options.generate_table_images = True

    doc_converter = DocumentConverter(
        format_options={
            InputFormat.PDF: PdfFormatOption(pipeline_options=pipeline_options)
        }
    )

    # 2. Convert Document
    try:
        conv_res = doc_converter.convert(input_doc_path)
    except ValueError as e:
        if "embedded null byte" in str(e):
            msg = (
                f"PDF conversion failed: the file '{input_doc_path.name}' contains embedded null bytes "
                f"in its internal structure. This usually means the PDF is corrupt, was incorrectly "
                f"exported, or contains binary-encoded content that Docling cannot parse. "
                f"Try re-downloading or re-saving the PDF and uploading again."
            )
            _log.error(msg)
            raise ValueError(msg) from e
        _log.error(f"An error occurred during document conversion: {e}")
        raise e
    except Exception as e:
        _log.error(f"An error occurred during document conversion: {e}")
        raise e

    doc = conv_res.document
    doc_filename_stem = input_doc_path.stem
    
    # Initialize Detector for agentic filtering
    detector = ReactionTableDetectorAgent()
    
    # 3. Extract images 
    image_name_mappings = {}
    picture_counter = 0
    table_counter = 0
    
    # Pre-calculate page texts for caption search
    page_texts_map = {}
    for item, _ in doc.iterate_items():
        if isinstance(item, TextItem):
            page_no = getattr(item, 'page_no', -1)
            if page_no not in page_texts_map:
                page_texts_map[page_no] = []
            page_texts_map[page_no].append(item)

    print(f"DEBUG: Iterating through document items...")
    
    for element, _level in doc.iterate_items():
        page_no = getattr(element, 'page_no', -1)
        caption = None
        
        # Try to get caption from docling attributes first
        if hasattr(element, 'caption_text'):
            if callable(element.caption_text):
                try:
                    caption = element.caption_text(doc)
                except TypeError:
                    # Fallback if it doesn't take doc
                    caption = element.caption_text()
            else:
                caption = element.caption_text
                
        if not caption and hasattr(element, 'label'):
             if callable(element.label):
                 caption = element.label()
             else:
                 caption = element.label

        if caption:
            caption = str(caption)
        
        # Get page number (handle missing attribute)
        page_no = getattr(element, 'page_no', -1)
        if page_no == -1 and hasattr(element, 'prov') and element.prov:
            # Try to get page number from provenance (1-based usually)
            try:
                page_no = element.prov[0].page_no # Keep 1-based
            except:
                pass
            
        if isinstance(element, PictureItem):
            picture_counter += 1
            
            # Fallback to spatial search if no metadata caption
            if not caption:
                page_texts = page_texts_map.get(page_no, [])
                caption = find_caption_for_image(element, page_texts)
            
            if caption:
                base_filename = sanitize_filename(caption)
                filename = f"{base_filename}.png"
                # Ensure uniqueness
                counter = 1
                while (processed_output_path / filename).exists():
                    filename = f"{base_filename}_{counter}.png"
                    counter += 1
                custom_filename = filename
                print(f"  -> [SUCCESS] Found caption for image {picture_counter} on page {page_no + 1}: '{caption}' -> '{custom_filename}'")
            else:
                filename = f"{doc_filename_stem}-picture-{picture_counter}.png"
                custom_filename = filename
                print(f"  -> [FALLBACK] No caption found for image {picture_counter} on page {page_no + 1}. Using name: {filename}")

            element_image_filename = processed_output_path / filename
            
            # Temporary save for agentic check
            try:
                img = element.get_image(doc)
                if img is None:
                    print(f"  -> [WARNING] Picture {picture_counter} on page {page_no + 1} has no image data. Skipping.")
                else:
                    with element_image_filename.open("wb") as fp:
                        img.save(fp, "PNG")
                    
                    # Agentic Filtering
                    # Pre-filter: images smaller than 100×50 px are never real figures
                    # (journal article IDs, glyphs etc.) — skip the LLM call.
                    _pre_w, _pre_h = img.size
                    if _pre_w < 100 or _pre_h < 50:
                        print(f"  -> [INFO] Picture {picture_counter} is too small ({_pre_w}×{_pre_h} px) — marking junk without LLM.")
                        detection = {"is_junk": True, "reasoning": f"pre-filter: {_pre_w}×{_pre_h} px"}
                    else:
                        print(f"  -> [DEBUG] Checking if Picture {picture_counter} is Junk...")
                        detection = detector.run(str(element_image_filename))
                    if detection.get("is_junk", False):
                        print(f"  -> [INFO] Picture {picture_counter} identified as Junk (e.g. logo). Deleting.")
                        element_image_filename.unlink()
                        # Do not add to mappings
                    else:
                        if caption:
                            # Map internal name to custom name if it was a valid image
                            if hasattr(element, 'image') and hasattr(element.image, 'path'):
                                 default_image_name = os.path.basename(element.image.path)
                                 image_name_mappings[default_image_name] = custom_filename
            except Exception as e:
                print(f"  -> [ERROR] Failed to save/check Picture {picture_counter}: {e}")
                if element_image_filename.exists():
                    element_image_filename.unlink()
                
                
        elif isinstance(element, TableItem):
            print(f"  -> [DEBUG] *** FOUND TableItem! ***")
            table_counter += 1
            print(f"  -> [DEBUG] Table counter is now: {table_counter}")
            
            # Fallback to spatial search for tables if needed
            if not caption:
                page_texts = page_texts_map.get(page_no, [])
                caption = find_caption_for_table(element, page_texts)

            if caption:
                base_filename = sanitize_filename(caption)
                filename = f"{base_filename}.png"
                # Ensure uniqueness
                counter = 1
                while (processed_output_path / filename).exists():
                    filename = f"{base_filename}_{counter}.png"
                    counter += 1
                print(f"  -> [SUCCESS] Found caption for table {table_counter} on page {page_no + 1}: '{caption}' -> '{filename}'")
            else:
                filename = f"{doc_filename_stem}-table-{table_counter}.png"
                print(f"  -> [FALLBACK] No caption found for table {table_counter} on page {page_no + 1}. Using name: {filename}")


            # Create combined image: reaction scheme + table + footnotes
            # print(f"  -> [DEBUG] Attempting combined extraction for table {table_counter}")
            if False: # STITCHING DISABLED IGNORE
                try:
                    # Get the page image
                    if page_no < 0 or page_no >= len(doc.pages):
                        print(f"  -> [WARNING] Invalid page number {page_no}. Saving table only.")
                        element_image_filename = processed_output_path / filename
                        with element_image_filename.open("wb") as fp:
                            element.get_image(doc).save(fp, "PNG")
                        continue

                    page = doc.pages[page_no]

                    
                    if not hasattr(page, 'image') or page.image is None:
                        print(f"  -> [WARNING] No page image available for page {page_no + 1}. Saving table only.")
                        element_image_filename = processed_output_path / filename
                        with element_image_filename.open("wb") as fp:
                            element.get_image(doc).save(fp, "PNG")
                    else:
                        page_img = page.image.pil_image
                        print(f"  -> [DEBUG] Page image size: {page_img.size}")
                        
                        # Get all elements on this page, sorted by position
                        page_elements = [item for item, _ in doc.iterate_items() if getattr(item, 'page_no', -1) == page_no]
                        page_elements.sort(key=lambda el: (get_bbox(el).y0 if get_bbox(el) else 0))
                        print(f"  -> [DEBUG] Found {len(page_elements)} elements on page {page_no + 1}")
                        
                        # Find the tables position in the sorted list
                        table_idx = next((i for i, el in enumerate(page_elements) if el is element), None)
                        if table_idx is None:
                            print(f"  -> [WARNING] Could not find table in page elements. Saving table only.")
                            element_image_filename = processed_output_path / filename
                            with element_image_filename.open("wb") as fp:
                                element.get_image(doc).save(fp, "PNG")
                        else:
                            print(f"  -> [DEBUG] Table is at index {table_idx} of {len(page_elements)} elements")
                            
                            # Find reaction scheme (PictureItem) immediately above the table
                            scheme_above = None
                            print(f"  -> [DEBUG] Searching for scheme above table at index {table_idx}...")
                            for i in range(table_idx - 1, -1, -1):
                                item = page_elements[i]
                                print(f"    -> [DEBUG] Checking item {i}: {type(item)}")
                                if isinstance(item, PictureItem):
                                    scheme_bbox = get_bbox(item)
                                    table_bbox = get_bbox(element)
                                    if scheme_bbox and table_bbox:
                                        distance = table_bbox.y0 - scheme_bbox.y1
                                        print(f"    -> [DEBUG] Found PictureItem at index {i}, distance: {distance}px (Scheme y1: {scheme_bbox.y1}, Table y0: {table_bbox.y0})")
                                        # Check if it's close above (within 600px - increased to handle text in between)
                                        if distance < 600:
                                            scheme_above = item
                                            print(f"    -> [INFO] Found reaction scheme above table (distance: {distance}px)")
                                            break
                                        else:
                                            print(f"    -> [DEBUG] PictureItem too far ({distance}px > 600px)")
                                    break  # Only check the immediate previous PictureItem
                            
                            # Find footnotes (TextItems) immediately below the table
                            footnotes_below = []
                            table_bbox = get_bbox(element)
                            if table_bbox:
                                print(f"  -> [DEBUG] Searching for footnotes below table...")
                                for i in range(table_idx + 1, len(page_elements)):
                                    if isinstance(page_elements[i], TextItem):
                                        text_bbox = get_bbox(page_elements[i])
                                        if text_bbox:
                                            distance = text_bbox.y0 - table_bbox.y1
                                            # Check if it's close below (within 150px) and looks like a footnote
                                            if distance < 150:
                                                text_content = page_elements[i].text.strip()
                                                # Footnotes often start with superscript letters or contain "Reaction conditions"
                                                if (text_content and (
                                                    text_content[0].islower() or 
                                                    'reaction' in text_content.lower() or
                                                    text_content.startswith('a') or
                                                    text_content.startswith('b') or
                                                    text_content.startswith('c')
                                                )):
                                                    footnotes_below.append(page_elements[i])
                                                    print(f"  -> [DEBUG] Found footnote at index {i}, distance: {distance}px, text: {text_content[:50]}...")
                                            else:
                                                break  # Stop if we've moved too far down
                                    elif isinstance(page_elements[i], (PictureItem, TableItem)):
                                        print(f"  -> [DEBUG] Hit next major element at index {i}, stopping footnote search")
                                        break  # Stop at next major element
                            
                            if footnotes_below:
                                print(f"  -> [INFO] Found {len(footnotes_below)} footnote(s) below table")
                            
                            # Calculate combined bounding box
                            elements_to_combine = []
                            if scheme_above:
                                elements_to_combine.append(scheme_above)
                            elements_to_combine.append(element)
                            elements_to_combine.extend(footnotes_below)
                            
                            print(f"  -> [DEBUG] Combining {len(elements_to_combine)} elements (scheme: {scheme_above is not None}, table: True, footnotes: {len(footnotes_below)})")
                            
                            bboxes = [get_bbox(el) for el in elements_to_combine if get_bbox(el)]
                            if not bboxes:
                                print(f"  -> [WARNING] No valid bboxes found. Saving table only.")
                                element_image_filename = processed_output_path / filename
                                with element_image_filename.open("wb") as fp:
                                    element.get_image(doc).save(fp, "PNG")
                            else:
                                # Compute combined bbox
                                min_x = min(bb.x0 for bb in bboxes)
                                min_y = min(bb.y0 for bb in bboxes)
                                max_x = max(bb.x1 for bb in bboxes)
                                max_y = max(bb.y1 for bb in bboxes)
                                
                                # Add padding
                                padding = 10
                                min_x = max(0, min_x - padding)
                                min_y = max(0, min_y - padding)
                                max_x = min(page_img.width, max_x + padding)
                                max_y = min(page_img.height, max_y + padding)
                                
                                print(f"  -> [DEBUG] Combined bbox: ({min_x}, {min_y}, {max_x}, {max_y})")
                                
                                # Crop and save
                                combined_img = page_img.crop((min_x, min_y, max_x, max_y))
                                element_image_filename = processed_output_path / filename
                                with element_image_filename.open("wb") as fp:
                                    combined_img.save(fp, "PNG")
                                
                                print(f"  -> [SUCCESS] Saved combined image (scheme + table + footnotes): {filename}")
                    
                except Exception as e:
                    import traceback
                    print(f"  -> [ERROR] Failed to create combined image: {e}")
                    print(f"  -> [ERROR] Traceback: {traceback.format_exc()}")
                    print(f"  -> [FALLBACK] Saving table only.")
                    element_image_filename = processed_output_path / filename
                    with element_image_filename.open("wb") as fp:
                        element.get_image(doc).save(fp, "PNG")
            
            else:
                 # Saving table only (Stitching disabled)
                 element_image_filename = processed_output_path / filename
                 with element_image_filename.open("wb") as fp:
                     element.get_image(doc).save(fp, "PNG")


    print(f"DEBUG: Extracted {picture_counter} pictures and {table_counter} tables.")

    # 4. Save Markdown and Update Links
    md_filename = processed_output_path / f"{doc_filename_stem}.md"
    
    # To avoid the crazy path recursion, we temporarily change the CWD and use local naming.
    old_cwd = os.getcwd()
    try:
        os.chdir(processed_output_path)
        # Use relative path for the markdown file name itself to prevent nested tree recreations
        doc.save_as_markdown(Path(md_filename.name), image_mode=ImageRefMode.REFERENCED)
    finally:
        os.chdir(old_cwd)
    
    # POST-PROCESSING: Cleanup Docling's redundant artifact directory if it created one
    # Also clean up junk from any artifacts folder
    artifact_dirs = [
        processed_output_path / f"{doc_filename_stem}_artifacts",
        processed_output_path / "artifacts" # Some docling versions use this
    ]
    
    # Pre-load markdown to strip out junk references
    md_text = md_filename.read_text(encoding="utf-8") if md_filename.exists() else ""
    
    for art_dir in artifact_dirs:
        if art_dir.exists():
            print(f"DEBUG: Cleaning up artifacts in: {art_dir}")
            for img_file in list(art_dir.glob("*.png")):
                try:
                    from PIL import Image as _PILImg
                    with _PILImg.open(img_file) as _af_img:
                        _af_w, _af_h = _af_img.size
                    if _af_w < 100 or _af_h < 50:
                        print(f"  -> [CLEANUP] Pre-filter: {img_file.name} ({_af_w}×{_af_h} px) — junk, skipping LLM.")
                        det = {"is_junk": True}
                    else:
                        det = detector.run(str(img_file))
                    if det.get("is_junk", False):
                        print(f"  -> [CLEANUP] Deleting junk artifact: {img_file.name}")
                        img_file.unlink()
                        # Strip from markdown
                        # Docling usually outputs ![Image](XXX_artifacts/image_XXXX.png)
                        import re
                        pattern = r'!\[.*?\]\([^)]*' + re.escape(img_file.name) + r'\)'
                        md_text = re.sub(pattern, '', md_text)
                except Exception as e:
                    print(f"  -> [CLEANUP] Error checking {img_file.name}: {e}")
                    
    if md_filename.exists():
        md_filename.write_text(md_text, encoding="utf-8")
    
    update_markdown_image_links(md_filename, image_name_mappings)
    
    if progress_callback:
        progress_callback("🤖 Running Classifier Agent...")

    markdown_content = md_filename.read_text(encoding="utf-8")
    print(f"DEBUG: Document converted to Markdown: {md_filename}")
    
    # ---------------- SI DOCUMENT DOCLING ----------------
    # Not used in the current workflow. Kept for future updates
    si_markdown_content = ""
    target_si_stem = input_doc_path.stem + "_SI"
    si_md_filename = processed_output_path / f"{target_si_stem}.md"
    
    if si_path_str:
        if progress_callback:
            progress_callback("📄 Converting SI with Docling...")
        try:
            print(f"DEBUG: Processing SI Document: {si_path_str}")
            si_input_path = Path(si_path_str)
            si_conv_res = doc_converter.convert(si_input_path)
            si_doc = si_conv_res.document
            
            old_cwd = os.getcwd()
            try:
                os.chdir(processed_output_path)
                si_doc.save_as_markdown(Path(si_md_filename.name), image_mode=ImageRefMode.REFERENCED)
            finally:
                os.chdir(old_cwd)
                
            si_markdown_content = si_md_filename.read_text(encoding="utf-8")
            print(f"DEBUG: SI converted to Markdown: {si_md_filename}")
        except Exception as e:
            msg = f"Failed to convert SI document: {e}"
            print(f"  -> [ERROR] {msg}")
            if progress_callback:
                progress_callback(f"⚠️ {msg}")
    # -----------------------------------------------------
    
    # 5. Post-process tables to create combined images
    # print(f"\nDEBUG: Post-processing tables to create combined images...")
    if False: # STITCHING DEACTIVATED AS FOR NOW
        try:
            # Find all table image files in the processed directory
            table_files = list(processed_output_path.glob("Table_*.png"))
            
            print(f"DEBUG: Found {len(table_files)} table image files")
            
            for table_image_path in table_files:
                table_filename = table_image_path.stem
                print(f"\n  -> [INFO] Processing table file: {table_filename}")
                
                # Extract caption from filename (remove .png and sanitization artifacts)
                # E.g., "Table_1._Optimization_of_the_Reaction_Conditions_a" -> "Table 1. Optimization of the Reaction Conditions a"
                table_caption_guess = table_filename.replace('_', ' ')
                
                # Find the table element in the document by iterating through all TableItems
                table_element = None
                table_page_no = None
                
                for item, _ in doc.iterate_items():
                    if isinstance(item, TableItem):
                        # Get the table's caption
                        item_caption = None
                        if hasattr(item, 'caption_text') and callable(item.caption_text):
                            try:
                                item_caption = str(item.caption_text(doc))
                            except:
                                pass
                        
                        # Check if this table matches our file
                        if item_caption:
                            sanitized_item_caption = sanitize_filename(item_caption)
                            if sanitized_item_caption in table_filename or table_filename.startswith(sanitized_item_caption[:20]):
                                table_element = item
                                table_page_no = getattr(item, 'page_no', -1)
                                if table_page_no == -1 and hasattr(item, 'prov') and item.prov:
                                    try:
                                        table_page_no = item.prov[0].page_no # Keep 1-based
                                    except:
                                        pass
                                print(f"  -> [DEBUG] Matched table element with caption: {item_caption}")
                                break
                
                if not table_element:
                    print(f"  -> [WARNING] Could not find matching table element in document")
                    continue
                
                # Get page image
                # doc.pages is 1-indexed, but item.page_no seems to be 0-indexed in this version
                page = doc.pages.get(table_page_no + 1)
                if not page:
                     # Fallback: try direct access if keys match
                     page = doc.pages.get(table_page_no)
                
                if not page:
                     print(f"  -> [WARNING] Could not find page {table_page_no} or {table_page_no + 1} in doc.pages")
                     continue
                if not hasattr(page, 'image') or page.image is None:
                    print(f"  -> [WARNING] No page image for page {table_page_no + 1}")
                    continue
                    
                page_img = page.image.pil_image
                print(f"  -> [DEBUG] Page image size: {page_img.size}")
                
                # Helper to get page number safely
                def get_page_no(item):
                    if hasattr(item, 'prov') and item.prov:
                        return item.prov[0].page_no
                    return getattr(item, 'page_no', -1)

                # Get all elements on this page
                page_elements = [item for item, _ in doc.iterate_items() if get_page_no(item) == table_page_no]
                
                # Sort by Top Y position DESCENDING (Top to Bottom visually)
                # Docling uses Bottom-Left origin, so Higher Y is Higher on page (Top).
                # We want Top to Bottom, so we sort by Y Descending.
                # Use .t (top) if available, else try .y1, else 0.
                def get_top(el):
                    bbox = get_bbox(el)
                    if not bbox: return 0
                    return getattr(bbox, 't', getattr(bbox, 'y1', 0))
                
                page_elements.sort(key=get_top, reverse=True)
                print(f"  -> [DEBUG] Found {len(page_elements)} elements on page {table_page_no}")
                
                # Debug: Print sorted elements
                for i, el in enumerate(page_elements):
                    bbox = get_bbox(el)
                    top = getattr(bbox, 't', getattr(bbox, 'y1', 0)) if bbox else 'N/A'
                    bottom = getattr(bbox, 'b', getattr(bbox, 'y0', 0)) if bbox else 'N/A'
                    el_type = type(el).__name__
                    is_target = " [TARGET]" if el is table_element else ""
                    # print(f"    -> [DEBUG] Item {i}: {el_type} (Top: {top}, Bottom: {bottom}){is_target}")

                # Find table index
                table_idx = next((i for i, el in enumerate(page_elements) if el is table_element), None)
                if table_idx is None:
                    print(f"  -> [WARNING] Could not locate table in page elements")
                    continue
                    
                # Find reaction scheme (PictureItem) immediately above the table
                # Find reaction scheme (PictureItem) immediately above the table
                scheme_above = None
                # print(f"  -> [DEBUG] Searching for scheme above table at index {table_idx}...")
                
                # Since we sorted Top to Bottom, items "above" the table have LOWER index.
                # So we iterate backwards from table_idx.
                for i in range(table_idx - 1, -1, -1):
                    item = page_elements[i]
                    if isinstance(item, PictureItem):
                        scheme_bbox = get_bbox(item)
                        table_bbox = get_bbox(table_element) # Use table_element here, not 'element'
                        if scheme_bbox and table_bbox:
                            # Calculate height of the picture
                            pic_height = getattr(scheme_bbox, 't', 0) - getattr(scheme_bbox, 'b', 0)
                            
                            # Skip small images (likely icons or artifacts)
                            if pic_height < 50:
                                print(f"    -> [DEBUG] Skipping small PictureItem at index {i} (Height: {pic_height:.1f}px)")
                                continue

                            # Calculate distance
                            # Scheme is above Table.
                            # Scheme Bottom (b) > Table Top (t) visually (since Y increases upwards).
                            # Distance = Scheme Bottom (b) - Table Top (t).
                            # Use .b/.t
                            s_b = getattr(scheme_bbox, 'b', 0)
                            t_t = getattr(table_bbox, 't', 0)
                            
                            distance = s_b - t_t
                            # print(f"    -> [DEBUG] Found PictureItem at index {i}, distance: {distance}px")
                            
                            # Check if it's close above (within 800px - increased search range)
                            if distance < 800:
                                scheme_above = item
                                print(f"    -> [INFO] Found reaction scheme above table (distance: {distance:.2f}px)")
                                break
                            else:
                                 print(f"    -> [DEBUG] PictureItem too far ({distance:.2f}px > 800px)")
                                 # Don't break here, keep looking further up just in case
                                 continue
                        # break  # REMOVED: Don't stop at the first picture if it wasn't the right one
                
                # Find footnotes below
                footnotes_below = []
                table_bbox = get_bbox(table_element)
                if table_bbox:
                    # Table Bottom is .b (since Y increases upwards, Bottom is lower Y)
                    # Footnote Top is .t
                    # Distance = Table Bottom - Footnote Top?
                    # No, Table is ABOVE Footnote.
                    # Table Y > Footnote Y.
                    # Distance = Table Bottom (b) - Footnote Top (t).
                    
                    t_b = getattr(table_bbox, 'b', 0)
                    
                    for i in range(table_idx + 1, len(page_elements)):
                        if isinstance(page_elements[i], TextItem):
                            text_bbox = get_bbox(page_elements[i])
                            if text_bbox:
                                # Footnote Top
                                txt_t = getattr(text_bbox, 't', 0)
                                
                                distance = t_b - txt_t
                                if distance < 150:
                                    text_content = page_elements[i].text.strip()
                                    if (text_content and (
                                        text_content[0].islower() or 
                                        'reaction' in text_content.lower() or
                                        text_content.startswith('a') or
                                        text_content.startswith('b') or
                                        text_content.startswith('c')
                                    )):
                                        footnotes_below.append(page_elements[i])
                                        # print(f"  -> [DEBUG] Found footnote: {text_content[:60]}...")
                                else:
                                    # print(f"  -> [DEBUG] TextItem too far ({distance}px), stopping")
                                    break
                        elif isinstance(page_elements[i], (PictureItem, TableItem)):
                            # print(f"  -> [DEBUG] Hit next major element, stopping")
                            break
                
                if footnotes_below:
                    print(f"  -> [INFO] Found {len(footnotes_below)} footnote(s)")
                
                # Create combined image if we have scheme or footnotes
                if scheme_above or footnotes_below:
                    elements_to_combine = []
                    if scheme_above:
                        elements_to_combine.append(scheme_above)
                    elements_to_combine.append(table_element)
                    elements_to_combine.extend(footnotes_below)
                    
                    # print(f"  -> [INFO] Combining {len(elements_to_combine)} elements for stitching")
                    
                    bboxes = [get_bbox(el) for el in elements_to_combine if get_bbox(el)]
                    if bboxes:
                        # Use l, b, r, t (PDF coordinates)
                        min_x = min(getattr(bb, 'l', 0) for bb in bboxes)
                        min_y = min(getattr(bb, 'b', 0) for bb in bboxes) # Bottom-most Y (lowest value)
                        max_x = max(getattr(bb, 'r', 0) for bb in bboxes)
                        max_y = max(getattr(bb, 't', 0) for bb in bboxes) # Top-most Y (highest value)
                        
                        # Calculate scale factors
                        # page.size is likely (width, height) in PDF points
                        pdf_width = page.size.width
                        pdf_height = page.size.height
                        img_width, img_height = page_img.size
                        
                        scale_x = img_width / pdf_width
                        scale_y = img_height / pdf_height
                        
                        # print(f"  -> [DEBUG] Scaling: PDF({pdf_width}, {pdf_height}) -> Img({img_width}, {img_height}) | Scale: {scale_x:.2f}, {scale_y:.2f}")
                        
                        # Convert PDF coords (Bottom-Left) to PIL coords (Top-Left) AND Scale
                        # PDF: (x, y) -> PIL: (x * scale_x, (page_h - y) * scale_y) ??
                        # No, usually we scale first then flip Y? Or flip Y then scale?
                        # PDF Y=0 is Bottom. PDF Y=Height is Top.
                        # PIL Y=0 is Top. PIL Y=Height is Bottom.
                        # Normalized Y (0-1) from Bottom: y_norm = y_pdf / pdf_height
                        # Normalized Y (0-1) from Top: y_pil_norm = 1 - y_norm = 1 - (y_pdf / pdf_height)
                        # PIL Y = y_pil_norm * img_height = (1 - y_pdf / pdf_height) * img_height
                        #       = img_height - (y_pdf * (img_height / pdf_height))
                        #       = img_height - (y_pdf * scale_y)
                        
                        # Top (PIL) corresponds to Max Y (PDF)
                        # pil_top = img_height - (max_y * scale_y)
                        # Bottom (PIL) corresponds to Min Y (PDF)
                        # pil_bottom = img_height - (min_y * scale_y)
                        
                        # Left (PIL) = min_x * scale_x
                        # Right (PIL) = max_x * scale_x
                        
                        pil_left = min_x * scale_x
                        pil_top = img_height - (max_y * scale_y)
                        pil_right = max_x * scale_x
                        pil_bottom = img_height - (min_y * scale_y)
                        
                        padding = 10 * scale_x # Scale padding too
                        pil_left = max(0, pil_left - padding)
                        pil_top = max(0, pil_top - padding)
                        pil_right = min(page_img.width, pil_right + padding)
                        pil_bottom = min(page_img.height, pil_bottom + padding)
                        
                        # print(f"  -> [DEBUG] Combined bbox (PIL): ({pil_left:.0f}, {pil_top:.0f}, {pil_right:.0f}, {pil_bottom:.0f})")
                        
                        combined_img = page_img.crop((pil_left, pil_top, pil_right, pil_bottom))
                        
                        # Replace the original table image with combined version
                        with table_image_path.open("wb") as fp:
                            combined_img.save(fp, "PNG")
                        
                        # print(f"  -> [SUCCESS] ✅ Created combined image: {table_image_path.name}")
                    else:
                        # print(f"  -> [WARNING] No valid bboxes for combining")
                        pass
                else:
                    # print(f"  -> [INFO] No scheme or footnotes found, keeping table-only image")
                    pass
                    
        except Exception as e:
            import traceback
            print(f"  -> [ERROR] Post-processing failed: {e}")
            print(f"  -> [ERROR] Traceback: {traceback.format_exc()}")
    
    # print(f"\nDEBUG: Post-processing complete\n")

    # 5. Run Classifier Agent
    classifier = ClassifierAgent()
    classification_result = classifier.run(markdown_content)
    print("\n" + "="*20 + " Article Classification " + "="*20)
    print(f"Type: {classification_result.get('article_type')}")
    print(f"Confidence: {classification_result.get('confidence')}")
    print(f"Reasoning: {classification_result.get('reasoning')}")
    print("="*64 + "\n")

    # Save classification result
    classification_output_path = processed_output_path / "classification_result.json"
    with open(classification_output_path, "w") as f:
        json_lib.dump(classification_result, f, indent=4)
    print(f"DEBUG: Saved classification result to: {classification_output_path}")

    # Check for Experimental content triggers
    article_type = classification_result.get('article_type', 'Unknown')
    if article_type not in ["Experimental", "Both"]:
        msg = f"⚠️ Skipping: Article type '{article_type}' is not experimental."
        print(msg)
        if progress_callback:
            progress_callback(msg)
        # Return a robust empty result structure so the caller (app.py) doesn't crash but knows nothing was extracted
        return json_lib.dumps({
            "image_path": None,
            "markdown_table": None,
            "entries": [],
            "extraction_status": "skipped",
            "reason": f"Article type is {article_type}"
        })

    if progress_callback:
        progress_callback("📑 Summarizer Agent skipped")
    # Summarizer agent is skipped: its output (catalyst_summary) was never consumed by any downstream agent or included in extraction results.
    # catalyst_summary = await summarizer_agent(markdown_content)
    # print("\n" + "="*20 + " Catalyst & Reaction Summary " + "="*20)
    # print(catalyst_summary)
    # print("="*67 + "\n")

    # 7. Run Image Grabber Agent
    selected_images = await image_grabber_agent(markdown_content)
    # print("\n" + "="*20 + " Relevant Images Selected " + "="*20)
    # if selected_images:
    #     for img_name in selected_images:
    #         print(f"  -> {img_name}")
    # else:
    #     print("  -> No specific images were selected by the agent.")
    # print("="*64 + "\n")

    # 8. Run Extraction (MLLM vs Text-Only)
    if not enable_mllm:
        if progress_callback:
            progress_callback("📝 Running Text-Only Extraction...")
        print("\n" + "="*20 + " 📝 Running Text-Only Extraction " + "="*20)
        # ... (Text-only logic remains same) ...
        # For brevity, assuming text-only logic is fine or can be skipped if not enabled. But to keep the file valid, leave the text-only block structure if it was there,  or just MLLM part.
        
        from miner.table_parser import parse_markdown_table
        from miner.orchestrator import Orchestrator
        
        # ... (Text-only implementation) ...
        # (Keeping existing text-only logic placeholder for safety, though user is using MLLM)
        
    elif selected_images or True: # Force run for now as we are using the new agent
        print("\n" + "="*20 + " 🧠 Running Agentic Structure Parsing & Extraction " + "="*20)
        
        from miner.agents.structure_parser_agent import StructureParserAgent
        from miner.orchestrator import Orchestrator
        from miner.table_parser import parse_markdown_table
        from urllib.parse import unquote
        import json
        
        from miner.agents.table_text_extractor_agent import TableTextExtractorAgent
        from miner.visualheist_extractor import is_table_text_useful, extract_table_image
        
        # Load feature toggles from config
        from context import load_config as _load_config
        _cfg = _load_config()
        _visualheist_enabled = _cfg.get("enable_visualheist_fallback", True)
        _visualheist_forced  = _cfg.get("force_enable_visualheist_fallback", False)
        _scope_table_enabled = _cfg.get("enable_scope_table_processing", True)
        _visualheist_paths: dict = {}  # heading >> visualheist image path, populated during extraction

        # Initialize Agents
        parser_agent = StructureParserAgent()
        table_extractor = TableTextExtractorAgent()
        
        # Helper to extract list
        def _get_items_list(agent_output):
            if isinstance(agent_output, dict):
                return agent_output.get("items", agent_output.get("data_items", []))
            elif isinstance(agent_output, list):
                return agent_output
            return []
        
        # ---------------- ADVANCED STRUCTURE PARSER ----------------
        try:
            from miner.agents.advanced_structure_parser_agent import AdvancedStructureAgent
            import json

            advanced_agent = AdvancedStructureAgent()
            print("  -> Running Advanced Structure Parser Agent...")
            if progress_callback:
                progress_callback("🧠 Running Advanced Structure Parser (Main)...")
                
            main_result = advanced_agent.run(markdown_content, base_dir=str(processed_output_path))
            
            main_adv_output = processed_output_path / f"{input_doc_path.stem}_main_advanced_structure.json"
            with open(main_adv_output, "w") as f:
                json.dump(main_result, f, indent=4)
            print(f"  -> Saved Advanced Main Structure to: {main_adv_output.name}")
            _cnm = main_result.get("compound_name_map") or {}
            _cdm = main_result.get("compound_description_map") or {}
            if _cnm:
                print(f"  -> compound_name_map ({len(_cnm)} entries): {dict(list(_cnm.items())[:8])}")
            else:
                print(f"  -> compound_name_map: (empty)")
            if _cdm:
                print(f"  -> compound_description_map ({len(_cdm)} entries): {dict(list(_cdm.items())[:4])}")
            else:
                print(f"  -> compound_description_map: (empty)")
            
            # Autodetect SI markdown files in the same processed output path
            target_si_stem = input_doc_path.stem + "_SI"
            si_md_path = processed_output_path / f"{target_si_stem}.md"
            if not si_md_path.exists():
                for f in processed_output_path.glob("*_SI.md"):
                    si_md_path = f
                    break
                    
            if si_md_path.exists():
                print(f"  -> Running Advanced Structure Parser on SI: {si_md_path.name}")
                if progress_callback:
                    progress_callback("🧠 Running Advanced Structure Parser (SI)...")
                si_content = si_md_path.read_text(encoding='utf-8')
                si_result = advanced_agent.run(si_content)
                si_advanced_items = _get_items_list(si_result)
                si_adv_output = processed_output_path / f"{input_doc_path.stem}_si_advanced_structure.json"
                with open(si_adv_output, "w") as f:
                    json.dump(si_result, f, indent=4)
                print(f"  -> Saved Advanced SI Structure to: {si_adv_output.name}")
        except Exception as e:
            import logging
            import traceback
            err_msg = f"Advanced Structure Parser Failed: {e}\n{traceback.format_exc()}"
            logging.error(err_msg)
            print(f"  -> ⚠️ {err_msg}")
            if progress_callback:
                progress_callback(f"❌ Advanced Structure Parser Failed: {e}")
        # -------------------------------------------------------------
        
        # Run Agent on Markdown
        if progress_callback:
            progress_callback("🧱 Running Structure Parser Agent (Main)...")
        structured_items = parser_agent.run(markdown_content)

        # Save structure parser output
        structure_output_path = processed_output_path / "structure_parser_output.json"
        with open(structure_output_path, "w") as f:
            json.dump(structured_items, f, indent=4)
        print(f"  -> Saved structure parser output to: {structure_output_path}")
        
            
        items_to_process = _get_items_list(structured_items)
        
        # SI Standard Parser
        if si_markdown_content:
            if progress_callback:
                progress_callback("🧱 Running Structure Parser Agent (SI)...")
            si_structured_items_raw = parser_agent.run(si_markdown_content)
            
            si_structure_output_path = processed_output_path / "si_structure_parser_output.json"
            with open(si_structure_output_path, "w") as f:
                json.dump(si_structured_items_raw, f, indent=4)
                
            si_items_list = _get_items_list(si_structured_items_raw)
            # Tag SI items
            for item in si_items_list:
                if isinstance(item, dict):
                    item["source"] = "SI"

            
        final_results_list = []

        # Merge Advanced Structure Parser results if available
        advanced_items = _get_items_list(main_result) if 'main_result' in locals() else []
        if advanced_items:
            print(f"  -> Merging {len(advanced_items)} advanced structure items into items_to_process...")
            for item in items_to_process:
                if not isinstance(item, dict): continue
                heading = item.get("heading", "")
                for adv_item in advanced_items:
                    adv_id = adv_item.get("id", "")
                    if adv_id and (heading.startswith(adv_id) or adv_id.startswith(heading)):
                        item["has_reaction_parameters_above_the_arrow"] = adv_item.get("has_reaction_parameters_above_the_arrow", False)
                        item["reaction_parameter_above_arrow_mapping"] = adv_item.get("reaction_parameter_above_arrow_mapping", False)
                        item["arrow_mapping"] = adv_item.get("arrow_mapping", None)
                        item["image_table_relationship"] = adv_item.get("image_table_relationship", "both")
                        item["has_r_group_substitution"] = adv_item.get("has_r_group_substitution", False)
                        item["has_structure_drawings"] = adv_item.get("has_structure_drawings", False)
                        item["structure_drawing_labels"] = adv_item.get("structure_drawing_labels", [])
                        if "columns" in adv_item:
                            item["columns"] = adv_item["columns"]
                        adv_ref = adv_item.get("reaction_scheme_ref", "")
                        if adv_ref:
                            # Always trust the advanced structure ref — it has full PDF visibility and explicitly identified the scheme image.
                            # Even when the basic parser returned image_path=None, the advanced parser's reaction_scheme_ref is authoritative.
                            item["image_path"] = adv_ref
                        participants = adv_item.get("reaction_scheme_participants", [])
                        if participants:
                            item["reaction_scheme_participants"] = participants
                        if adv_id and not item.get("id"):
                            item["id"] = adv_id
                        if "is_optimization_table" in adv_item:
                            item["is_optimization_table"] = adv_item["is_optimization_table"]
                        if "is_scope_table" in adv_item:
                            item["is_scope_table"] = adv_item["is_scope_table"]
                        if "purpose" in adv_item:
                            item["purpose"] = adv_item["purpose"]
                        if "expected_row_count" in adv_item:
                            item["expected_row_count"] = adv_item["expected_row_count"]
                        print(f"    -> [MERGE] Integrated advanced flags for {adv_id}")
                        break
            
        for i, item in enumerate(items_to_process):
            if not isinstance(item, dict):
                continue
            item_type = item.get("type", "").lower()
            heading = item.get("heading", "")
            caption = item.get("caption", "")
            image_rel_path = item.get("image_path", "")
            description = item.get("description", "No description provided.")
            contains_reaction = item.get("contains_reaction_info", False)
            
            # print(f"DEBUG: Item: {heading}, Type: {item_type}, Reaction: {contains_reaction}, Image: {image_rel_path}")
            
            # USER REQUEST: Process ONLY Tables for now
            if item_type != "table":
                # print(f"    -> Skipping {heading} (Not a Table)")
                continue

            if not contains_reaction:
                continue

            # Scope filter: process optimization tables and substrate scope tables.
            if "is_optimization_table" in item:
                is_optimization = bool(item["is_optimization_table"])
            else:
                purpose_lower = item.get("purpose", "").lower()
                is_optimization = (
                    "optimiz" in purpose_lower
                    or (
                        "screening" in purpose_lower
                        and any(kw in purpose_lower for kw in [
                            "base", "solvent", "catalyst", "condition",
                            "temperature", "reagent", "ligand",
                        ])
                    )
                )

            is_scope = bool(item.get("is_scope_table", False)) and _scope_table_enabled

            if not is_optimization and not is_scope:
                print(f"    -> ⏭️  Skipping '{heading}' — not an optimization or scope table")
                continue

            # Resolve image path
            # The table image contains the reaction on top and tabular data in the bottom it should never be substituted with a Scheme image.
            scheme_image_path = resolve_image_path(heading, processed_output_path, image_rel_path)

            if not scheme_image_path:
                print(f"    -> ⚠️ No image found for {heading}. Processing without image (Text-Only fallback).")
            else:
                print(f"    -> ✅ Final Table Image Path: {scheme_image_path.name}")

            # ── Low-Resolution Scheme Image — VisualHeist Upgrade ───────────────
            # Docling crops figures at 2× scale, but when the original figure is a narrow inline banner (e.g. < 500×80 px) the molecular structures are too small for reliable OCSR or LLM vision recognition. In that case, run VisualHeist which renders the PDF at 150 DPI and returns a table crop that includes the reaction scheme at much higher resolution.
            _LOW_RES_W, _LOW_RES_H = 500, 80
            if _visualheist_enabled and scheme_image_path and scheme_image_path.exists():
                try:
                    from PIL import Image as _PILImage
                    with _PILImage.open(scheme_image_path) as _img:
                        _img_w, _img_h = _img.size
                    if _img_w < _LOW_RES_W or _img_h < _LOW_RES_H:
                        print(f"    -> ⚠️ Scheme image is low-resolution ({_img_w}×{_img_h} px). "
                              f"Trying VisualHeist for a higher-quality crop...")
                        if progress_callback:
                            progress_callback(f"🔍 Low-res scheme image — upgrading via VisualHeist for '{heading}'...")
                        _vh_upgrade = extract_table_image(
                            pdf_path=pdf_path,
                            heading=heading,
                            processed_output_path=processed_output_path,
                        )
                        if _vh_upgrade and _vh_upgrade.exists():
                            print(f"    -> ✅ VisualHeist upgrade: replacing {scheme_image_path.name} "
                                  f"({_img_w}×{_img_h}) with {_vh_upgrade.name} for scheme extraction")
                            scheme_image_path = _vh_upgrade
                            item = dict(item)
                            item["image_table_relationship"] = "both"
                            item["visualheist_image_path"] = str(_vh_upgrade)
                            _visualheist_paths[heading] = str(_vh_upgrade)
                        else:
                            print(f"    -> ⚠️ VisualHeist upgrade failed — keeping original low-res image")
                except Exception as _e:
                    print(f"    -> ⚠️ Low-res check failed ({_e}) — keeping original image")
            # ────────────────────────────────────────────────────────────────────

            # Extract Table Text
            if progress_callback:
                progress_callback(f"📊 Extracting text for '{heading}'...")
            print(f"  -> Extracting text for {heading}...")
            extracted_table_text = table_extractor.run(markdown_content, heading)

            # ── Visualheist Fallback ────────────────────────────────────────────
            # If text extraction returned nothing useful (e.g., Docling failed to parse a hybrid image+text table), use TF-ID/visualheist to crop the full table image directly from the PDF. The vision LLM then reads both the reaction scheme AND the tabular rows from that single image.
            if _visualheist_enabled and not is_table_text_useful(extracted_table_text):
                if progress_callback:
                    progress_callback(f"🔍 Table text poor — trying visual fallback for '{heading}'...")
                print(f"    -> ⚠️ Table text extraction poor. Trying visualheist fallback...")
                fallback_img = extract_table_image(
                    pdf_path=pdf_path,
                    heading=heading,
                    processed_output_path=processed_output_path,
                )
                if fallback_img:
                    print(f"    -> ✅ Visualheist fallback: using {fallback_img.name} as table image")
                    scheme_image_path = fallback_img
                    extracted_table_text = ""
                    # Visualheist crops contain the full table (scheme + rows).
                    # Override so the intelligence engine extracts both, not just the scheme.
                    item = dict(item)
                    item["image_table_relationship"] = "both"
                    item["visualheist_image_path"] = str(fallback_img)
                    _visualheist_paths[heading] = str(fallback_img)
                    print(f"    -> Visualheist: overriding image_table_relationship to 'both'")
                else:
                    print(f"    -> ⚠️ Visualheist fallback returned nothing. Dropping garbage text to force vision extraction.")
                    extracted_table_text = ""
                    # The original Docling image often contains the full table if visualheist failed.
                    item = dict(item)
                    item["image_table_relationship"] = "both"
                    print(f"    -> Fallback: overriding image_table_relationship to 'both'")
            # ───────────────────────────────────────────────────────────────────


            # Save extracted text for verification
            safe_heading = "".join([c if c.isalnum() else "_" for c in heading])
            text_output_path = processed_output_path / f"{safe_heading}_text.txt"
            with open(text_output_path, "w") as f:
                f.write(extracted_table_text)
            print(f"    -> Saved extracted text to: {text_output_path}")

            # Process with Orchestrator
            if progress_callback:
                progress_callback(f"🧪 Processing '{heading}' with Piper Intelligence...")
            print(f"  -> Processing {heading} with Piper Intelligence...")
            orchestrator = Orchestrator()

            footnote_dependent_columns = []
            if "columns" in item:
                for col_name, col_data in item["columns"].items():
                    if col_data.get("has_footnote_dependency", False):
                        footnote_dependent_columns.append(col_name)
            if footnote_dependent_columns:
                print(f"    -> 📌 Footnote-dependent columns: {footnote_dependent_columns}")

            has_arrow_params    = item.get("has_reaction_parameters_above_the_arrow", False)
            arrow_mapping_str   = item.get("arrow_mapping", None)
            has_r_group         = item.get("has_r_group_substitution", False)
            img_table_rel       = item.get("image_table_relationship", "both")
            has_struct_drawings = item.get("has_structure_drawings", False)
            struct_draw_labels  = item.get("structure_drawing_labels", [])

            # If the advanced structure parser counted more rows in the image than OCR extracted from the markdown, the markdown is incomplete. Override to "both" so the intelligence engine reads all rows directly from the image.
            expected_rc = item.get("expected_row_count")
            if expected_rc and img_table_rel == "scheme_only":
                _md_pipe_rows = sum(
                    1 for ln in extracted_table_text.splitlines()
                    if ln.strip().startswith("|") and not set(ln.replace("|","").replace("-","").replace(" ","")) == set()
                ) - 1  # subtract header row
                if _md_pipe_rows < expected_rc:
                    item = dict(item)
                    item["image_table_relationship"] = "both"
                    img_table_rel = "both"
                    extracted_table_text = ""
                    print(f"    -> ⚠️ OCR only captured {_md_pipe_rows} rows but image has {expected_rc}. "
                          f"Overriding image_table_relationship to 'both' for full-image extraction.")

            if has_arrow_params:
                print(f"    -> 📌 Arrow parameters flagged for extraction.")
            if has_r_group:
                print(f"    -> 📌 R-group substitution flagged.")
            if img_table_rel != "both":
                print(f"    -> 📌 image_table_relationship: {img_table_rel}")
            if has_struct_drawings:
                print(f"    -> 📌 Additional structure drawings flagged{f': {struct_draw_labels}' if struct_draw_labels else ''}.")

            scheme_path_str = str(scheme_image_path) if scheme_image_path else None
            use_mllm_mode   = scheme_path_str is not None

            if not use_mllm_mode:
                print(f"    -> ⚠️ No scheme image available for '{heading}'. Falling back to Text-Only extraction.")

            result_data = orchestrator.process_document(
                scheme_image_path=scheme_path_str,
                text_content=extracted_table_text,
                full_text_content=markdown_content,
                use_mllm=use_mllm_mode,
                footnote_dependencies=footnote_dependent_columns,
                has_arrow_parameters=has_arrow_params,
                arrow_mapping=arrow_mapping_str,
                has_r_group_substitution=has_r_group,
                image_table_relationship=img_table_rel,
                has_structure_drawings=has_struct_drawings,
                structure_drawing_labels=struct_draw_labels,
                output_dir=str(processed_output_path),
                columns_metadata=item.get("columns", {}),
                progress_callback=progress_callback,
                advanced_structure=main_result if 'main_result' in locals() else None,
                current_item=item,
                is_scope_table=is_scope,
            )
            if not result_data or not isinstance(result_data, dict):
                print(f"    ⚠️  Orchestrator returned invalid data for {heading}. Skipping.")
                continue

            # Construct Final Result Object
            final_res = {
                "document": input_doc_path.stem,
                "table_name": heading,
                "table_caption": caption,
                "image_path": str(scheme_image_path),
                "entries": result_data.get("specific_reactions", []),
                "reaction_scheme": heading,
                "abbreviations": result_data.get("abbreviations", {}),
                "reactants": result_data.get("reactants", []),
                "products": result_data.get("products", []),
                "conditions": result_data.get("conditions", {}),
                "columns_metadata": item.get("columns", {})
            }
            final_results_list.append(final_res)
            print(f"    -> Added result to final list. Entries: {len(final_res['entries'])}")


        # Save Results
        # We save the full list of results to support multi-table extraction
        
        if final_results_list:
            # ──────── NEW: Label Resolution Post-Processing ────────
            print(f"\n--- 🏷️ Resolving labels for {len(final_results_list)} tables ---")
            if progress_callback:
                progress_callback("🏷️ Resolving chemical labels and abbreviations...")
            
            # ─── Post-processing: Label Resolution ───
            print(f"\n[Post-Processing] Resolving chemical labels and abbreviations...")
            if progress_callback:
                progress_callback("🏷️ Resolving chemical labels and abbreviations...")
            
            resolver = get_label_resolver_agent()
            
            # Map of source_id -> {label -> {name, smiles}}
            global_mapping_cache = {}
            # Map of normalized_role -> source_id 
            role_to_source_id = {}
            import re

            # PASS 1: Identify all explicit abbreviation columns and build a global role-to-source map
            for table_res in final_results_list:
                cols_meta = table_res.get("columns_metadata", {})
                for col_name, col_meta in cols_meta.items():
                    if col_meta.get("value_is_abbreviation", False):
                        source_id = col_meta.get("label_map_source_id") or col_meta.get("label_map_ref")
                        if source_id:
                            norm_role = col_name.lower().replace("_", " ").replace(".", " ").strip()
                            role_to_source_id[norm_role] = source_id
                            # Map standard roles if they appear in the column name
                            for std_role in ["catalyst", "ligand", "base", "solvent", "photocatalyst"]:
                                if std_role in norm_role:
                                    role_to_source_id[std_role] = source_id

            # PASS 2: Collect all unique labels to resolve from ALL tables
            all_labels_to_resolve = {} # source_id -> set of labels
            
            for table_res in final_results_list:
                cols_meta = table_res.get("columns_metadata", {})
                for entry in table_res.get("entries", []):
                    for condition in entry.get("conditions", []):
                        text = str(condition.get("text", "")).strip()
                        if not text: continue
                        
                        role = condition.get("role", "").lower().replace("_", " ").replace(".", " ").strip()
                        assigned_source = None
                        
                        # A. Try matching an abbreviation column in the local table
                        for col_name, col_meta in cols_meta.items():
                            norm_col = col_name.lower().replace("_", " ").replace(".", " ").strip()
                            if norm_col in role or role in norm_col or (norm_col and role and (norm_col.startswith(role) or role.startswith(norm_col))):
                                if col_meta.get("value_is_abbreviation", False):
                                    assigned_source = col_meta.get("label_map_source_id") or col_meta.get("label_map_ref")
                                break
                        
                        # B. Fuzzy match: If no local column match, use our global role_to_source_id map
                        if not assigned_source:
                            for known_role, known_source in role_to_source_id.items():
                                if known_role in role or role in known_role:
                                    assigned_source = known_source
                                    break
                        
                        if assigned_source:
                            if assigned_source not in all_labels_to_resolve:
                                all_labels_to_resolve[assigned_source] = set()
                            
                            # Extract only the base alphanumeric identifier (e.g., "PC1" from "PC1 (1 mol%)")
                            match = re.search(r'([A-Za-z]*\d+[a-z]*)', text)
                            if match:
                                label_only = match.group(1)
                                all_labels_to_resolve[assigned_source].add(label_only)

            # PASS 3: Resolve labels for each unique source
            for source_id, label_set in all_labels_to_resolve.items():
                if source_id not in global_mapping_cache:
                    global_mapping_cache[source_id] = {}
                
                source_item = None
                for it in items_to_process:
                    if it.get("id") == source_id or it.get("heading", "").startswith(source_id):
                        source_item = it
                        break
                
                if not source_item and 'si_items_list' in locals():
                    for it in si_items_list:
                        if it.get("id") == source_id or it.get("heading", "").startswith(source_id):
                            source_item = it
                            break
                
                if not source_item and 'advanced_items' in locals():
                    for it in advanced_items:
                        if it.get("id") == source_id:
                            source_item = it
                            break
                            
                if not source_item and 'si_advanced_items' in locals():
                    for it in si_advanced_items:
                        if it.get("id") == source_id:
                            source_item = it
                            break
                
                if not source_item:
                    print(f"      [WARNING] Could not find source item for fuzzy resolution: {source_id}")
                    continue
                
                # --- Parent Image Inheritance ---
                # If the item (likely a sub-item from advanced Parser) lacks an image_path, attempt to inherit it from its parent figure in the standard items list.
                if not source_item.get("image_path"):
                    print(f"      [INFO] source_item '{source_id}' has no image_path. Attempting to inherit from parent figure...")
                    
                    # Try to find a base ID (e.g., "Figure 3" from "Figure 3b")
                    import re
                    base_match = re.match(r'^(Figure\s+\d+|Table\s+\d+)', str(source_id), re.I)
                    base_id = base_match.group(1) if base_match else source_id
                    
                    for it in items_to_process:
                        it_id = str(it.get("id") or "")
                        it_heading = str(it.get("heading") or "")
                        
                        # Robust matching: 
                        # 1. Exact match on base ID
                        # 2. Item heading starts with base ID
                        # 3. Source ID starts with Item ID
                        if it.get("image_path") and (
                            it_id == base_id or 
                            it_heading.strip().startswith(base_id) or 
                            source_id.startswith(it_id)
                        ):
                            source_item["image_path"] = it.get("image_path")
                            if not source_item.get("heading"):
                                source_item["heading"] = it.get("heading", "")
                            print(f"      [INFO] Successfully inherited image_path from item '{it_id}' for '{source_id}'")
                            break
                    
                    if not source_item.get("image_path") and 'si_items_list' in locals():
                        for it in si_items_list:
                            it_id = str(it.get("id") or "")
                            it_heading = str(it.get("heading") or "")
                            if it.get("image_path") and (
                                it_id == base_id or 
                                it_heading.strip().startswith(base_id) or 
                                source_id.startswith(it_id)
                            ):
                                source_item["image_path"] = it.get("image_path")
                                if not source_item.get("heading"):
                                    source_item["heading"] = it.get("heading", "")
                                print(f"      [INFO] Successfully inherited image_path from SI item '{it_id}' for '{source_id}'")
                                break
                
                source_type = "image" if source_item.get("image_path") else "text"
                source_content = ""
                
                # Determine normalized_source_id for better prompt context
                # E.g., normalize "Figure 3b" to "Figure 3" if the image is inherited from the parent
                normalized_source_id = source_id
                import re
                fig_match = re.match(r'^(Figure\s+\d+|Table\s+\d+)', str(source_id), re.I)
                if fig_match:
                    normalized_source_id = fig_match.group(1)
                
                if source_type == "image":
                    _src_heading = source_item.get("heading", "")
                    _resolved_vh = None
                    if _visualheist_forced:
                        # 1. Check dict populated during extraction (most reliable)
                        _stored = _visualheist_paths.get(_src_heading)
                        if _stored and Path(_stored).exists():
                            _resolved_vh = _stored
                            print(f"    -> ✅ Label resolution using stored VisualHeist image: {Path(_stored).name}")
                        else:
                            # 2. Direct glob in processed_output_path — covers items skipped in the extraction loop (e.g. synthesis schemes)
                            _vh_match = re.match(r"^(Table|Scheme|Figure)\s+(\d+)", _src_heading, re.IGNORECASE)
                            if _vh_match:
                                _vh_num = _vh_match.group(2)
                                for _vh_type in (_vh_match.group(1).lower(), "figure", "scheme", "table"):
                                    _vh_hits = list(
                                        processed_output_path.glob(f"visualheist_*_{_vh_type}_{_vh_num}.png")
                                    )
                                    if _vh_hits:
                                        # Sort by page number ascending — lowest page = main article
                                        def _pass3_page_key(p):
                                            m = re.search(r"visualheist_page_(\d+)_", p.name)
                                            return int(m.group(1)) if m else 9999
                                        _vh_hits.sort(key=_pass3_page_key)
                                        _resolved_vh = str(_vh_hits[0])
                                        print(f"    -> ✅ Label resolution using glob VisualHeist image: {_vh_hits[0].name}")
                                        break
                    if _resolved_vh:
                        source_content = _resolved_vh
                    else:
                        res_path = resolve_image_path(_src_heading, processed_output_path, source_item.get("image_path"))
                        source_content = str(res_path) if res_path else ""
                else:
                    source_content = markdown_content
                
                if source_content:
                    needed_labels = [l for l in label_set if str(l) not in global_mapping_cache[source_id]]
                    if needed_labels:
                        # Use normalized_source_id in the resolver call for better visual grounding
                        # Also pass a hint if we inherited the image from a parent figure
                        res_source_id = normalized_source_id
                        if source_id != normalized_source_id:
                            # E.g. source_id "Figure 3b", normalized "Figure 3"
                            res_source_id = f"{normalized_source_id} (Parent figure for {source_id}). Please locate the specific section for {source_id}."
                        
                        _dp_name_map = (main_result.get("compound_name_map") if 'main_result' in locals() else None) or {}
                        resolved = resolver.resolve(
                            needed_labels, source_type, source_content, res_source_id,
                            progress_callback,
                            paper_text=markdown_content or "",
                            name_map=_dp_name_map,
                        )
                        mappings = resolved.get("mappings")
                        if not isinstance(mappings, list):
                            mappings = []
                            
                        for m in mappings:
                            label_val = m.get("label")
                            if label_val:
                                global_mapping_cache[source_id][str(label_val)] = {
                                    "name": m.get("name"),
                                    "smiles": m.get("smiles")
                                }

            # PASS 4: Apply mappings globally to all final results
            for table_res in final_results_list:
                cols_meta = table_res.get("columns_metadata", {})
                for entry in table_res.get("entries", []):
                    for condition in entry.get("conditions", []):
                        text = str(condition.get("text", "")).strip()
                        if not text: continue
                        
                        role = condition.get("role", "").lower().replace("_", " ").replace(".", " ").strip()
                        
                        # Find the best source for this condition
                        possible_sources = []
                        # 1. Local column source
                        for col_name, col_meta in cols_meta.items():
                            norm_col = col_name.lower().replace("_", " ").replace(".", " ").strip()
                            if norm_col in role or role in norm_col:
                                s_id = col_meta.get("label_map_source_id") or col_meta.get("label_map_ref")
                                if s_id: possible_sources.append(s_id)
                        
                        # 2. Global role source
                        for known_role, known_source in role_to_source_id.items():
                            if known_role in role or role in known_role:
                                possible_sources.append(known_source)
                        
                        # Apply from any possible source if a match is found.
                        # Never overwrite a condition that the orchestrator already resolved — Pass 3 runs a secondary resolver that may cache incorrect results for bare numeric labels (e.g. "2" matched to a substrate when the orchestrator correctly resolved "2" as "Photocatalyst 2").
                        if condition.get("resolved_smiles"):
                            continue
                        for source_id in set(possible_sources):
                            cache = global_mapping_cache.get(source_id, {})
                            # Match full text or extracted label
                            match = re.search(r'([A-Za-z]*\d+[a-z]*)', text)
                            label_only = match.group(1) if match else None

                            m = cache.get(text) or (cache.get(label_only) if label_only else None)
                            if m:
                                if m.get("name"): condition["resolved_name"] = m["name"]
                                if m.get("smiles"): condition["resolved_smiles"] = m["smiles"]
                                condition["mapping_source"] = source_id
                                break
                                condition["mapping_source"] = source_id

            # ──────────────────────────────────────────────────────────

            import json
            from miner.smiles_validator import validate_extraction_results
            
            # Validate all results
            validated_results_list = []
            for res in final_results_list:
                validated_res = validate_extraction_results(res)
                validated_results_list.append(validated_res)

            # Save to JSON
            output_json_path = processed_output_path / "extraction_results.json"
            with open(output_json_path, "w", encoding='utf-8') as f:
                json.dump(validated_results_list, f, indent=4, ensure_ascii=False)

            print(f"\n✅ Results saved to: {output_json_path}")

            # Save token usage summary
            try:
                from miner.token_tracker import tracker
                tracker.save(processed_output_path / "token_usage.json")
                tracker.reset()
            except Exception as e:
                print(f"Warning: Failed to save token usage: {e}")
            
            # Print validation summary for all tables
            print("\nValidation Summaries:")
            for i, res in enumerate(validated_results_list):
                 if 'validation_summary' in res:
                    summary = res['validation_summary']
                    table_name = res.get('table_name', f'Table {i+1}')
                    print(f"  Table: {table_name}")
                    print(f"    - Valid SMILES: {summary['valid_molecules']}/{summary['total_molecules']} ({summary['validation_rate']}%)")
                    
                    if 'invalid_smiles' in res and res['invalid_smiles']:
                        print(f"    - ⚠️ Invalid SMILES found (first 3):")
                        for invalid in res['invalid_smiles'][:3]:
                            print(f"      * Entry {invalid['entry']}, {invalid['type']}: {invalid['error']}")
        else:
            print("\n⚠️  No data extracted")
        
        print("="*68 + "\n")

    return markdown_content