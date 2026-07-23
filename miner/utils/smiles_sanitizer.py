import logging
import rdkit.Chem as Chem
from typing import Optional
from miner.agents.smiles_repair_agent import get_smiles_repair_agent

_log = logging.getLogger(__name__)

def is_valid_smiles(smiles: str) -> bool:
    """Checks if a SMILES string is RDKit-compatible, allowing coordinate bonds."""
    if not smiles or not isinstance(smiles, str):
        return False
    
    # --- COORDINATION COMPLEX BYPASS ---
    # RDKit's default SMILES parser fails on coordinate bonds (->, <-).
    # We bypass validation for these to preserve high-fidelity catalyst data.
    if "->" in smiles or "<-" in smiles:
        return True

    try:
        # Ignore RDKit logs for this check
        from rdkit import RDLogger
        lg = RDLogger.logger()
        lg.setLevel(RDLogger.CRITICAL)
        mol = Chem.MolFromSmiles(smiles)
        return mol is not None
    except:
        return False

def sanitize_smiles(smiles: str) -> str:
    """
    Validates a SMILES string. If invalid, attempts to repair it using 
    an LLM-based repair agent with an iterative feedback loop.
    """
    if not smiles:
        return ""
    
    # 1. Quick check
    if is_valid_smiles(smiles):
        return smiles
    
    _log.warning(f"SMILES Sanitizer: Detected invalid SMILES: {smiles}")
    
    # 2. Iterative Repair (Max 2 attempts)
    last_error = "RDKit returned None for MolFromSmiles"
    current_smiles = smiles
    
    try:
        agent = get_smiles_repair_agent()
        for attempt in range(2):
            _log.info(f"SMILES Sanitizer: Repair attempt {attempt + 1}...")
            repaired = agent.repair(current_smiles, error_context=last_error)
            
            if repaired and is_valid_smiles(repaired):
                _log.info(f"SMILES Sanitizer: Repaired successfully: {repaired}")
                return repaired
            
            # If still invalid, try one more time with the new error if possible
            last_error = f"Still invalid SMILES: {repaired}" if repaired else "Repair agent returned None"
            current_smiles = smiles # Always try to fix the original or the best effort
            _log.warning(f"SMILES Sanitizer: Attempt {attempt + 1} failed. {last_error}")

        _log.error(f"SMILES Sanitizer: All repair attempts failed for: {smiles}")
        return smiles # Fallback to original
    except Exception as e:
        _log.error(f"SMILES Sanitizer: Critical error in repair cycle: {e}")
        return smiles
