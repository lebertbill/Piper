"""
SMILES Extractor for extracting molecular structures from reaction scheme images.
"""

import os
import json
import base64
import logging
from typing import Dict, List, Any, Optional
from openai import OpenAI
from dotenv import load_dotenv

load_dotenv()
_log = logging.getLogger(__name__)


def load_prompt(filename: str) -> str:
    import os
    prompt_path = os.path.join(os.path.dirname(__file__), "prompts", filename)
    try:
        with open(prompt_path, "r", encoding="utf-8") as f:
            return f.read()
    except Exception as e:
        _log.error(f"Failed to load prompt {filename}: {e}")
        return ""

SMILES_EXTRACTION_PROMPT = load_prompt("smiles_extraction_prompt.txt")


class SmilesExtractor:
    """Extract SMILES from reaction scheme images using a configurable vision model."""
    
    def __init__(self):
        from context import load_config
        config = load_config()
        self.model = config.get("model", {}).get("agents", {}).get("smiles_extractor_model", "openai/gpt-4o")

        self.api_key = os.getenv("OPENROUTER_API_KEY") or os.getenv("API_KEY")
        if not self.api_key:
            _log.warning("No API key found for SmilesExtractor. Please set OPENROUTER_API_KEY.")
        
        self.client = OpenAI(
            base_url="https://openrouter.ai/api/v1",
            api_key=self.api_key,
            default_headers={
                "HTTP-Referer": "http://localhost:3000",
                "X-Title": "Piper-SmilesExtractor",
            },
        )
    
    def encode_image(self, image_path: str) -> str:
        """Encode image to base64."""
        with open(image_path, "rb") as image_file:
            return base64.b64encode(image_file.read()).decode('utf-8')
    
    def extract_smiles(
        self, 
        image_path: str, 
        markdown_context: Optional[str] = None
    ) -> Dict[str, Any]:
        """
        Extract SMILES from a reaction scheme image.
        
        Args:
            image_path: Path to the scheme image
            markdown_context: Optional markdown text for additional context
        
        Returns:
            Dictionary with reactants, products, intermediates, and conditions
        """
        if not os.path.exists(image_path):
            return {"error": f"Image not found: {image_path}"}
        
        base64_image = self.encode_image(image_path)
        
        # Build the prompt with optional context
        prompt = SMILES_EXTRACTION_PROMPT
        if markdown_context:
            # Extract relevant context (e.g., nearby text mentioning the scheme)
            context_snippet = self._extract_relevant_context(markdown_context, image_path)
            if context_snippet:
                prompt += f"\n\n**Additional Context from Paper:**\n{context_snippet}"
        
        messages = [
            {"role": "system", "content": "You are an expert chemist and molecular structure analyst."},
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": prompt},
                    {
                        "type": "image_url",
                        "image_url": {"url": f"data:image/png;base64,{base64_image}"}
                    }
                ]
            }
        ]
        
        try:
            response = self.client.chat.completions.create(
                model=self.model,
                messages=messages,
                response_format={"type": "json_object"},
                temperature=0
            )
            
            # ── Log the prompt and response ──
            from miner.prompt_logger import prompt_logger
            try:
                content = response.choices[0].message.content
                prompt_logger.log(self.__class__.__name__, self.model, messages, content)
            except Exception:
                pass
            from miner.token_tracker import tracker
            if hasattr(response, "usage") and response.usage:
                tracker.record(self.__class__.__name__, self.model,
                               response.usage.prompt_tokens or 0,
                               response.usage.completion_tokens or 0)

            result = json.loads(response.choices[0].message.content)
            _log.info(f"✅ Extracted SMILES from {os.path.basename(image_path)}")
            
            return result
            
        except Exception as e:
            _log.error(f"Error extracting SMILES: {e}")
            return {"error": str(e)}
    
    def _extract_relevant_context(self, markdown_content: str, image_path: str) -> str:
        """
        Extract relevant text context around the scheme reference.
        
        Looks for text mentioning the scheme name near the image reference.
        """
        # Get the image filename
        image_name = os.path.basename(image_path)
        
        # Try to find references to this image in the markdown
        # Look for "Scheme X" mentions
        import re
        scheme_pattern = r'(Scheme\s+\d+[^\n]{0,200})'
        matches = re.findall(scheme_pattern, markdown_content, re.IGNORECASE)
        
        if matches:
            # Return the first few scheme mentions as context
            return "\n".join(matches[:3])
        
        return ""

    def refine_smiles_with_table(
        self,
        image_path: str,
        generic_smiles_data: Dict[str, Any],
        table_context: Dict[str, Any]
    ) -> Dict[str, Any]:
        """
        Refine SMILES by substituting R-groups from table data into generic structures.
        
        Args:
            image_path: Path to the scheme image
            generic_smiles_data: Output from extract_smiles (Pass 1)
            table_context: Extracted table data
            
        Returns:
            Dictionary with specific reactions for each table entry
        """
        if not os.path.exists(image_path):
            return {"error": f"Image not found: {image_path}"}
            
        base64_image = self.encode_image(image_path)
        
        # Construct the prompt for Pass 2
        base_refine_prompt = load_prompt("smiles_refinement_prompt.txt")
        prompt = base_refine_prompt.format(
            generic_smiles=json.dumps(generic_smiles_data, indent=2),
            table_data=json.dumps(table_context, indent=2)
        )
        
        messages = [
            {"role": "system", "content": "You are an expert chemist specializing in molecular structure substitution."},
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": prompt},
                    {
                        "type": "image_url",
                        "image_url": {"url": f"data:image/png;base64,{base64_image}"}
                    }
                ]
            }
        ]
        
        try:
            response = self.client.chat.completions.create(
                model=self.model,
                messages=messages,
                response_format={"type": "json_object"},
                temperature=0
            )
            
            # ── Log the prompt and response ──
            from miner.prompt_logger import prompt_logger
            try:
                content = response.choices[0].message.content
                prompt_logger.log(f"{self.__class__.__name__}_Refine", self.model, messages, content)
            except Exception:
                pass
            from miner.token_tracker import tracker
            if hasattr(response, "usage") and response.usage:
                tracker.record(f"{self.__class__.__name__}_Refine", self.model,
                               response.usage.prompt_tokens or 0,
                               response.usage.completion_tokens or 0)

            result = json.loads(response.choices[0].message.content)
            _log.info(f"✅ Refined SMILES with table context for {len(result.get('specific_reactions', []))} entries")
            return result
            
        except Exception as e:
            _log.error(f"Error refining SMILES: {e}")
            return {"error": str(e)}


