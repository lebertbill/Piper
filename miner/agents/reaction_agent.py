# Deprecated. Better version is bulit in Piper-Intelligence Engine
import base64
import json
from typing import Dict, Any
from miner.agents.base_agent import BaseAgent

class ReactionAgent(BaseAgent):
    """
    Agent responsible for extracting the generic reaction skeleton from an image.
    Identifies reactants, products, conditions, and generic placeholders (e.g., R, Ar).
    """
    
    def run(self, image_path: str) -> Dict[str, Any]:
        with open(image_path, "rb") as image_file:
            base64_image = base64.b64encode(image_file.read()).decode('utf-8')

        base_prompt = "You are an expert organic chemist. Your task is to extract the reaction scheme from the provided image. Output valid JSON."
        
        messages = self._assemble_prompt(
            base_prompt=base_prompt,
            user_content=f"Please extract the reaction data from the attached image {image_path}.",
            skills=["reaction_data_extraction.md"]
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


        # ENSEMBLE VOTING: Run 3 times to ensure stability
        results = []
        import logging
        logger = logging.getLogger(__name__)
        
        for i in range(3):
            logger.info(f"  -> ReactionAgent Voting Round {i+1}/3...")
            try:
                response = self._call_llm(messages, response_format={"type": "json_object"})
                parsed = self._parse_json(response)
                if parsed:
                    results.append(parsed)
            except Exception as e:
                logger.error(f"Error in voting round {i+1}: {e}")

        if not results:
            logger.error("ReactionAgent: All voting rounds failed to return valid JSON.")
            return {} # Return empty dict instead of raising to allow pipeline to continue

        # Majority Vote Logic
        # We serialize the JSONs to strings to compare them
        from collections import Counter
        
        # Normalize JSONs for comparison (sort keys)
        json_strings = [json.dumps(r, sort_keys=True) for r in results]
        counter = Counter(json_strings)
        
        most_common_json_str, count = counter.most_common(1)[0]
        
        logger.info(f"  -> Ensemble Result: Winner appeared {count}/3 times.")
        
        return json.loads(most_common_json_str)
