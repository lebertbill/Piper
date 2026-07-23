# Deprecated. Better version is bulit in Piper-Intelligence Engine
import json
from typing import Dict, Any, List
from miner.agents.base_agent import BaseAgent

class MolecularAgent(BaseAgent):
    """
    Agent responsible for assembling and validating the final molecular structures.
    It takes the Generic Reaction and the R-Group Mappings, performs the substitution,and returns the final specific SMILES for each entry.
    """

    def run(self, generic_reaction: Dict[str, Any], r_group_mapping: Dict[str, Any]) -> Dict[str, Any]:
        # Convert inputs to string
        reaction_str = json.dumps(generic_reaction, indent=2)
        mapping_str = json.dumps(r_group_mapping, indent=2)

        messages = self._assemble_prompt(
            base_prompt="You are a helpful assistant designed to output JSON.",
            user_content=f"Generic Reaction:\n{reaction_str}\n\nR-Group Mapping:\n{mapping_str}",
            skills=["molecular_assembly.md"]
        )

        response = self._call_llm(messages, response_format={"type": "json_object"})
        data = self._parse_json(response)
        return data if data is not None else {"results": []}
