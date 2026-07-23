import json
import logging
from typing import Dict, Any
from miner.agents.base_agent import BaseAgent

_log = logging.getLogger(__name__)

class ClassifierAgent(BaseAgent):
    """
    Agent responsible for classifying the article type.
    """

    def __init__(self):
        from context import load_config
        config = load_config()
        model = config.get("model", {}).get("agents", {}).get("classifier_model", "openai/gpt-4o")
        super().__init__(model=model)

    def run(self, text_content: str) -> Dict[str, Any]:
        """
        Classifies the article based on the text content.
        """
        prompt = self._load_prompt("classifier_prompt.txt")
        content_to_analyze = text_content[:15000] # Should be sufficient to classify

        messages = [
            {"role": "system", "content": "You are a helpful assistant designed to output JSON."},
            {
                "role": "user",
                "content": f"{prompt}\n\nArticle Content (First 15k chars):\n---\n{content_to_analyze}\n---\n"
            }
        ]

        try:
            _log.info("ClassifierAgent: Calling LLM...")
            # print("DEBUG: ClassifierAgent running...")
            response = self._call_llm(messages, response_format={"type": "json_object"})
            data = self._parse_json(response)
            if data is None:
                return {"article_type": "Unknown", "confidence": 0.0, "reasoning": "Empty or invalid JSON response from LLM."}
            return data
        except Exception as e:
            _log.error(f"Error in ClassifierAgent: {e}")
            return {"article_type": "Unknown", "confidence": 0.0, "reasoning": f"Error: {e}"}
