import os
import ssl
import re
import hashlib 
import fitz 
from typing import List, Optional
from docling.document_converter import DocumentConverter, PdfFormatOption
from docling.datamodel.base_models import InputFormat
from docling.datamodel.pipeline_options import PdfPipelineOptions
from metadata import extract_metadata
from context import load_config, get_parameters


def get_file_hash(file_path: str) -> str:
    """Calculates the SHA256 hash of a file's content to uniquely identify it."""
    sha256_hash = hashlib.sha256()
    try:
        with open(file_path, "rb") as f:
            # Read and update hash in chunks of 4K to handle large files
            for byte_block in iter(lambda: f.read(4096), b""):
                sha256_hash.update(byte_block)
        return sha256_hash.hexdigest()
    except IOError as e:
        print(f"⚠️ Could not read file for hashing: {e}")
        return None


def ensure_ocr_dependencies(lang_list: Optional[List[str]] = None):
    """
    Ensure EasyOCR models are installed for the given languages.
    """
    if lang_list is None:
        lang_list = ["en"]
    ssl._create_default_https_context = ssl._create_unverified_context
    try:
        import easyocr
        print("🔍 Checking OCR models...")
        easyocr.Reader(lang_list, download_enabled=True, verbose=False)
        print("✅ OCR models ready.")
    except ImportError:
        print("⚠️ EasyOCR not installed. Installing via pip...")
        os.system("pip install easyocr")
        import easyocr
        easyocr.Reader(lang_list, download_enabled=True, verbose=False)
        print("✅ OCR models installed and ready.")
    except Exception as e:
        print(f"❌ OCR setup failed: {e}")
        raise


class Chunk:
    def __init__(self, text: str, name: str, metadata: dict):
        self.text = text
        self.name = name
        self.metadata = metadata


def get_heading_level(heading_text):
    """Determines the hierarchical level of a heading."""
    match = re.match(r'^((\d+(\.\d+)*)|([A-Z]))\.', heading_text)
    if match: return heading_text.split()[0].count('.') + 1
    return 1


def is_heading(text_obj, avg_font_size=10.0) -> bool:
    """
    Heuristically determines if a text object is a section heading.
    """
    text = getattr(text_obj, 'text', '').strip()
    if not text or len(text) > 200: return False
    exclude_patterns = [
        r'^(Table|Figure|Scheme|Fig\.)\s*\d+', r'journal of|contents lists available at|research article|article',
        r'https?://', r'\d+\s*\|\s*.+', r'et al\.', r'received|accepted|published', r'^\(cid:\d+\)',
        r'^[a-zA-Z]{1,2}-\d+$'
    ]
    if any(re.search(p, text, re.IGNORECASE) for p in exclude_patterns): return False
    words = text.split()
    if len(words) > 15 or text.isdigit(): return False
    font_name = getattr(text_obj, 'font_name', '').lower()
    is_bold = 'bold' in font_name
    font_size = getattr(text_obj, 'font_size', avg_font_size)
    if re.match(r'^((\d+(\.\d+)*)|([A-Z]))\.\s+', text): return True
    common_headings = [
        'introduction', 'results and discussion', 'results', 'discussion', 'conclusion', 'conclusions',
        'experimental section', 'experimental', 'summary', 'abstract', 'keywords', 'references',
        'acknowledgments', 'acknowledgements', 'declaration of competing interest', 'supplementary data', 'appendix'
    ]
    if len(words) < 8 and any(text.lower().startswith(h) for h in common_headings): return True
    if is_bold and font_size > avg_font_size * 1.1 and (text.istitle() or text.isupper()): return True
    return False


