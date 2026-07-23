import os
import re
import json
import logging
from typing import Dict, Any, Optional
from openai import OpenAI
from dotenv import load_dotenv

load_dotenv()
_log = logging.getLogger(__name__)

class BaseAgent:
    """
    Base class for all specialized agents in the Piper system.
    """
    def __init__(self, model: str = "openai/gpt-4o"):
        self.api_key = os.getenv("OPENROUTER_API_KEY") or os.getenv("API_KEY")
        if not self.api_key:
            _log.warning(f"No API key found for {self.__class__.__name__}. Please set OPENROUTER_API_KEY.")
            print(f"DEBUG: {self.__class__.__name__} - API Key is MISSING. Env vars: {list(os.environ.keys())}")
        else:
            # print(f"DEBUG: {self.__class__.__name__} - API Key found (starts with {self.api_key[:5]}...)")
            pass
        
        self.client = OpenAI(
            base_url="https://openrouter.ai/api/v1",
            api_key=self.api_key,
            default_headers={
                "HTTP-Referer": "http://localhost:3000",
                "X-Title": "Piper Multi-Agent",
            },
        )
        self.model = model

    def run(self, input_data: Any) -> Dict[str, Any]:
        """
        Main execution method for the agent.
        Must be implemented by subclasses.
        """
        raise NotImplementedError("Subclasses must implement the run method.")

    def _call_llm(self, messages: list, response_format: Optional[dict] = None) -> Dict[str, Any]:
        """
        Helper method to call the LLM.
        """
        try:
            kwargs = {
                "model": self.model,
                "messages": messages,
                "temperature": 0
            }
            if response_format:
                kwargs["response_format"] = response_format

            response = self.client.chat.completions.create(**kwargs)

            # ── Log the prompt and response ──
            from miner.prompt_logger import prompt_logger
            try:
                content = response.choices[0].message.content
                prompt_logger.log(self.__class__.__name__, self.model, messages, content)
            except Exception:
                pass

            # ── Track token usage ── !!!(works with agents that subclassess base_agent. There are some llm calls which needs to be integrated.)!!!
            from miner.token_tracker import tracker
            if hasattr(response, 'usage') and response.usage:
                tracker.record(
                    self.__class__.__name__,
                    self.model,
                    response.usage.prompt_tokens or 0,
                    response.usage.completion_tokens or 0,
                )

            return response
        except Exception as e:
            _log.error(f"Error calling LLM in {self.__class__.__name__}: {e}")
            raise

    def _parse_json(self, response: Any) -> Any:
        """
        Safely parses a JSON response from the LLM.
        """
        content = None
        try:
            content = response.choices[0].message.content
            if not content:
                _log.warning(f"[{self.__class__.__name__}] LLM returned an empty response.")
                return None
            # Strip markdown code fences (```json ... ``` or ``` ... ```)
            stripped = content.strip()
            if stripped.startswith("```"):
                stripped = re.sub(r'^```[a-zA-Z]*\n?', '', stripped)
                stripped = re.sub(r'\n?```$', '', stripped).strip()
            return json.loads(stripped)
        except json.JSONDecodeError:
            # Last resort: extract first {...} or [...] block
            if content:
                m = re.search(r'(\{[\s\S]*\}|\[[\s\S]*\])', content)
                if m:
                    try:
                        return json.loads(m.group(1))
                    except json.JSONDecodeError:
                        pass
            _log.error(f"[{self.__class__.__name__}] Failed to decode JSON. Content: {(content or '')[:200]}")
            return None
        except Exception as e:
            _log.error(f"[{self.__class__.__name__}] Unexpected error parsing JSON: {e}")
            return None

    def _load_skill(self, skill_name: str) -> str:
        # Needs a better implementation in future versions.
        """
        Loads the content of a skill file from the miner/skills directory.
        """
        skill_path = os.path.join(os.path.dirname(__file__), "..", "skills", skill_name)
        try:
            with open(skill_path, 'r', encoding='utf-8') as f:
                return f.read()
        except FileNotFoundError:
            _log.warning(f"[{self.__class__.__name__}] Skill file not found: {skill_path}")
            return ""
        except Exception as e:
            _log.error(f"[{self.__class__.__name__}] Error loading skill {skill_name}: {e}")
            return ""

    def _load_prompt(self, prompt_name: str) -> str:
        """
        Loads the content of a prompt file from the miner/prompts directory.
        """
        prompt_path = os.path.join(os.path.dirname(__file__), "..", "prompts", prompt_name)
        try:
            with open(prompt_path, 'r', encoding='utf-8') as f:
                return f.read()
        except FileNotFoundError:
            _log.warning(f"[{self.__class__.__name__}] Prompt file not found: {prompt_path}")
            return ""
        except Exception as e:
            _log.error(f"[{self.__class__.__name__}] Error loading prompt {prompt_name}: {e}")
            return ""

    def _assemble_prompt(self, base_prompt: str, user_content: str, skills: list = None, supplementary_content: str = None, image_path: str = None) -> list:
        """
        Assembles the system and user messages, dynamically injecting skill instructions.
        Supports optional image input for vision-capable models.
        """
        import base64
        system_content = base_prompt
        
        if skills:
            system_content += "\n\n# REQUIRED SKILLS\n"
            system_content += "The following skills provide domain knowledge and specific instructions you must follow:\n"
            for skill in skills:
                skill_text = self._load_skill(skill)
                if skill_text:
                    system_content += f"\n--- Start of Skill: {skill} ---\n{skill_text}\n--- End of Skill: {skill} ---\n"
                    
        messages = [
            {"role": "system", "content": system_content}
        ]

        user_msg_parts = []
        user_msg_parts.append({"type": "text", "text": f"Main Input:\n{user_content}"})
        
        has_image = False
        if image_path and os.path.exists(image_path):
            with open(image_path, "rb") as image_file:
                b64_img = base64.b64encode(image_file.read()).decode('utf-8')
            user_msg_parts.append({
                "type": "image_url",
                "image_url": {
                    "url": f"data:image/png;base64,{b64_img}"
                }
            })
            has_image = True
            
        # If no image, use a simple string for the user message to maximize compatibility
        if not has_image:
            messages.append({"role": "user", "content": f"Main Input:\n{user_content}"})
        else:
            messages.append({"role": "user", "content": user_msg_parts})
        
        if supplementary_content:
            messages.append({"role": "user", "content": f"Supplementary Input:\n{supplementary_content}"})
            
        return messages

