import streamlit as st
import asyncio
import os
import hashlib
import json
import sqlite3
import shutil
import sys
import re
from contextlib import contextmanager
from io import StringIO
from document_processor import process_document_fully
import tempfile

from pathlib import Path
from rdkit import Chem
from rdkit.Chem import Draw
from rdkit.Chem import rdChemReactions
from rdkit.Chem.Draw import rdMolDraw2D
from PIL import Image

# Import all necessary functions from your backend scripts
from context import load_config, get_parameters, CONTEXT_FILE


# --- Asyncio Event Loop Management for Streamlit ---

def get_or_create_eventloop():
    try:
        return asyncio.get_running_loop()
    except RuntimeError:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        return loop


# --- Utility Functions ---

def mol_to_image(smiles):
    """Generates an RDKit image from a SMILES for display."""
    try:
        mol = Chem.MolFromSmiles(smiles)
        if mol:
            return Draw.MolToImage(mol, size=(400, 200))
    except Exception:
        pass
    return None

def reaction_to_image(rxn_smiles: str):
    """Renders a full reaction (reactants >> products) as PNG using RDKit.
    Falls back to a side-by-side molecule grid if reaction drawing fails.
    Always returns PNG bytes or a PIL Image which is supported by st.image()."""
    try:
        parts = rxn_smiles.split(">>")
        reactants_part = parts[0].strip() if len(parts) > 0 else ""
        products_part = parts[1].strip() if len(parts) > 1 else ""

        if not reactants_part and not products_part:
            return None
        if not reactants_part or not products_part:
            # One side missing - skip
            raise ValueError("incomplete reaction")

        rxn = rdChemReactions.ReactionFromSmarts(rxn_smiles, useSmiles=True)
        if rxn and rxn.GetNumReactantTemplates() > 0 and rxn.GetNumProductTemplates() > 0:
            rxn.Initialize()

            # Prefer PNG drawer (MolDraw2dcairo)
            try:
                drawer = rdMolDraw2D.MolDraw2DCairo(900, 250)
                drawer.DrawReaction(rxn)
                drawer.FinishDrawing()
                png_bytes = drawer.GetDrawingText()
                if png_bytes:
                    return png_bytes
            except Exception:
                pass

            # Second attempt: SVG → cairosvg → PNG bytes
            try:
                drawer = rdMolDraw2D.MolDraw2DSVG(900, 250)
                drawer.DrawReaction(rxn)
                drawer.FinishDrawing()
                svg = drawer.GetDrawingText()
                import cairosvg
                png_bytes = cairosvg.svg2png(bytestring=svg.encode())
                if png_bytes:
                    return png_bytes
            except Exception:
                pass
    except Exception:
        pass

    # Fallback: draw all molecules in a grid (works without cairo)
    try:
        parts = rxn_smiles.split(">>")
        all_smiles = [s for part in parts for s in part.split(".") if s]
        mols = [Chem.MolFromSmiles(s) for s in all_smiles if Chem.MolFromSmiles(s)]
        if mols:
            r_count = len(parts[0].split(".")) if parts else 0
            labels = ["Reactant" if i < r_count else "Product" for i in range(len(mols))]
            return Draw.MolsToGridImage(mols, molsPerRow=min(len(mols), 4),
                                        subImgSize=(250, 220), legends=labels)
    except Exception:
        pass
    return None

@contextmanager
def st_redirect(src, container):
    """A context manager to redirect stdout to a Streamlit container."""
    placeholder = container.empty()
    output_func = placeholder.code
    
    with StringIO() as buffer:
        old_write = src.write

        def new_write(b):
            # Defer to the old write to get the string (???)
            res = old_write(b)
            # Append to the buffer and update the Streamlit element
            buffer.write(b)
            output_func(buffer.getvalue())
            return res

        src.write = new_write
        yield
        src.write = old_write

def save_config(config_data):
    """Saves the configuration dictionary to context.json."""
    try:
        with open(CONTEXT_FILE, 'w') as f:
            json.dump(config_data, f, indent=4)
        return True
    except Exception as e:
        st.error(f"Failed to save configuration: {e}")
        return False

def get_data_paths(base_folder_path: str):
    """Wrapper so callers don't need to handle a missing path explicitly."""
    if not base_folder_path:
        return None, None, None, None, None
    from context import get_data_paths as _get_data_paths
    return _get_data_paths(base_folder_path)

def clear_data_store(db_path, processed_log_path, failed_log_path):
    """Deletes the ChromaDB database and associated log files."""
    st.warning("🔥 Force re-ingest flag is set. Clearing data store...")
    if os.path.exists(db_path):
        shutil.rmtree(db_path)
        st.write(f"  - Deleted database: {db_path}")
    if os.path.exists(processed_log_path):
        os.remove(processed_log_path)
        st.write(f"  - Deleted processed log: {processed_log_path}")
    if os.path.exists(failed_log_path):
        os.remove(failed_log_path)
        st.write(f"  - Deleted failed log: {failed_log_path}")
    st.success("✅ Data store cleared.")


