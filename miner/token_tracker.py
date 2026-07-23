"""
Thread-safe singleton token tracker for accumulating LLM usage across agents.
Usage:
    from miner.token_tracker import tracker
    tracker.record("AgentName", "openai/gpt-5.1", prompt_tokens, completion_tokens)
    summary = tracker.get_summary()
    tracker.save("path/to/token_usage.json")
    tracker.reset()
"""

import json
import threading
import time
from pathlib import Path
from typing import Any, Dict, List

# ── Model pricing (USD per 1 million tokens) ──────────────────────────────
# Source: https://openrouter.ai/{provider}/{model}  (as of 2026-02-25)
_PRICING = {
    # ── OpenAI ──
    "openai/gpt-5.1":      {"input": 1.25,  "output": 10.00},
    "openai/gpt-5":        {"input": 2.00,  "output": 8.00},
    "openai/gpt-5-nano":   {"input": 0.05,  "output": 0.40},
    "openai/gpt-4o":       {"input": 2.50,  "output": 10.00},
    "openai/gpt-4o-mini":  {"input": 0.15,  "output": 0.60},
    "openai/gpt-4.1":      {"input": 2.00,  "output": 8.00},
    "openai/gpt-4.1-nano": {"input": 0.10,  "output": 0.40},
    "openai/gpt-4.1-mini": {"input": 0.40,  "output": 1.60},
    "openai/gpt-5-mini":   {"input": 0.25,  "output": 2.00},
    # ── Google ──
    "google/gemini-3-flash-preview": {"input": 0.50, "output": 3.00},
    "google/gemini-2.5-flash":       {"input": 0.30, "output": 2.50},
    "google/gemini-2.5-pro":         {"input": 1.25, "output": 10.00},
    "google/gemini-2.0-flash":       {"input": 0.10, "output": 0.40},
    # ── DeepSeek ──
    "deepseek/deepseek-chat-v3-0324": {"input": 0.19, "output": 0.87},
    "deepseek/deepseek-chat":         {"input": 0.19, "output": 0.87},
    "deepseek/deepseek-r1":           {"input": 0.70, "output": 2.50},
}
_DEFAULT_PRICING = {"input": 3.00, "output": 12.00}


class TokenTracker:
    """Accumulates token usage across multiple LLM calls for a single article."""

    def __init__(self):
        self._lock = threading.Lock()
        self._calls: List[Dict[str, Any]] = []

    def record(
        self,
        agent_name: str,
        model: str,
        prompt_tokens: int,
        completion_tokens: int,
    ) -> None:
        """Record a single LLM call."""
        prices = _PRICING.get(model, _DEFAULT_PRICING)
        input_cost = prompt_tokens * prices["input"] / 1_000_000
        output_cost = completion_tokens * prices["output"] / 1_000_000

        entry = {
            "agent": agent_name,
            "model": model,
            "input_tokens": prompt_tokens,
            "output_tokens": completion_tokens,
            "input_cost_usd": round(input_cost, 6),
            "output_cost_usd": round(output_cost, 6),
            "total_cost_usd": round(input_cost + output_cost, 6),
            "timestamp": time.time(),
        }

        with self._lock:
            self._calls.append(entry)

        print(
            f"  📊 Token usage [{agent_name}]: "
            f"{prompt_tokens} in / {completion_tokens} out  "
            f"(${input_cost + output_cost:.4f})"
        )

    def get_summary(self) -> Dict[str, Any]:
        """Return an aggregate summary of all recorded calls."""
        with self._lock:
            calls = list(self._calls)

        total_input = sum(c["input_tokens"] for c in calls)
        total_output = sum(c["output_tokens"] for c in calls)
        total_input_cost = sum(c["input_cost_usd"] for c in calls)
        total_output_cost = sum(c["output_cost_usd"] for c in calls)
        total_cost = sum(c["total_cost_usd"] for c in calls)

        # Per-agent breakdown
        agents: Dict[str, Dict[str, Any]] = {}
        for c in calls:
            name = c["agent"]
            if name not in agents:
                agents[name] = {
                    "calls": 0,
                    "input_tokens": 0,
                    "output_tokens": 0,
                    "cost_usd": 0.0,
                }
            agents[name]["calls"] += 1
            agents[name]["input_tokens"] += c["input_tokens"]
            agents[name]["output_tokens"] += c["output_tokens"]
            agents[name]["cost_usd"] = round(
                agents[name]["cost_usd"] + c["total_cost_usd"], 6
            )

        return {
            "total_calls": len(calls),
            "total_input_tokens": total_input,
            "total_output_tokens": total_output,
            "total_input_cost_usd": round(total_input_cost, 6),
            "total_output_cost_usd": round(total_output_cost, 6),
            "total_cost_usd": round(total_cost, 6),
            "per_agent": agents,
            "calls": calls,
        }

    def save(self, path) -> None:
        """Persist the usage summary to a JSON file."""
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        summary = self.get_summary()
        with open(path, "w", encoding="utf-8") as f:
            json.dump(summary, f, indent=2, ensure_ascii=False)
        print(f"  💰 Token usage saved to {path}")

    def reset(self) -> None:
        """Clear all recorded calls (call between articles)."""
        with self._lock:
            self._calls.clear()


# ── Module-level singleton ─────────────────────────────────────────────────
tracker = TokenTracker()
