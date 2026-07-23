
import base64
import json
import logging
from typing import Dict, Any
from miner.agents.base_agent import BaseAgent

_log = logging.getLogger(__name__)

class ReactionTableDetectorAgent(BaseAgent):
    """
    Agent responsible for detecting if an image contains a reaction table
    (optimization table, scope table, etc.) or just a reaction scheme.
    Also identifies "Junk" images like logos, icons, or editorial graphics.
    """

    def __init__(self):
        from context import load_config
        config = load_config()
        model = config.get("model", {}).get("agents", {}).get(
            "reaction_table_detector_model", "openai/gpt-4o")
        super().__init__(model=model)

    def run(self, image_path: str) -> Dict[str, Any]:
        """
        Detects if the image is a table, a scheme, or junk.
        """
        try:
            with open(image_path, "rb") as image_file:
                base64_image = base64.b64encode(image_file.read()).decode('utf-8')

            base_prompt = "You are an expert at classifying chemical figures and images. Output only valid JSON."
            
            messages = self._assemble_prompt(
                base_prompt=base_prompt,
                user_content=f"Please classify the attached image from {image_path}.",
                skills=["reaction_scheme_detection.md"]
            )
            
            # Append the image to the user's content
            if len(messages) == 2 and messages[1]["role"] == "user":
                messages[1]["content"] = [
                    {"type": "text", "text": messages[1]["content"]},
                    {
                        "type": "image_url",
                        "image_url": {"url": f"data:image/png;base64,{base64_image}"}
                    }
                ]

            _log.info(f"ReactionTableDetectorAgent: Checking image {image_path}...")
            response = self._call_llm(messages, response_format={"type": "json_object"})
            result = self._parse_json(response)
            
            if result is None:
                # Force a "not found" style result instead of None
                result = {
                    "is_table": False, 
                    "is_scheme": False, 
                    "is_junk": True, 
                    "is_relevant_chemical_diagram": False, 
                    "confidence": 0.0, 
                    "reasoning": "Empty or invalid LLM response"
                }

            # Ensure all keys exist
            if "is_junk" not in result:
                result["is_junk"] = False
            if "is_relevant_chemical_diagram" not in result:
                result["is_relevant_chemical_diagram"] = not result.get("is_junk", False)
                
            return result
        except Exception as e:
            _log.error(f"Error in ReactionTableDetectorAgent: {e}")
            return {
                "is_table": False, 
                "is_scheme": True, 
                "is_junk": False, 
                "is_relevant_chemical_diagram": True, 
                "contains_r_groups": False, 
                "confidence": 0.0, 
                "reasoning": str(e)
            }