# --- Page Configuration ---
st.set_page_config(
    page_title="Piper Extractor",
    page_icon=Image.open("ico/ico.png"),
    layout="wide"
)

import extra_streamlit_components as stx
import datetime

# --- Security / Authentication ---
def check_password():
    """Returns `True` if the user had the correct password."""
    
    # Initialize Cookie Manager (Singleton-ish via key)
    cookie_manager = stx.CookieManager(key="piper_auth_manager")
    
    # 1. Check Cookie first (Persistence)
    auth_cookie = cookie_manager.get(cookie="piper_auth_token")
    username_cookie = cookie_manager.get(cookie="piper_username")

    if auth_cookie == "valid_token" and username_cookie:
        st.session_state['authenticated'] = True
        st.session_state['username'] = username_cookie
        return True

    # 2. Check Session State
    if st.session_state.get('authenticated', False):
        return True

    # 3. Show Login Form
    st.title("Piper Security")
    st.write("Please verify your credentials to access the local deployment.")
    
    col1, col2 = st.columns(2)
    with col1:
        username = st.text_input("Username", help="Enter your name for tracking purposes.")
    with col2:
        password = st.text_input("Password", type="password")
        
    remember_me = st.checkbox("Remember Me (30 days)")

    if st.button("Login"):
        if password == "piper_secure_2025" and username: # Default password
            st.session_state['authenticated'] = True
            st.session_state['username'] = username
            
            if remember_me:
                # Set cookie with explicit expiration (30 days)
                expires = datetime.datetime.now() + datetime.timedelta(days=30)
                cookie_manager.set("piper_auth_token", "valid_token", expires_at=expires, key="set_auth")
                cookie_manager.set("piper_username", username, expires_at=expires, key="set_user")
                import time
                time.sleep(1) # Give the frontend time to set the cookie before reload
                
            st.success(f"Access Granted! Welcome, {username}.")
            st.rerun()
            return True
        elif not username:
            st.error("Please enter a username.")
            return False
        else:
            st.error("😕 Incorrect password")
            return False
            
    return False

# --- Enforce Security ---
if not check_password():
    st.stop()


# --- App Title ---
import base64 as _b64
with open("ico/ico.png", "rb") as _f:
    _ico = _b64.b64encode(_f.read()).decode()
st.markdown(
    f'<div style="display:flex;align-items:center;gap:14px;margin-bottom:8px">'
    f'<img src="data:image/png;base64,{_ico}" width="48"/>'
    f'<span style="font-size:2rem;font-weight:700;color:#1f2937">Agentic Data Extraction</span>'
    f'</div>',
    unsafe_allow_html=True
)

# --- Session State Initialization ---
if 'config' not in st.session_state:
    st.session_state.config = load_config()