async def read_doc(file_path: str, chunk_params: tuple, metadata: dict) -> List[Chunk]:
    """
    Reads a PDF and chunks it semantically based on headings.
    Accepts pre-fetched metadata to avoid redundant lookups.
    """
    try:
        # --- Calculate content hash at the beginning for de-duplication ---
        content_hash = get_file_hash(file_path)
        if not content_hash:
            print(f"Skipping file due to hashing error: {file_path}")
            return []

        doc = None
        # --- Rely solely on Docling for structural analysis ---
        print("[INFO] Attempting structural parsing with Docling...")
        pipeline_options = PdfPipelineOptions(
            do_ocr=True,
            do_table_structure=True,
        )
        pdf_format_option = PdfFormatOption(pipeline_options=pipeline_options)
        doc_converter = DocumentConverter(
            format_options={InputFormat.PDF: pdf_format_option}
        )
        conv_result = doc_converter.convert(file_path)
        doc = conv_result.document
        
        # Unpack chunking parameters passed from the main script
        chunk_size, chunk_overlap, _ = chunk_params
        
        # --- Combine and sort all document elements (text and tables) ---
        combined = []
        if doc.texts:
            for t in doc.texts:
                combined.append(('text', getattr(t, 'page', 0), getattr(t, 'bbox', [0, 0, 0, 0])[1], t))
        if doc.tables:
            for tbl in doc.tables:
                combined.append(('table', getattr(tbl, 'page', 0), getattr(tbl, 'bbox', [0, 0, 0, 0])[1], tbl))
        combined.sort(key=lambda x: (x[1], x[2]))

        # --- Part 1: Process Text Content into Sections and Chunks ---
        font_sizes = [getattr(t, 'font_size', 0) for _, _, _, t in combined if getattr(t, 'text', '').strip() and getattr(t, 'font_size', 0) > 0]
        avg_font_size = sum(font_sizes) / len(font_sizes) if font_sizes else 10.0
        
        current_heading_path = ["Introduction / Abstract"]
        sections = []
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

        # --- Clean up and filter sections ---
        cleaned_sections = []
        unwanted_headings = ["acknowledgments", "acknowledgements", "references", "bibliography", "declaration of competing interest"]
        for section in sections:
            heading_lower = section["heading"].lower()
            # Skip unwanted sections entirely
            if any(unwanted in heading_lower for unwanted in unwanted_headings):
                print(f"  -> Skipping section: '{section['heading']}'")
                continue
            # Clean up prefixes from section headings
            section["heading"] = re.sub(r'^(keywords:|abstract)\s*/\s*', '', section["heading"], flags=re.IGNORECASE).strip()
            cleaned_sections.append(section)

        final_chunks_text = []
        for section in cleaned_sections:
            section_content_str = " ".join(section['content'])
            words = section_content_str.split()
            if not words: continue
            for i in range(0, len(words), chunk_size - chunk_overlap):
                chunk_text = " ".join(words[i:i + chunk_size])
                # Associate the chunk with its section heading
                final_chunks_text.append((section['heading'], chunk_text))

        # --- Part 2: Process Tables into their own dedicated chunks ---
        table_chunks_text = []
        for i, (kind, page, y_pos, item) in enumerate(combined):
            if kind == 'table':
                table_content_parts = []
                table_label = f"Table on page {page+1}" # Default label

                # Look backwards up to 5 text blocks for a table label (e.g., "Table 1.")
                for j in range(1, 6):
                    if i - j >= 0 and combined[i-j][0] == 'text':
                        prev_text_item = combined[i-j][3]
                        prev_text = getattr(prev_text_item, 'text', '').strip()
                        # A label often starts with "Table X" and is a relatively short line.
                        if re.match(r'^Table\s+\d+', prev_text, re.IGNORECASE) and len(prev_text.split()) < 30:
                            table_label = prev_text
                            table_content_parts.append(table_label)
                            print(f"  -> Found label for table: '{table_label}'")
                            break # Stop after finding the first valid title

                # Add the table markdown itself
                table_md = item.export_to_markdown(doc=doc)
                table_content_parts.append(table_md)

                # Look forwards up to 5 text blocks for footnotes
                for j in range(1, 6):
                    if i + j < len(combined) and combined[i+j][0] == 'text':
                        next_text_item = combined[i+j][3]
                        next_text = getattr(next_text_item, 'text', '').strip()
                        # Heuristic: footnotes often start with a lowercase letter, a symbol, or a superscript-like char 'a'.
                        if next_text and (next_text[0].islower() or next_text.startswith(('a ', 'b ', 'c ', 'd ', '*'))):
                            table_content_parts.append(next_text)
                            print(f"  -> Found footnote for table: '{next_text[:60]}...'")
                        else:
                            # If the text doesn't look like a footnote, stop scanning forward.
                            break
                
                full_table_chunk = "\n\n".join(table_content_parts)
                table_chunks_text.append((table_label, full_table_chunk))
        
        # Combine text chunks and table chunks
        final_chunks_text.extend(table_chunks_text)

        # Use the pre-fetched metadata passed into the function
        meta = metadata or {}

        base_name = os.path.basename(file_path)
        final_chunks: List[Chunk] = []
        
        # Create Chunk objects with metadata
        for i, (section_heading, chunk_text) in enumerate(final_chunks_text):
            chunk_meta = {
                "pdf_path": file_path,
                "content_hash": content_hash, # Add the hash to the metadata
                "chunk_index": i,
                "section": section_heading, # Add the section name to metadata
                "title": meta.get("title", ""),
                "authors": meta.get("authors", []),
                "journal": meta.get("journal", ""),
                "publisher": meta.get("publisher", ""),
                "year": meta.get("year", ""),
                "DOI": meta.get("DOI", ""),
                "item_type": meta.get("item_type", "")
            }
            chunk_name = meta.get("title") or base_name
            final_chunks.append(Chunk(text=chunk_text.strip(), name=chunk_name, metadata=chunk_meta))

        if final_chunks:
            print(f"[VERIFY] Metadata for first chunk (in pdf2chunks): {final_chunks[0].metadata}")

        return final_chunks

    except Exception as e:
        print(f"⚠️ Error converting {file_path} with Docling: {e}")
        return []

read_doc.get_file_hash = get_file_hash
