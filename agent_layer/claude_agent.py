"""
The judgment layer. Called only at discrete decision points (per-candidate,
not per-tick) — see StrategyConfig.agent_max_calls_per_session. This keeps
LLM latency and cost out of the fast intraday path entirely; the agent
reasons about *whether to take* a trade the rules engine already found,
not about market microstructure in real time.
"""
import json
from dataclasses import dataclass

import anthropic

from config import CONFIG
from fast_layer.signal_generator import SpreadCandidate
from agent_layer.prompts import SYSTEM_PROMPT, build_user_prompt


@dataclass
class AgentDecision:
    decision: str  # "approve" | "resize" | "reject"
    contracts: int
    confidence: float
    reasoning: str
    raw_response: str = ""


class TradeReviewAgent:
    def __init__(self, config=CONFIG):
        self.config = config
        self._client = anthropic.Anthropic(api_key=config.claude.api_key)
        self._calls_this_session = 0

    def review(self, candidate: SpreadCandidate, market_context: dict) -> AgentDecision:
        if self._calls_this_session >= self.config.strategy.agent_max_calls_per_session:
            return AgentDecision(
                decision="reject",
                contracts=0,
                confidence=0.0,
                reasoning="Session agent-call budget exhausted; deferring to next session rather than reviewing unbounded.",
            )

        user_prompt = build_user_prompt(candidate, market_context)
        response = self._client.messages.create(
            model=self.config.claude.model,
            max_tokens=self.config.claude.max_tokens,
            system=SYSTEM_PROMPT,
            messages=[{"role": "user", "content": user_prompt}],
        )
        self._calls_this_session += 1

        text = "".join(block.text for block in response.content if hasattr(block, "text"))
        try:
            parsed = json.loads(text)
            return AgentDecision(
                decision=parsed.get("decision", "reject"),
                contracts=int(parsed.get("contracts", 0)),
                confidence=float(parsed.get("confidence", 0.0)),
                reasoning=parsed.get("reasoning", ""),
                raw_response=text,
            )
        except (json.JSONDecodeError, ValueError, TypeError):
            # Fail closed: malformed agent output means no trade, not a guess.
            return AgentDecision(
                decision="reject",
                contracts=0,
                confidence=0.0,
                reasoning=f"Agent response could not be parsed; failing closed. Raw: {text[:200]}",
                raw_response=text,
            )

    def reset_session(self):
        self._calls_this_session = 0