# --- Sidebar for Configuration and Ingestion ---
with st.sidebar:
    st.header("Configuration")

    # Use expanders to organize the settings
    with st.expander("Models"):
        embedding_config = st.session_state.config.get('embedding', {})
        embedding_config['embedding_model'] = st.text_input(
            "Embedding Model",
            value=embedding_config.get('embedding_model', 'BAAI/bge-large-en-v1.5')
        )
        st.session_state.config['embedding'] = embedding_config

        model_config = st.session_state.config.get('model', {})
        model_config['rag_model_name'] = st.text_input(
            "Language Model (LLM)",
            value=model_config.get('rag_model_name', 'openai/gpt-4o')
        )
        
        st.markdown("### Agent Models")
        
        agents_config = model_config.get('agents', {})
        agents_config['reaction_model'] = st.text_input("Reaction Model", value=agents_config.get('reaction_model', 'openai/gpt-5-mini'))
        agents_config['text_reaction_model'] = st.text_input("Text Reaction Model", value=agents_config.get('text_reaction_model', 'openai/gpt-5-mini'))
        agents_config['r_group_model'] = st.text_input("R-Group Model", value=agents_config.get('r_group_model', 'openai/gpt-5-mini'))
        agents_config['molecular_model'] = st.text_input("Molecular Model", value=agents_config.get('molecular_model', 'openai/gpt-5-mini'))
        agents_config['structure_parser_model'] = st.text_input("Structure Parser Model", value=agents_config.get('structure_parser_model', 'google/gemini-3-flash-p'))
        agents_config['classifier_model'] = st.text_input("Classifier Model", value=agents_config.get('classifier_model', 'openai/gpt-5-mini'))
    #    agents_config['smiles_extractor_model'] = st.text_input("SMILES Extractor Model", value=agents_config.get('smiles_extractor_model', 'openai/gpt-5-mini'))
        agents_config['label_resolver_model'] = st.text_input("Label Resolver Model", value=agents_config.get('label_resolver_model', 'openai/gpt-5-mini'))
        agents_config['advanced_structure_model'] = st.text_input("Advanced Structure Model", value=agents_config.get('advanced_structure_model', 'google/gemini-3-flash-preview'))
        agents_config['reaction_table_detector_model'] = st.text_input("Reaction Table Detector Model", value=agents_config.get('reaction_table_detector_model', 'openai/gpt-5-mini'))
        agents_config['fixed_condition_extractor_model'] = st.text_input("Fixed Condition Extractor Model", value=agents_config.get('fixed_condition_extractor_model', 'openai/gpt-5-mini'))
        agents_config['smiles_repair_model'] = st.text_input("SMILES Repair Model", value=agents_config.get('smiles_repair_model', 'openai/gpt-5-mini'))

        st.divider()
        vh_enabled = st.checkbox(
            "🔍 Enable Visualheist Fallback",
            value=st.session_state.config.get('enable_visualheist_fallback', True),
            help="When table text extraction fails (Docling can't parse hybrid image+text tables), "
                 "use TF-ID/visualheist to crop the full table directly from the PDF as an image. "
                 "Requires isolated venv setup on first use (~2 min)."
        )
        st.session_state.config['enable_visualheist_fallback'] = vh_enabled

        st.session_state.config['force_enable_visualheist_fallback'] = st.checkbox(
            "⚡ Force Visualheist for Label Resolution",
            value=st.session_state.config.get('force_enable_visualheist_fallback', False),
            disabled=not vh_enabled,
            help="When ON, label resolution always uses the VisualHeist-cropped image instead of the "
                 "Docling artifact. Only effective when Enable Visualheist Fallback is also ON "
                 "(requires visualheist images to have been produced during extraction)."
        )

        st.session_state.config['enable_scope_table_processing'] = st.checkbox(
            "🔬 Process Substrate Scope Tables",
            value=st.session_state.config.get('enable_scope_table_processing', True),
            help="When ON (default): substrate scope tables (is_scope_table=true) are processed "
                 "alongside optimization tables — each row's substrate and yield are extracted. "
                 "Turn OFF to skip scope tables and only process condition optimization tables."
        )

        st.session_state.config['build_global_labels'] = st.checkbox(
            "🏷️ Build Global Label Registry (all figures)",
            value=st.session_state.config.get('build_global_labels', False),
            help="When OFF (default): only resolve labels from figures explicitly referenced by "
                 "the optimization table's columns. Faster and avoids unnecessary LLM calls on "
                 "substrate scope figures. When ON: resolve every labelled figure in the document — "
                 "useful when condition text references labels defined in unlinked figures."
        )

        model_config['agents'] = agents_config
        st.session_state.config['model'] = model_config

    with st.expander("Processing Parameters"):
        chunking_config = st.session_state.config.get('chunking', {})
        chunking_config['chunk_words'] = st.number_input("Chunk Size (words)", value=chunking_config.get('chunk_words', 800), min_value=50)
        chunking_config['chunk_overlap_words'] = st.number_input("Chunk Overlap (words)", value=chunking_config.get('chunk_overlap_words', 80), min_value=0)
        chunking_config['max_summary_words'] = st.number_input("Max Summary (words)", value=chunking_config.get('max_summary_words', 250), min_value=20)
        st.session_state.config['chunking'] = chunking_config

        retrieval_config = st.session_state.config.get('retrieval', {})
        retrieval_config['top_k_general'] = st.number_input("Top-K (General Query)", value=retrieval_config.get('top_k_general', 100), min_value=1)
        retrieval_config['top_k_filtered'] = st.number_input("Top-K (Filtered Query)", value=retrieval_config.get('top_k_filtered', 5), min_value=1)
        retrieval_config['top_k_structured'] = st.number_input("Top-K (Structured Query)", value=retrieval_config.get('top_k_structured', 20), min_value=1)
        st.session_state.config['retrieval'] = retrieval_config

    with st.expander("API & Services"):
        st.session_state.config['crossref'] = st.session_state.config.get('crossref', {})
        st.session_state.config['crossref']['email'] = st.text_input(
            "CrossRef Email",
            value=st.session_state.config.get('crossref', {}).get('email', ''),
            help="Email for CrossRef API's 'polite pool'."
        )

    if st.button("Save Configuration"):
        if save_config(st.session_state.config):
            st.success("Configuration saved to context.json!")

    st.divider()


# --- Main Interface ---

st.markdown("Upload a organic chemistry PDF article to extract structured data (reactants, products, and conditions).")

# --- Mode Selection: New vs Existing ---
app_mode = st.radio("Choose Action", ["Process New PDF", "View Processed Data"], horizontal=True)

