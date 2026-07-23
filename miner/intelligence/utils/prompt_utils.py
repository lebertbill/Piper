import os

# ! Deprecated !
def load_prompt_with_commons(prompt_filename: str) -> str:
    """Load a prompt file and prepend shared common rules."""
    from miner.intelligence.engine import load_prompt_with_commons as load_prompt
    return load_prompt(prompt_filename)
