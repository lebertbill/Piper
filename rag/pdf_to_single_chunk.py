import os
import re
from typing import List
from .pdf2chunks import is_heading, get_heading_level


async def read_doc_as_single_chunk(doc, file_path: str, metadata: dict) -> List:
    """
    Processes a parsed PDF document into a single chunk containing the entire text and all tables.
    """
    # --- Combine and sort all document elements (text and tables) ---
    combined = []
    if doc.texts:
        for t in doc.texts:
            combined.append(('text', getattr(t, 'page', 0), getattr(t, 'bbox', [0, 0, 0, 0])[1], t))
    if doc.tables:
        for tbl in doc.tables:
            combined.append(('table', getattr(tbl, 'page', 0), getattr(tbl, 'bbox', [0, 0, 0, 0])[1], tbl))
    combined.sort(key=lambda x: (x[1], x[2]))

    # --- Part 1: Process Text Content into Sections ---
    font_sizes = [getattr(t, 'font_size', 0) for _, _, _, t in combined if
                  getattr(t, 'text', '').strip() and getattr(t, 'font_size', 0) > 0]
    avg_font_size = sum(font_sizes) / len(font_sizes) if font_sizes else 10.0

    sections = []
    current_heading_path = ["Introduction / Abstract"]
    current_section = {"heading": " / ".join(current_heading_path), "content": []}

    for kind, _, _, item in combined:
        if kind == 'text':
            text_str = getattr(item, 'text', '').strip()
            if not text_str: continue

            if is_heading(item, avg_font_size):
                if current_section["content"]: sections.append(current_section)
                new_heading_text = text_str.strip()
                new_level = get_heading_level(new_heading_text)
                current_level = len(current_heading_path)
                if new_level > current_level:
                    current_heading_path.append(new_heading_text)
                else:
                    current_heading_path = current_heading_path[:new_level - 1]
                    current_heading_path.append(new_heading_text)
                current_section = {"heading": " / ".join(current_heading_path), "content": []}
            else:
                current_section["content"].append(text_str)

    if current_section["content"]:
        sections.append(current_section)

    # --- Clean up and filter text sections ---
    cleaned_sections = []
    unwanted_headings = ["acknowledgments", "acknowledgements", "references", "bibliography",
                         "declaration of competing interest"]
    for section in sections:
        heading_lower = section["heading"].lower()
        if any(unwanted in heading_lower for unwanted in unwanted_headings):
            print(f"  -> Skipping section: '{section['heading']}'")
            continue
        section["heading"] = re.sub(r'^(keywords:|abstract)\s*/\s*', '', section["heading"],
                                    flags=re.IGNORECASE).strip()
        cleaned_sections.append(section)

    # Assemble the full text content from the cleaned sections
    full_text_content = []
    for section in cleaned_sections:
        full_text_content.append(f"## {section['heading']}\n\n")
        full_text_content.append(" ".join(section['content']))
        full_text_content.append("\n\n")

    # --- Part 2: Process Tables into a separate list ---
    all_tables_content = []
    for i, (kind, page, y_pos, item) in enumerate(combined):
        if kind == 'table':
            table_bundle = []
            table_title = f"Table on page {page + 1}"  # Default title

            # Look backwards for a table label
            for j in range(1, 6):
                if i - j >= 0 and combined[i - j][0] == 'text':
                    prev_text_item = combined[i - j][3]
                    prev_text = getattr(prev_text_item, 'text', '').strip()
                    if re.match(r'^Table\s+\d+', prev_text, re.IGNORECASE) and len(prev_text.split()) < 30:
                        table_title = prev_text
                        break
            
            table_bundle.append(f"### {table_title}\n")

            # Add the table markdown itself
            table_md = item.export_to_markdown(doc=doc)
            table_bundle.append(table_md)

            # Look forwards for footnotes
            footnotes = []
            for j in range(1, 6):
                if i + j < len(combined) and combined[i + j][0] == 'text':
                    next_text_item = combined[i + j][3]
                    next_text = getattr(next_text_item, 'text', '').strip()
                    if next_text and (next_text[0].islower() or next_text.startswith(('a ', 'b ', 'c ', 'd ', '*'))):
                        footnotes.append(next_text)
                    else:
                        break
            if footnotes:
                table_bundle.append("\n" + "\n".join(footnotes))

            all_tables_content.append("\n".join(table_bundle))

    # --- Combine all text and all tables into a single string ---
    final_document_string = "".join(full_text_content)
    if all_tables_content:
        final_document_string += "\n\n---\n\n# Extracted Tables\n\n"
        final_document_string += "\n\n---\n\n".join(all_tables_content)

    # --- Create a single Chunk object for the entire document ---
    from pdf2chunks import Chunk, get_file_hash # Import necessary components

    content_hash = get_file_hash(file_path)
    meta = metadata or {}
    chunk_meta = {
        "pdf_path": file_path,
        "content_hash": content_hash,
        "chunk_index": 0, # Only one chunk
        "section": "Full Document",
        "title": meta.get("title", ""),
        "authors": meta.get("authors", []),
        "journal": meta.get("journal", ""),
        "publisher": meta.get("publisher", ""),
        "year": meta.get("year", ""),
        "DOI": meta.get("DOI", ""),
        "item_type": meta.get("item_type", "")
    }

    base_name = os.path.basename(file_path)
    chunk_name = meta.get("title") or base_name
    
    # Return a list containing the single chunk
    return [Chunk(text=final_document_string.strip(), name=chunk_name, metadata=chunk_meta)]