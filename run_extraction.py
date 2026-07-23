"""
Standalone script to trigger process_document_fully for a given PDF and run name.
Usage: python run_extraction.py <pdf_path> <run_name>
"""
import asyncio
import sys
import os
from pathlib import Path

sys.path.insert(0, os.path.dirname(__file__))

from document_processor import process_document_fully


async def main(pdf_path_str: str, run_name: str):
    pdf_path = Path(pdf_path_str)
    paper_stem = pdf_path.stem
    output_dir = Path("extracted_data") / paper_stem / run_name
    output_dir.mkdir(parents=True, exist_ok=True)
    print(f"Running extraction: {pdf_path.name}  →  {output_dir}")
    result = await process_document_fully(str(pdf_path), str(output_dir))
    print(f"Done. Result: {result}")


if __name__ == "__main__":
    if len(sys.argv) < 3:
        print("Usage: python run_extraction.py <pdf_path> <run_name>")
        sys.exit(1)
    asyncio.run(main(sys.argv[1], sys.argv[2]))