if app_mode == "Process New PDF":
    col_file, col_run = st.columns([2, 1])
    with col_file:
        uploaded_files = st.file_uploader(
            "Upload one or more PDF articles",
            type=["pdf"],
            accept_multiple_files=True,
            help="You can select multiple PDFs — they will be processed one at a time."
        )
        si_uploaded_files = st.file_uploader(
            "Upload Supplementary Information (Optional)",
            type=["pdf", "docx"],
            accept_multiple_files=True,
            help="Optional. SI filenames must share the same prefix as their main article (e.g. 'Abdiaj.pdf' -> 'Abdiaj_SI.docx')."
        )
    with col_run:
        run_name_input = st.text_input(
            "Run Name (Optional)",
            value="default_run",
            help="A label for this extraction run (e.g. 'GPT4o_Test'). Applied to all uploaded files."
        )
        st.session_state['run_name_input'] = run_name_input

    if uploaded_files:
        st.markdown(f"**{len(uploaded_files)} file(s) queued:**")

        # Initialize batch results in session state (reset each time files change)
        if (
            'batch_results' not in st.session_state
            or len(st.session_state['batch_results']) != len(uploaded_files)
            or [r['name'] for r in st.session_state['batch_results']] != [f.name for f in uploaded_files]
        ):
            st.session_state['batch_results'] = [
                {"name": f.name, "status": "⏳ Queued", "error": None}
                for f in uploaded_files
            ]

        # Display a live-updating queue table
        queue_table = st.empty()

        def render_queue():
            rows = "\n".join(
                f"| {row['name']} | {row['status']} |"
                for row in st.session_state['batch_results']
            )
            queue_table.markdown(
                f"| File | Status |\n|---|---|\n{rows}"
            )

        render_queue()

        if st.button("▶ Start Batch Extraction"):
            base_output_dir = Path("extracted_data")
            base_output_dir.mkdir(exist_ok=True)

            run_name = st.session_state.get('run_name_input', 'default_run')
            sanitized_run_name = "".join([c if c.isalnum() else "_" for c in run_name])

            loop = get_or_create_eventloop()

            log_container = st.expander("📋 Processing Log", expanded=True)

            for idx, uploaded_file in enumerate(uploaded_files):
                # Update status → Processing
                st.session_state['batch_results'][idx]['status'] = "⚙️ Processing..."
                render_queue()

                pdf_path = base_output_dir / uploaded_file.name

                try:
                    # Save PDF to disk
                    with open(pdf_path, "wb") as f:
                        f.write(uploaded_file.getbuffer())

                    file_output_dir = base_output_dir / pdf_path.stem / sanitized_run_name
                    file_output_dir.mkdir(parents=True, exist_ok=True)

                    with log_container:
                        st.markdown(f"---\n**Processing: `{uploaded_file.name}`**")

                    # ---- START SI PROCESSING ----
                    matching_si = None
                    if si_uploaded_files:
                        if len(uploaded_files) == 1 and len(si_uploaded_files) == 1:
                            # Direct pair
                            matching_si = si_uploaded_files[0]
                        else:
                            # Fuzzy prefix match (e.g., matching "Abdiaj" in "Abdiaj - Main.pdf" and "Abdiaj_SI.docx")
                            main_first_word = uploaded_file.name.split(' ')[0].split('_')[0].split('-')[0]
                            for si_f in si_uploaded_files:
                                if si_f.name.startswith(main_first_word):
                                    matching_si = si_f
                                    break
                                
                    if matching_si:
                        with log_container:
                            st.markdown(f"  -> **Found matching SI:** `{matching_si.name}`")
                        
                        # Ensure processed directory is ready for the Advanced Structure Agent
                        si_processed_dir = file_output_dir / "processed"
                        si_processed_dir.mkdir(parents=True, exist_ok=True)
                        
                        si_temp_path = si_processed_dir / matching_si.name
                        with open(si_temp_path, "wb") as f:
                            f.write(matching_si.getbuffer())
                            
                        with log_container:
                            st.write(f"  -> Saved SI file for deep processing: {si_temp_path.name}")
                    # ---- END SI PROCESSING ----

                    def make_callback(container, file_name):
                        def update_status(msg):
                            with container:
                                st.write(f"[{file_name}] {msg}")
                        return update_status

                    progress_cb = make_callback(log_container, uploaded_file.name)

                    loop.run_until_complete(
                        process_document_fully(
                            str(pdf_path),
                            str(file_output_dir),
                            progress_callback=progress_cb,
                            si_path_str=str(si_temp_path) if matching_si else None
                        )
                    )

                    # Save metadata
                    try:
                        meta_data = {
                            "processed_by": st.session_state.get("username", "Unknown"),
                            "timestamp": str(datetime.datetime.now()),
                            "run_name": run_name,
                            "status": "completed"
                        }
                        with open(file_output_dir / "run_metadata.json", "w") as f:
                            json.dump(meta_data, f, indent=4)
                    except Exception:
                        pass

                    st.session_state['batch_results'][idx]['status'] = "✅ Done"

                    # Automatically set session for the LAST processed file for display
                    st.session_state['extraction_complete'] = True
                    st.session_state['current_pdf_stem'] = pdf_path.stem
                    st.session_state['base_output_dir'] = str(base_output_dir)
                    st.session_state['selected_run_subfolder'] = sanitized_run_name

                except Exception as e:
                    st.session_state['batch_results'][idx]['status'] = "❌ Failed"
                    st.session_state['batch_results'][idx]['error'] = str(e)
                    with log_container:
                        st.error(f"[{uploaded_file.name}] Failed: {e}")

                render_queue()

            # Final summary
            total = len(uploaded_files)
            done = sum(1 for r in st.session_state['batch_results'] if r['status'] == "✅ Done")
            failed = total - done
            if failed == 0:
                st.success(f"🎉 All {total} file(s) processed successfully!")
            else:
                st.warning(f"Completed: {done}/{total} succeeded, {failed} failed.")
                for r in st.session_state['batch_results']:
                    if r['error']:
                        st.error(f"**{r['name']}**: {r['error']}")

