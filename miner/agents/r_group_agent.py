# Deprecated. Better version is bulit in Piper-Intelligence Engine
import json
from typing import Dict, Any, List
from miner.agents.base_agent import BaseAgent

class RGroupAgent(BaseAgent):
    """
    Agent responsible for mapping table data to generic placeholders in the reaction scheme.
    """



    def run(self, table_data: Any, generic_reaction: Dict[str, Any]) -> Dict[str, Any]:
        # Convert inputs to string for the prompt
        table_str = json.dumps(table_data, indent=2) if isinstance(table_data, (dict, list)) else str(table_data)
        reaction_str = json.dumps(generic_reaction, indent=2)

        messages = self._assemble_prompt(
            base_prompt="You are a helpful assistant designed to output JSON.",
            user_content=f"Table Data:\n{table_str}\n\nGeneric Reaction:\n{reaction_str}",
            skills=["r_group_mapping.md"]
        )

        response = self._call_llm(messages, response_format={"type": "json_object"})
        data = self._parse_json(response)
        return data if data is not None else {}
