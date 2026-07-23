import logging
from typing import Optional
from miner.agents.base_agent import BaseAgent

_log = logging.getLogger(__name__)

class TableTextExtractorAgent(BaseAgent):
    """
    Agent responsible for extracting the exact text of a specific table from the Markdown content.
    It extracts from the Table heading to the end of its footnote/caption.
    """

    def __init__(self):
        from context import load_config
        config = load_config()
        # Use a capable model for text extraction/identification
        model = config.get("model", {}).get("agents", {}).get("structure_parser_model", "openai/gpt-4o")
        super().__init__(model=model)

    def run(self, markdown_content: str, table_heading: str) -> str:
        """
        Extracts the text for the given table heading.
        """
        base_prompt = "You are a precise text extractor."
        user_content = f"Target Table Heading: {table_heading}\n\nMarkdown Content:\n{markdown_content}"
        
        messages = self._assemble_prompt(
            base_prompt=base_prompt,
            user_content=user_content,
            skills=["table_text_extraction.md"]
        )

        
        try:
            _log.info(f"TableTextExtractorAgent: Extracting text for {table_heading}...")
            print(f"DEBUG: TableTextExtractorAgent using model: {self.model}")
            # print(f"DEBUG: TableTextExtractorAgent API Key prefix: {self.api_key[:5] if self.api_key else 'None'}")
            response = self._call_llm(messages)
            raw_content = response.choices[0].message.content
            if raw_content is None:
                _log.warning(f"TableTextExtractorAgent: Received empty content for {table_heading}")
                return ""
            
            content = raw_content.strip()
            
            if "TABLE_NOT_FOUND" in content:
                _log.warning(f"Table {table_heading} not found in markdown.")
                return ""
                
            return content
        except Exception as e:
            _log.error(f"Error in TableTextExtractorAgent: {e}")
            return ""
