import asyncio
import os
import json
from docling.document_converter import DocumentConverter, PdfFormatOption
from docling.datamodel.base_models import InputFormat
from docling.datamodel.pipeline_options import PdfPipelineOptions
from pathlib import Path
from concurrent.futures import ProcessPoolExecutor, as_completed
from typing import Tuple, List
from .pdf2chunks import read_doc, ensure_ocr_dependencies, Chunk  # Import the Chunk class
from .pdf_to_single_chunk import read_doc_as_single_chunk
from metadata import extract_metadata
from document_processor import process_document_fully  # Import the new full document processor
from .embeddings import EmbeddingRetriever


def get_all_pdfs(folder_path):
    return [os.path.join(root, file) for root, _, files in os.walk(folder_path) for file in files if file.lower().endswith(".pdf")]


def load_json_log(file_path):
    if os.path.exists(file_path):
        with open(file_path, "r") as f:
            try:
                return json.load(f)
            except json.JSONDecodeError:
                return {}
    return {}


def save_json_log(data, file_path):
    with open(file_path, "w") as f:
        json.dump(data, f, indent=4)


def process_single_file(pdf_path: str, chunk_params: Tuple[int, int, int], metadata: dict, chunk_by_document: bool, query_responder: bool) -> list:
    """Worker process: Parses and chunks a single PDF, returning the chunks."""
    print(f"\U0001F4E5 Starting processing for: {os.path.basename(pdf_path)}")
    
    # --- This is the core change: decide which chunking function to use ---
    if chunk_by_document:
        if not query_responder:
            # Run the agent-based analysis workflow and do NOT return chunks for indexing.
            print("ℹ️ Query responder disabled. Running agent-based analysis...")
            output_dir = Path(pdf_path).parent / f"{Path(pdf_path).stem}_assets"
            asyncio.run(process_document_fully(pdf_path, str(output_dir)))
            # Return an empty list so nothing is added to the query database.
            return []
        else:
            # Run the simple full-document chunking for querying.
            print("ℹ️ Query responder enabled. Creating a single chunk for the document...")
            pipeline_options = PdfPipelineOptions(do_ocr=True, do_table_structure=True)
            pdf_format_option = PdfFormatOption(pipeline_options=pipeline_options)
            doc_converter = DocumentConverter(format_options={InputFormat.PDF: pdf_format_option})
            conv_result = doc_converter.convert(pdf_path)
            doc = conv_result.document
            chunks = asyncio.run(read_doc_as_single_chunk(doc, pdf_path, metadata))
    else:
        # Use the original fine-grained chunking function
        chunks = asyncio.run(read_doc(pdf_path, chunk_params, metadata))

    if chunks:
        return chunks
    raise ValueError("Document conversion resulted in no chunks to process.")


def run_ingestion(folder_path: str, embedding_model_name: str,
                  chunk_params: Tuple[int, int, int], db_path: str, processed_log_path: str, failed_log_path: str,
                  parallel: bool = True, chunk_by_document: bool = False, query_responder: bool = True):
    print(f"\U0001F4C2 Scanning folder: {folder_path}")
    unique_pdf_files = sorted(list(set(get_all_pdfs(folder_path))))
    print(f"✅ Found {len(unique_pdf_files)} unique PDF files.")

    processed_files = load_json_log(processed_log_path)
    failed_files = load_json_log(failed_log_path)
    
    # --- NEW: Track processed content hashes to avoid duplicates ---
    processed_hashes = {record['hash'] for record in processed_files.values() if isinstance(record, dict) and 'hash' in record}
    new_files: List[str] = []
    
    db_exists = Path(db_path).exists()

    for pdf_path in unique_pdf_files:
        # if pdf_path in failed_files:
        #     continue

        mod_time = os.path.getmtime(pdf_path)
        # Check if file is new or modified
        if pdf_path not in processed_files or processed_files[pdf_path].get('mtime') != mod_time or not db_exists:
            # Before adding, check if we've already processed this content from a different path
            content_hash = read_doc.get_file_hash(pdf_path)
            if content_hash and content_hash not in processed_hashes:
                new_files.append(pdf_path)
                processed_hashes.add(content_hash) # Add to set to handle duplicates in the same run

    if not new_files:
        print("✅ No new files to process. Index is up to date.")
        return

    print(f"\U0001F4C4 Found {len(new_files)} new/updated PDFs to process.")
    ensure_ocr_dependencies(["en"])

    print("📚 Pre-fetching metadata for new files...")
    metadata_map = {pdf_path: asyncio.run(extract_metadata(pdf_path)) for pdf_path in new_files}

    all_new_chunks = []
    successful_files = []

    def process_and_collect_results(executor, files_to_process):
        future_to_path = {executor.submit(process_single_file, path, chunk_params, metadata_map.get(path, {}), chunk_by_document, query_responder): path for path in files_to_process}
        for future in as_completed(future_to_path):
            pdf_path = future_to_path[future]
            try:
                chunks = future.result()
                # Only mark as successful if chunks are returned.
                # The agent-only workflow returns an empty list.
                if chunks:
                    all_new_chunks.extend(chunks)
                    # Store both modification time and content hash
                    processed_files[pdf_path] = {'mtime': os.path.getmtime(pdf_path), 'hash': chunks[0].metadata.get('content_hash')}
                    print(f"✅ Successfully processed: {os.path.basename(pdf_path)}")
                else:
                    print(f"✅ Agent analysis complete for: {os.path.basename(pdf_path)}")
                
                successful_files.append(pdf_path)
                # Save log immediately after any success (chunking or agent analysis)
                save_json_log(processed_files, processed_log_path)
            except Exception as exc:
                print(f"🚨 A worker for '{os.path.basename(pdf_path)}' failed catastrophically: {exc}")
                failed_files[pdf_path] = str(exc)
                save_json_log(failed_files, failed_log_path)

    if parallel:
        print("🚀 Starting multi-core processing of PDFs...")
        with ProcessPoolExecutor(max_workers=os.cpu_count()) as executor:
            process_and_collect_results(executor, new_files)
    else:
        print("🚀 Starting serial processing of PDFs...")
        # For serial execution, we process files one by one directly. For streamlit, serial is forced.
       
        for path in new_files:
            try:
                chunks = process_single_file(path, chunk_params, metadata_map.get(path, {}), chunk_by_document, query_responder)
                if chunks:
                    all_new_chunks.extend(chunks)
                    processed_files[path] = {'mtime': os.path.getmtime(path), 'hash': chunks[0].metadata.get('content_hash')}
                    print(f"✅ Successfully processed: {os.path.basename(path)}")
                else:
                    print(f"✅ Agent analysis complete for: {os.path.basename(path)}")
                
                successful_files.append(path)
                save_json_log(processed_files, processed_log_path)
            except Exception as exc:
                print(f"🚨 Processing for '{os.path.basename(path)}' failed: {exc}")
                failed_files[path] = str(exc)
                save_json_log(failed_files, failed_log_path)

    if all_new_chunks:
        print(f"\n📚 Collected {len(all_new_chunks)} new chunks. Upserting to database in a single batch...")
        retriever = EmbeddingRetriever(db_path=db_path, model_name=embedding_model_name)
        retriever.create_or_load_index(all_new_chunks)

    print(f"\n✅ Summary: Successfully processed and stored {len(successful_files)} out of {len(new_files)} new files.")