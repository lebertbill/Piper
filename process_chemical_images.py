import sys
import os
import logging
from pathlib import Path
from typing import List, Dict, Any
from dotenv import load_dotenv

load_dotenv()

_log = logging.getLogger(__name__)

def process_images_with_piper_intelligence(image_paths: List[Path]) -> Dict[str, Any]:
    """
    Process a list of image paths using Piper Intelligence Engine to extract chemical data.
    
    Args:
        image_paths: List of paths to the images to process.
        
    Returns:
        A dictionary mapping image filenames to their extracted data.
    """
    results = {}
    
    # Import Piper
    try:
        from miner.orchestrator import run_catalyst_miner
        
        _log.info("🦅 Piper loaded successfully.")
        
        print(f"🚀 Starting Miner processing for {len(image_paths)} images...")
    
        results = []
        for img_path in image_paths:
            print(f"  Processing: {img_path.name}...")
            if not img_path.exists():
                print(f"  ⚠️ Warning: Image file not found: {img_path.name}")
                results.append({"file": img_path.name, "error": "File not found"})
                continue
            try:
                # Call the new Piper
                result = run_catalyst_miner(str(img_path))
                results.append(result)
                print(f"  ✅ Success: {img_path.name}")
            except Exception as e:
                print(f"  ❌ Failed: {img_path.name} - {e}")
                results.append({"file": img_path.name, "error": str(e)})
                
        print("🏁 Miner processing complete.")
        # The original function returned a dict mapping filename to data.
        # The new code returns a list of results. Let's convert it to match the original signature.
        # Assuming each result in the list has a 'file' key.
        dict_results = {item.get('file', f"unknown_file_{i}"): item for i, item in enumerate(results)}
        return dict_results
                
    except ImportError as e:
        _log.error(f"Failed to import Piper: {e}")
        # If Piper can't be imported, return an empty dict or an error for all images
        return {path.name: {"error": f"Failed to load Miner: {e}"} for path in image_paths}
    except Exception as e:
        _log.error(f"An unexpected error occurred while loading Piper: {e}")
        return {path.name: {"error": f"Unexpected error loading Miner: {e}"} for path in image_paths}

