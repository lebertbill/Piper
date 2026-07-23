# ! Older RAG query responder which was built even before the extraction agents ! Safe to discard
import asyncio
import os
import json
import shutil
import warnings
import argparse
from dotenv import load_dotenv
from rag.embeddings import EmbeddingRetriever
from context import load_config, get_parameters, get_data_paths, CONTEXT_FILE
from rag.query_router import classify_query, extract_entities
from rag.retriever_logic import (
    handle_general_query,
    handle_list_query,
    handle_filtered_query,
    handle_structured_extraction_query,
)
from rag.ingestion import run_ingestion

warnings.filterwarnings("ignore", category=UserWarning, message=".*'pin_memory' argument is set as true.*")
load_dotenv()


async def run_query(question: str, db_path: str, model_name: str):
    """
    Handles the asynchronous query logic.
    """
    retriever = EmbeddingRetriever(db_path=db_path, model_name=model_name)

    print("\n" + "=" * 50)
    print(f"Processing query: {question}")
    print("=" * 50 + "\n")

    query_type = await classify_query(question)

    print(f"🔍 Query classified as: {query_type}")

    if query_type in ['list_query', 'filtered_query']:
        entities = await extract_entities(question)
        if query_type == 'list_query':
            response = await handle_list_query(retriever, entities)
        else: # filtered_query
            response = await handle_filtered_query(retriever, question, entities)
    elif query_type == 'structured_extraction_query':
        response = await handle_structured_extraction_query(retriever, question)
    else:
        response = await handle_general_query(retriever, question)

    print("\n" + "="*25 + " Final Answer " + "="*25 + "\n" + response)



def clear_data_store(db_path, processed_log_path, failed_log_path, results_db_path, results_excel_path):
    """Deletes the ChromaDB database and associated log files."""
    print("🔥 Force re-ingest flag is set. Clearing data store...")
    if os.path.exists(db_path):
        shutil.rmtree(db_path)
        print(f"  - Deleted database: {db_path}")
    if os.path.exists(results_excel_path):
        os.remove(results_excel_path)
        print(f"  - Deleted results Excel file: {results_excel_path}")
    if os.path.exists(processed_log_path):
        os.remove(processed_log_path)
        print(f"  - Deleted processed log: {processed_log_path}")
    if os.path.exists(failed_log_path):
        os.remove(failed_log_path)
        print(f"  - Deleted failed log: {failed_log_path}")
    print("✅ Data store cleared.")

def main():
    parser = argparse.ArgumentParser(description="Piper: A local RAG-based research assistant.")
    parser.add_argument("--serial", action="store_true", help="Run ingestion in serial mode (slower, but less memory).")
    parser.add_argument("--query", type=str, help="Run with a specific query instead of the one in context.json.")
    # Add arguments to explicitly enable or disable the query responder, overriding context.json
    parser.add_argument('--enable-query-responder', dest='query_responder_override', action='store_true', help="Force enable query answering when chunking by document.")
    parser.add_argument('--disable-query-responder', dest='query_responder_override', action='store_false', help="Force disable query answering to run agent-only analysis.")
    parser.set_defaults(query_responder_override=None)
    args = parser.parse_args()

    config = load_config()
    folder_path = config["folder_path"]

    # !Establish the authoritative model name here. All other parts of the app will use this. You can set default model here as well!
    embedding_model_name = config.get("embedding", {}).get("embedding_model", "BAAI/bge-large-en-v1.5")
    question = args.query if args.query else config["question"]
    chunk_by_document = config.get("chunk_by_document", False)

    # Load query_responder from context.json, then check for CLI override.
    query_responder = config.get("query_responder", True)
    if args.query_responder_override is not None:
        query_responder = args.query_responder_override
        print(f"ℹ️ CLI override: Query responder set to {query_responder}.")

    # If not chunking by document, query responder is always effectively on.
    if not chunk_by_document:
        query_responder = True
    elif not query_responder:
        print("ℹ️ Query responder is disabled. Agent analysis will run, but the query will be skipped.")
        print("ℹ️ Forcing serial execution for agent analysis to avoid multiprocessing issues.")
        args.serial = True

    db_path, processed_log_path, failed_log_path, results_db_path, results_excel_path = get_data_paths(folder_path)
    chunk_size, chunk_overlap, max_summary_words, _ = get_parameters()

    # Check for the force_reingest flag
    if config.get("force_reingest", False):
        clear_data_store(db_path, processed_log_path, failed_log_path, results_db_path, results_excel_path)
        # Reset the flag in the config file to prevent re-running
        config["force_reingest"] = False
        with open(CONTEXT_FILE, 'w') as f:
            json.dump(config, f, indent=4)

    run_ingestion(
        folder_path=folder_path,
        embedding_model_name=embedding_model_name,
        chunk_params=(chunk_size, chunk_overlap, max_summary_words),
        db_path=db_path,
        processed_log_path=processed_log_path,
        failed_log_path=failed_log_path,
        parallel=not args.serial,
        chunk_by_document=chunk_by_document,
        query_responder=query_responder
    )

    # Only run the query if the query responder is enabled
    if query_responder:
        asyncio.run(run_query(question, db_path, embedding_model_name))


if __name__ == "__main__":
    main()
