import json
from typing import Any, Dict, Optional
from miner.agents.base_agent import BaseAgent

class TextReactionAgent(BaseAgent):
    """
    Agent responsible for extracting the generic reaction skeleton from text content.
    Identifies reactants, products, conditions, and generic placeholders (e.g., R, Ar)
    from the text description of the reaction.
    """
    
    def run(self, text_content: str, table_data: Optional[Dict] = None) -> Dict[str, Any]:
        base_prompt = "You are a helpful assistant designed to output JSON. Do NOT output SMILES strings."
        user_content = f"Text Content:\n{text_content}\n\nTable Data:\n{table_data}"
        
        messages = self._assemble_prompt(
            base_prompt=base_prompt,
            user_content=user_content,
            skills=["text_reaction_extraction.md"]
        )

        
        try:
            response = self._call_llm(messages, response_format={"type": "json_object"})
            data = self._parse_json(response)
            return data if data is not None else {"error": "Empty or invalid JSON response"}
        except Exception as e:
            print(f"Error in TextReactionAgent: {e}")
            return {"error": str(e)}
