import os
import re
import difflib
from typing import Optional

import fitz  
import httpx
from PyPDF2 import PdfReader
from dotenv import load_dotenv
from context import load_config
from docling.document_converter import DocumentConverter

#Based on SciX project
# Load environment variables from a .env file
load_dotenv()

BASE_URL = "https://api.crossref.org"


def similar(a: str, b: str) -> float:
    """Calculates the similarity ratio between two strings."""
    return difflib.SequenceMatcher(None, a or "", b or "").ratio()


async def fetch_crossref_metadata(doi: str = None, title: str = None, similarity_threshold: float = 0.8) -> Optional[dict]:
    """
    Fetches publication metadata from the CrossRef API using a DOI or title.
    """
    config = load_config()
    # Prioritize environment variable, then fall back to config file
    email = os.getenv("CROSSREF_EMAIL")
    if not email:
        cr_config = config.get("crossref", {})
        email = cr_config.get("email", None)
    headers = {"User-Agent": f"MetadataFetcher/1.0 (mailto:{email})"} if email else {}

    async with httpx.AsyncClient(timeout=30.0) as client:
        record = None
        try:
            if doi:
                url = f"{BASE_URL}/works/{doi}"
                resp = await client.get(url, headers=headers)
                if resp.status_code == 200:
                    record = resp.json().get("message")

            if not record and title:
                params = {"query.bibliographic": title, "rows": 5}
                resp = await client.get(f"{BASE_URL}/works", params=params, headers=headers)
                if resp.status_code == 200:
                    items = resp.json().get("message", {}).get("items", [])
                    if items:
                        best_match = max(items, key=lambda x: similar(title.lower(), x.get("title", [""])[0].lower()))
                        record = best_match

            if not record:
                return None

            year = ""
            for date_field in ["published-print", "published-online", "created"]:
                date_parts = record.get(date_field, {}).get("date-parts")
                if date_parts and date_parts[0]:
                    year = str(date_parts[0][0])
                    break

            authors = [f"{a.get('given', '').strip()} {a.get('family', '').strip()}".strip() for a in
                       record.get("author", []) if a.get('given') or a.get('family')]

            result = {
                "DOI": record.get("DOI", ""),
                "title": record.get("title", [""])[0] or "",
                "item_type": record.get("type", ""),
                "authors": authors,
                "journal": record.get("container-title", [""])[0] or "",
                "publisher": record.get("publisher", "") or "",
                "year": year,
            }

            if title and not doi and similar(title.lower(), result["title"].lower()) < similarity_threshold:
                return None

            return result

        except Exception as e:
            print(f"[ERROR] Crossref fetch error: {e}")
            return None



def parse_pdf_for_doi(pdf_path: str) -> Optional[str]:
    """Scans the first two pages of a PDF for a DOI."""
    try:
        doc = fitz.open(pdf_path)
        doi_text = ""
        for page_num in range(min(2, len(doc))):
            doi_text += doc[page_num].get_text("text")
        doc.close()
        doi_match = re.search(r"\b10\.\d{4,9}/[-._;()/:A-Z0-9]+\b", doi_text, re.I)
        return doi_match.group(0) if doi_match else None
    except Exception as e:
        print(f"[ERROR] PDF DOI parse error: {e}")
        return None


def parse_pdf_structurally_for_title(pdf_path: str) -> Optional[str]:
    """
    Uses docling to analyze document structure and find the most likely title.
    This version is smarter and avoids grabbing non-title text.
    """
    try:
        print("[INFO] Performing structural analysis for title...")
        doc_converter = DocumentConverter()
        conv_result = doc_converter.convert(pdf_path)
        doc = conv_result.document

        if not doc.texts or not doc.pages:
            return None

        first_page = doc.pages[0]
        page_height = first_page.height

        first_page_texts = [t for t in doc.texts if getattr(t, 'page', 0) == 0]
        if not first_page_texts:
            return None

        max_font_size = 0
        for text_obj in first_page_texts:
            font_size = getattr(text_obj, 'font_size', 0)
            if font_size > max_font_size:
                max_font_size = font_size

        if max_font_size == 0:
            return None

        title_candidates = []
        for text_obj in first_page_texts:
            if abs(getattr(text_obj, 'font_size', 0) - max_font_size) < 0.1:
                title_candidates.append(text_obj)

        filtered_title_parts = []
        ignore_keywords = ['university', 'institute', 'department', 'college', 'thesis', 'dissertation', 'submitted by',
                           'roll no.', 'abstract', 'introduction', 'certificate']

        for text_obj in title_candidates:
            text_str = getattr(text_obj, 'text', '').strip().lower()
            y_coord = getattr(text_obj, 'bbox', [0, 0, 0, 0])[1]

            if y_coord > (page_height * 0.5):
                continue

            if any(keyword in text_str for keyword in ignore_keywords):
                continue

            filtered_title_parts.append(getattr(text_obj, 'text', '').strip())

        title = " ".join(filtered_title_parts)
        print(f"[DEBUG] Structurally parsed title: '{title}'")
        return title

    except Exception as e:
        print(f"⚠️ Structural title parse error: {e}")
        return None


async def extract_metadata(pdf_path: str = None) -> dict:
    """
    Extracts metadata for a given PDF using PDF parsing and CrossRef.
    """
    metadata = {}

    # Priority 1: Smart PDF Parsing
    if pdf_path:
        doi = parse_pdf_for_doi(pdf_path)
        if doi:
            metadata['DOI'] = doi

        try:
            reader = PdfReader(pdf_path)
            props = reader.metadata
            if getattr(props, 'title', None):
                metadata['title'] = props.title
            if getattr(props, 'author', None):
                metadata['authors'] = [props.author]
        except Exception:
            pass

        if not doi and not metadata.get('title'):
            title = parse_pdf_structurally_for_title(pdf_path)
            if title:
                metadata['title'] = title

    # Priority 3: CrossRef (use the best info found from the PDF)
    crossref_meta = await fetch_crossref_metadata(
        doi=metadata.get("DOI"),
        title=metadata.get("title")
    )

    if crossref_meta:
        print("[INFO] Successfully updated metadata from CrossRef.")
        metadata.update(crossref_meta)

    # Final cleanup to ensure consistent structure
    final_keys = ["title", "authors", "DOI", "journal", "publisher", "year", "item_type"]
    for key in final_keys:
        if key not in metadata:
            metadata[key] = [] if key == "authors" else ""

    metadata["pdf_path"] = pdf_path or ""
    return metadata