else: # View Processed Data
    base_output_dir = Path("extracted_data")
    if not base_output_dir.exists():
         st.warning("No 'extracted_data' folder found.")
    else:
        # 1. Select Paper (Source PDF)
        # List immediate subdirectories in extracted_data (these are the papers)
        paper_candidates = [f.name for f in base_output_dir.iterdir() if f.is_dir()]
        paper_candidates.sort()
        
        if not paper_candidates:
            st.info("No processed papers found in 'extracted_data'.")
        else:
            selected_paper = st.selectbox("Select Paper", paper_candidates)
            
            if selected_paper:
                paper_dir = base_output_dir / selected_paper
                
                # 2. Select Run (Legacy or specific Run Name)
                # We look for subdirectories that contain 'processed' or are themselves processed containers
                # Case A: Legacy - paper_dir / processed / extraction_results.json
                # Case B: Run - paper_dir / <Run_Name> / processed / extraction_results.json
                
                run_options = []
                
                # Check for Legacy (Standard) Run
                if (paper_dir / "processed" / "extraction_results.json").exists():
                    run_options.append("Default (Legacy)")
                    
                # Check for Named Runs
                for item in paper_dir.iterdir():
                    if item.is_dir() and item.name != "processed":
                        if (item / "processed" / "extraction_results.json").exists():
                            run_options.append(item.name)
                
                run_options.sort()
                
                if not run_options:
                    st.warning("No processed runs found for this paper.")
                else:
                    selected_run = st.selectbox("Select Extraction Run", run_options)
                    
                    if st.button("Load Data"):
                        st.session_state['extraction_complete'] = True
                        st.session_state['current_pdf_stem'] = selected_paper
                        st.session_state['base_output_dir'] = str(base_output_dir)
                        
                        # Store the specific run path/name for logic downstream
                        if selected_run == "Default (Legacy)":
                            st.session_state['selected_run_subfolder'] = None # Indicates direct access
                        else:
                            st.session_state['selected_run_subfolder'] = selected_run
                        
                        st.success(f"Loaded data for: {selected_paper} ({selected_run})")

