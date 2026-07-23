import json
import logging
from typing import List, Dict, Any, Optional
from miner.agents.base_agent import BaseAgent

_log = logging.getLogger(__name__)

class StructureParserAgent(BaseAgent):
    """
    Agent responsible for parsing the document structure from Markdown.
    It identifies Tables, Schemes, and Figures, extracts their headings, captions,
    and associated image paths, and flags whether they contain reaction information.
    """

    def __init__(self):
        from context import load_config
        config = load_config()
        model = config.get("model", {}).get("agents", {}).get("structure_parser_model", "openai/gpt-4o")
        super().__init__(model=model)

    def run(self, main_markdown: str, supplementary_markdown: Optional[str] = None) -> Dict[str, Any]:
        """
        Parses the markdown content from main and optional SI documents.
        Returns a dictionary with 'summary' and 'items'.
        """
        base_prompt = "You are a helpful assistant that outputs JSON."
        
        user_content = f"# MAIN ARTICLE\n{main_markdown}"
        if supplementary_markdown:
            user_content += f"\n\n# SUPPLEMENTARY INFORMATION\n{supplementary_markdown}"

        messages = self._assemble_prompt(
            base_prompt=base_prompt,
            user_content=user_content,
            skills=["document_structure_analysis.md"]
        )
        
        try:
            _log.info("StructureParserAgent: Calling LLM...")
            # Use a slightly higher temperature if needed, but 0 is usually best for JSON
            response = self._call_llm(messages, response_format={"type": "json_object"})
            data = self._parse_json(response)
            if not data:
                return {"items": [], "summary": "Failed to parse structure."}
            return data
        except Exception as e:
            _log.error(f"Error in StructureParserAgent: {e}")
            return {"items": [], "summary": f"Error during parsing: {str(e)}"}
