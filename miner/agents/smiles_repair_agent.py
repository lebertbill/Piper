import logging
from typing import Optional
from miner.agents.base_agent import BaseAgent

_log = logging.getLogger(__name__)

class SMILESRepairAgent(BaseAgent):
    """
    Agent responsible for taking an invalid or broken SMILES string 
    and repairing its syntax to be RDKit-compatible.
    """

    def __init__(self):
        from context import load_config
        config = load_config()
        # Use a high-quality model for repair tasks
        model = config.get("model", {}).get("agents", {}).get("smiles_repair_model", "openai/gpt-4o")
        super().__init__(model=model)

    def repair(self, broken_smiles: str, error_context: str = None) -> Optional[str]:
        """
        Repairs a broken SMILES string using specialized skills.
        """
        base_prompt = "You are a chemical informatics assistant that outputs JSON."
        user_content = f"INVALID SMILES TO REPAIR: {broken_smiles}"
        if error_context:
            user_content += f"\nERROR FROM RDKit: {error_context}"
            user_content += "\nACTION: If the syntax is too complex, simplify the molecular representation to favor RDKit-compatible connectivity over intricate 3D/bridged syntax."
        
        messages = self._assemble_prompt(
            base_prompt=base_prompt,
            user_content=user_content,
            skills=["smiles_correction.md"]
        )

        try:
            _log.info(f"SMILESRepairAgent: Repairing SMILES: {broken_smiles}...")
            response = self._call_llm(messages, response_format={"type": "json_object"})
            data = self._parse_json(response)
            
            if data and "corrected_smiles" in data:
                corrected = data["corrected_smiles"].strip()
                _log.info(f"SMILESRepairAgent: Success! -> {corrected}")
                return corrected
            return None
        except Exception as e:
            _log.error(f"Error in SMILESRepairAgent: {e}")
            return None

def get_smiles_repair_agent():
    return SMILESRepairAgent()