# --- Display Logic (Independent of Source) ---
if st.session_state.get('extraction_complete'):
        try:
            base_output_dir = Path(st.session_state['base_output_dir'])
            doc_stem = st.session_state['current_pdf_stem']

            # Find the result JSON
            doc_stem = st.session_state['current_pdf_stem']
            run_subfolder = st.session_state.get('selected_run_subfolder')
            
            # Logic to find the correct result directory
            if run_subfolder:
                 # Named Run: base / doc / run / processed
                 result_dir = base_output_dir / doc_stem / run_subfolder / "processed"
            else:
                 # Legacy/Default: base / doc / processed
                 # OR check if we just processed a new file with 'default_run' without explicit selection
                 # If we just finished extraction (Process New PDF mode), we might know the exact path.
                 # But for viewing mode, run_subfolder logic holds.
                 
                 # FallCheck: If we just came from "Process New PDF", let's check if we used a run name
                 # Actually, "Process New PDF" sets extraction_complete but doesn't set selected_run_subfolder probably?
                 # Let's fix Process New PDF to set it too. Assume for now we handled it.
                 
                 result_dir = base_output_dir / doc_stem / "processed"
                 
                 # If that doesn't exist, maybe it was a new run named 'default_run'?
                 if not result_dir.exists():
                      if (base_output_dir / doc_stem / "default_run" / "processed").exists():
                           result_dir = base_output_dir / doc_stem / "default_run" / "processed"

            relevant_result = result_dir / "extraction_results.json"
            
            if not relevant_result.exists():
                 st.warning(f"Could not find results at: {relevant_result}")
            
            if relevant_result and relevant_result.exists():
                result_path = relevant_result
                # result_dir is set above
                        
                # Load Structure Parser Output
                # --- DISPLAY METADATA ---
                metadata_file = result_dir.parent / "run_metadata.json"
                if metadata_file.exists():
                    try:
                        with open(metadata_file, "r") as mf:
                            meta = json.load(mf)
                            processed_by = meta.get("processed_by", "Unknown")
                            timestamp = meta.get("timestamp", "Unknown Time")
                            st.info(f"👮 Processed by: **{processed_by}** on {timestamp}")
                    except Exception:
                        pass
                # ------------------------

                # --- TOKEN USAGE & COST ---
                token_usage_file = result_dir / "token_usage.json"
                if token_usage_file.exists():
                    try:
                        with open(token_usage_file, "r") as tf:
                            usage = json.load(tf)
                        
                        total_in = usage.get("total_input_tokens", 0)
                        total_out = usage.get("total_output_tokens", 0)
                        in_cost = usage.get("total_input_cost_usd", 0)
                        out_cost = usage.get("total_output_cost_usd", 0)
                        total_cost = usage.get("total_cost_usd", 0)
                        num_calls = usage.get("total_calls", 0)

                        st.markdown("#### 💰 API Cost Summary")
                        c1, c2, c3, c4 = st.columns(4)
                        c1.metric("Input Tokens", f"{total_in:,}", help=f"Cost: ${in_cost:.4f}")
                        c2.metric("Output Tokens", f"{total_out:,}", help=f"Cost: ${out_cost:.4f}")
                        c3.metric("Total Cost", f"${total_cost:.4f}")
                        c4.metric("API Calls", str(num_calls))

                        with st.expander("📊 Per-Agent Breakdown"):
                            agent_data = usage.get("per_agent", {})
                            if agent_data:
                                import pandas as pd
                                rows = []
                                for agent, info in agent_data.items():
                                    rows.append({
                                        "Agent": agent,
                                        "Calls": info["calls"],
                                        "Input Tokens": f"{info['input_tokens']:,}",
                                        "Output Tokens": f"{info['output_tokens']:,}",
                                        "Cost (USD)": f"${info['cost_usd']:.4f}",
                                    })
                                st.dataframe(pd.DataFrame(rows), width="stretch", hide_index=True)
                    except Exception as e:
                        st.caption(f"⚠️ Could not load token usage: {e}")
                # -------------------------

                # --- Article Classification ---
                classification_file = result_dir / "classification_result.json"
                if classification_file.exists():
                    try:
                        with open(classification_file, "r") as f:
                            class_data = json.load(f)
                        st.divider()
                        st.subheader("📑 Article Classification")
                        st.markdown(f"**Type:** {class_data.get('article_type', 'Unknown')} (Confidence: {class_data.get('confidence', 0):.2f})")
                        st.info(f"**Reasoning:** {class_data.get('reasoning', 'No reasoning provided.')}")
                    except Exception as e:
                        st.caption(f"⚠️ Could not load classification data: {e}")
                # -------------------------

                structure_output_path = result_dir / "structure_parser_output.json"
                structure_data = []
                if structure_output_path.exists():
                    try:
                        with open(structure_output_path, "r") as f:
                            structure_data = json.load(f)
                            
                            # Handle dict wrapper from new agent
                            if isinstance(structure_data, dict):
                                structure_data = structure_data.get("items", structure_data.get("data_items", []))
                            if not isinstance(structure_data, list):
                                structure_data = []
                                
                            # Sort by image index to ensure correct document order
                            def get_image_index(item):
                                if not isinstance(item, dict): return 999999
                                path = item.get("image_path") or ""
                                match = re.search(r"image_(\d+)", path)
                                return int(match.group(1)) if match else 999999
                            
                            structure_data.sort(key=get_image_index)
                    except Exception as e:
                        st.warning(f"Could not load structure parser output: {e}")

                # --- Structure Analysis Section ---
                st.divider()
                st.subheader("🔍 Article Structure")
                
                if structure_data:
                    if st.button("View Raw Structure JSON"):
                        st.json(structure_data)
                    
                    for item in structure_data:
                        with st.container(border=True):
                            col_img, col_info = st.columns([1, 2])
                            
                            with col_img:
                                img_rel_path = item.get("image_path")
                                heading = item.get("heading", "")
                                
                                # Fallback: If no image path provided, try to find one based on the heading
                                if not img_rel_path and heading:
                                    sanitized_heading = heading.replace(" ", "_").replace(".", "")
                                    try:
                                        # Try to match the first few words, e.g., "Scheme_1", "Table_1"
                                        parts = sanitized_heading.split("_")
                                        if len(parts) >= 2:
                                            prefix = f"{parts[0]}_{parts[1]}"
                                            candidates = list(result_dir.glob(f"{prefix}*.png"))
                                            if candidates:
                                                candidates.sort(key=lambda x: x.stat().st_size, reverse=True)
                                                img_rel_path = str(candidates[0])
                                    except Exception:
                                        pass

                                if img_rel_path:
                                    # Resolve path relative to the project root or absolute
                                    # The path in JSON is relative to project root usually
                                    img_path = Path(img_rel_path)
                                    if not img_path.exists():
                                        # Fallback: Search for the file in the processed directory (handles nested artifacts)
                                        filename = img_path.name
                                        found_files = list(result_dir.rglob(filename))
                                        if found_files:
                                            img_path = found_files[0]
                                    
                                    # Normalize check again if we just found/constructed it
                                    if not img_path.exists() and not img_path.is_absolute():
                                         # It might be a direct filename in result_dir if we found it via glob above
                                         # checking absolute path of result_dir + filename
                                         maybe_path = result_dir / img_path.name
                                         if maybe_path.exists():
                                             img_path = maybe_path

                                    if img_path.exists():
                                        st.image(str(img_path), width="stretch")
                                    else:
                                        st.warning(f"Image not found: {img_rel_path}")
                                else:
                                    st.info("No image associated.")
                            
                            with col_info:
                                st.markdown(f"**{item.get('heading', 'Unknown Item')}**")
                                st.caption(f"Type: {item.get('type')} | Reaction Info: {item.get('contains_reaction_info')} | Source: {item.get('section', 'Unknown')}")
                                st.write(item.get("description", ""))

                else:
                    st.info("No structure analysis data found.")

                st.divider()
                st.subheader("🧪 Extracted data")
                
                with open(result_path, "r") as f:
                    raw_result_data = json.load(f)
                
                # Normalize to list to support multi-table
                if isinstance(raw_result_data, dict):
                    all_results = [raw_result_data]
                elif isinstance(raw_result_data, list):
                    all_results = raw_result_data
                else:
                    all_results = []
                    
                if not all_results:
                    st.warning("No extraction results found in file.")
                
                # Iterate through each table result
                for idx, result_data in enumerate(all_results):
                    table_title = result_data.get("table_name", f"Table {idx + 1}")
                    st.markdown(f"#### {table_title}")
                    
                    # Layout: Left (Image/Table) | Right (Results)
                    col1, col2 = st.columns([1, 1])
                    
                    with col1:
                        st.markdown("##### 📄 Source Material")
                        image_path = result_data.get("image_path")
                        table_name = result_data.get("table_name", "")
                        if image_path and image_path != "None" and os.path.exists(image_path):
                            st.image(image_path, caption="Extracted Scheme/Table Image")
                        else:
                            st.warning("No image found for this extraction.")
                        
                        # Display Markdown Table
                        markdown_table = result_data.get("markdown_table")
                        
                        # Fallback: Check for text files in the directory if markdown_table is missing
                        if not markdown_table:
                            try:
                                 # Try to find specific text file for this table
                                 safe_name = "".join([c if c.isalnum() else "_" for c in table_name])
                                 # result_dir is a Path object
                                 text_files = list(result_dir.glob(f"*{safe_name}*text.txt"))
                                 
                                 # If precise match fails, try generic fallback if it's the only one, 
                                 # but with multi-table we should be careful.
                                 if not text_files and len(all_results) == 1:
                                     text_files = list(result_dir.glob("*text.txt"))
                                     
                                 if text_files:
                                     text_files.sort(key=lambda x: x.stat().st_size, reverse=True)
                                     target_text_file = text_files[0]
                                     with open(target_text_file, "r") as f:
                                         markdown_table = f.read()
                                     st.info(f"Loaded table content from: {target_text_file.name}")
                            except Exception as e:
                                # st.warning(f"Failed to load fallback text file: {e}")
                                pass

                        if markdown_table:
                            with st.expander("Show Markdown Table Content", expanded=False):
                                st.code(markdown_table, language="markdown")
                        else:
                            st.info("No table content extracted.")

                    with col2:
                        st.markdown("##### 🧪 Extracted Reactions")
                        
                        entries = result_data.get("entries", [])
                        if not entries:
                            st.warning("No entries found.")
                        
                        # Scrollable container for entries
                        with st.container(height=600):
                            for i, entry in enumerate(entries):
                                with st.container(border=True):
                                    entry_id = entry.get("Entry", entry.get("entry", f"Reaction {i+1}"))
                                    st.markdown(f"**Entry {entry_id}**")
                                    
                                    # Display Reactants/Products/Conditions
                                    reactants = entry.get("reactants", [])
                                    products = entry.get("products", [])
                                    conditions = entry.get("reaction_conditions", {}) # Fallback key? Piper uses 'conditions' usually
                                    if not conditions:
                                        conditions = entry.get("conditions", [])

                                    # Generate Reaction SMILES for display
                                    # Prefer smiles_canonical (validated), fall back to raw smiles
                                    def _best_smiles(p):
                                        return p.get("smiles_canonical") or (p.get("smiles") if p.get("smiles_valid") else None) or p.get("smiles") or ""
                                    reactant_smiles = [_best_smiles(r) for r in reactants if _best_smiles(r)]
                                    product_smiles  = [_best_smiles(p) for p in products  if _best_smiles(p)]

                                    reaction_smiles = f"{'.'.join(reactant_smiles)}>>{'.'.join(product_smiles)}"

                                    # Display full reaction image: Reactants >> Products
                                    if reactant_smiles or product_smiles:
                                        st.caption("Reaction SMILES:")
                                        st.code(reaction_smiles)

                                        try:
                                            rxn_img = reaction_to_image(reaction_smiles)
                                            if rxn_img is not None:
                                                st.image(rxn_img, caption="Full Reaction", use_container_width=True)
                                            else:
                                                # Render individual molecules when one side is missing
                                                all_mols = [(s, "Reactant") for s in reactant_smiles] + [(s, "Product") for s in product_smiles]
                                                mol_cols = st.columns(min(len(all_mols), 4))
                                                for idx, (smi, role) in enumerate(all_mols):
                                                    img = mol_to_image(smi)
                                                    if img:
                                                        mol_cols[idx % len(mol_cols)].image(img, caption=role)
                                        except Exception as e:
                                            st.warning(f"Could not render reaction image: {e}")

                                    # Display conditions as a clean table + molecule images
                                    if conditions:
                                        st.caption("Conditions:")
                                        cond_rows = []
                                        cond_with_smiles = []   # [(label, smiles, source, full_cond)]
                                        for c in conditions:
                                            text = str(c.get("text", "")).strip()
                                            resolved = c.get("resolved_name")
                                            resolved_smiles = c.get("resolved_smiles", "")
                                            smiles_source = c.get("smiles_source", "")

                                            display_text = text
                                            if resolved:
                                                import re
                                                parts = re.split(r"(\s*\(.*\))", text, maxsplit=1)
                                                if len(parts) > 1:
                                                    display_text = f"{resolved}{parts[1]}"
                                                else:
                                                    display_text = resolved

                                            row = {"Role": c.get("role", ""), "Text": display_text}
                                            if "quantity" in c:
                                                row["Quantity"] = c["quantity"]
                                                row["Unit"] = c.get("unit", "")
                                            if resolved_smiles:
                                                row["SMILES"] = resolved_smiles
                                                row["Source"] = {
                                                    "ocsr_coref":        "Tier 1 — Direct",
                                                    "ocsr_graph":        "Tier 2 — Symbol Correction",
                                                    "ocsr_graph_visual": "Tier 3 — BBox Match",
                                                    "ocsr_rgroup":       "Tier 4 — R-group Sub",
                                                    "pubchem_lookup":    "Tier 5 — PubChem",
                                                    "opsin_lookup":      "Tier 0 — OPSIN",
                                                    "llm_name_to_smiles":"Tier 0 — Name→SMILES",
                                                    "label_registry":    "Registry (OCSR)",
                                                    "llm_vision":        "Tier 6 — LLM Vision",
                                                }.get(smiles_source, smiles_source or "—")
                                                cond_with_smiles.append((
                                                    resolved or text,
                                                    resolved_smiles,
                                                    smiles_source,
                                                    c,
                                                ))
                                            cond_rows.append(row)

                                        st.dataframe(
                                            cond_rows,
                                            use_container_width=True,
                                            hide_index=True,
                                        )

                                        # Molecule grid for conditions with SMILES
                                        if cond_with_smiles:
                                            st.caption("Condition structures:")
                                            cols = st.columns(min(len(cond_with_smiles), 4))
                                            _src_badge = {
                                                "ocsr_coref":        "🔬 T1 Direct",
                                                "ocsr_graph":        "🔬 T2 Symbol",
                                                "ocsr_graph_visual": "🔬 T3 BBox",
                                                "ocsr_rgroup":       "🔄 T4 R-group",
                                                "pubchem_lookup":    "🔍 T5 PubChem",
                                                "opsin_lookup":      "📐 T0 OPSIN",
                                                "llm_name_to_smiles":"🤖 T0 Name→SMILES",
                                                "label_registry":    "🔬 Registry",
                                                "llm_vision":        "👁️ T6 LLM",
                                            }
                                            for idx, (lbl, smi, src, full_cond) in enumerate(cond_with_smiles):
                                                with cols[idx % 4]:
                                                    mol_img = mol_to_image(smi)
                                                    if mol_img:
                                                        st.image(mol_img, use_container_width=True)
                                                    else:
                                                        st.markdown("*(no structure)*")
                                                    st.caption(
                                                        f"**{lbl}**  \n"
                                                        f"`{smi[:40]}{'…' if len(smi)>40 else ''}`  \n"
                                                        f"{_src_badge.get(src, src or '—')}"
                                                    )
                                                    # Show alternative SMILES candidates when present
                                                    _alts = {
                                                        "PubChem":    full_cond.get("smiles_pubchem"),
                                                        "OPSIN":      full_cond.get("smiles_opsin"),
                                                        "LLM Name":   full_cond.get("smiles_llm"),
                                                        "OCSR (raw)": full_cond.get("smiles_ocsr_raw"),
                                                    }
                                                    _alts = {k: v for k, v in _alts.items() if v}
                                                    if _alts:
                                                        with st.expander("All SMILES candidates"):
                                                            for src_name, alt_smi in _alts.items():
                                                                is_chosen = (alt_smi == smi)
                                                                prefix = "✅ " if is_chosen else ""
                                                                st.markdown(
                                                                    f"**{prefix}{src_name}**  \n"
                                                                    f"`{alt_smi}`"
                                                                )
                                                                alt_img = mol_to_image(alt_smi)
                                                                if alt_img:
                                                                    st.image(alt_img, use_container_width=True)

                                    # Raw JSON for debugging
                                    with st.expander("Details (raw JSON)"):
                                        st.json(entry)
                    
                    st.divider() # Separator between tables
                        
        except Exception as e:
            st.error(f"Error displaying results: {e}")