def extract_smiles_from_scheme(
    image_path: str, 
    markdown_context: Optional[str] = None,
    table_context: Optional[Dict[str, Any]] = None
) -> Dict[str, Any]:
    """
    Extract SMILES from a scheme image, optionally refining with table data.
    
    Args:
        image_path: Path to the reaction scheme image
        markdown_context: Optional markdown text for additional context
        table_context: Optional dictionary containing extracted table data
    
    Returns:
        Dictionary with reactants, products, intermediates, and conditions.
        If table_context is provided, includes 'specific_reactions' list.
    """
    extractor = SmilesExtractor()
    
    # Pass 1: Extract Generic SMILES
    generic_result = extractor.extract_smiles(image_path, markdown_context)
    
    # Pass 2: Refine with Table Context (if provided)
    if table_context and "error" not in generic_result:
        _log.info("Performing Pass 2: Refinement with Table Context...")
        refined_result = extractor.refine_smiles_with_table(
            image_path, 
            generic_result, 
            table_context
        )
        
        # Merge results
        if "specific_reactions" in refined_result:
            generic_result["specific_reactions"] = refined_result["specific_reactions"]
            
    return generic_result


# Working!
if __name__ == "__main__":
    import sys
    
    if len(sys.argv) > 1:
        image_path = sys.argv[1]
        result = extract_smiles_from_scheme(image_path)
        print(json.dumps(result, indent=2))
    else:
        print("Usage: python smiles_extractor.py <path_to_scheme_image>")
