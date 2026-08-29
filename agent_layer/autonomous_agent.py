"""
Runs one autonomous decision cycle: Claude is given the tool set from
agent_layer.tools and drives its own tool-calling conversation — check
data, decide, trade, assess, adjust — until it stops requesting tools
and writes a final summary including its chosen interval until the
next cycle.

This differs fundamentally from agent_layer/claude_agent.py (which
only reviews a pre-generated candidate) and agent_layer/
rules_review_agent.py (which only adjusts whitelisted config values).
Here, Claude originates everything: what to look at, what to trade,
when, and how much, bounded only by the two hard backstops enforced
inside agent_layer/tools.py itself.
"""
import re
import anthropic

from config import CONFIG
from agent_layer.tools import TOOL_SCHEMAS, ToolDispatcher
from agent_layer.autonomous_prompts import AUTONOMOUS_AGENT_SYSTEM_PROMPT
from execution.trade_logger import log_event

DEFAULT_NEXT_CHECK_MINUTES = 15
MAX_TOOL_ROUNDS_PER_CYCLE = 25  # safety valve against a runaway tool-call loop within one cycle


class AutonomousTradingAgent:
    def __init__(self, config=CONFIG):
        self.config = config
        self._client = anthropic.Anthropic(api_key=config.claude.api_key)
        self._dispatcher = ToolDispatcher(config)

    async def run_cycle(self) -> int:
        """
        Runs one full decision cycle. Returns the number of minutes
        until the next cycle should run, as chosen by Claude (or a
        default if parsing fails or the key is missing).
        """
        if not self.config.claude.api_key:
            log_event("autonomous_cycle_skipped", {"reason": "ANTHROPIC_API_KEY not configured"})
            return DEFAULT_NEXT_CHECK_MINUTES

        log_event("autonomous_cycle_start", {})

        messages = [{
            "role": "user",
            "content": (
                "Begin this decision cycle. Check whatever account, position, and market information "
                "you need, decide whether to act, and act if warranted within your limits. End with "
                "your summary and the NEXT_CHECK_MINUTES line."
            ),
        }]

        final_text = ""
        for round_num in range(MAX_TOOL_ROUNDS_PER_CYCLE):
            response = self._client.messages.create(
                model=self.config.claude.model,
                max_tokens=4096,
                system=AUTONOMOUS_AGENT_SYSTEM_PROMPT,
                tools=TOOL_SCHEMAS,
                messages=messages,
            )

            text_blocks = [b.text for b in response.content if b.type == "text"]
            tool_use_blocks = [b for b in response.content if b.type == "tool_use"]

            if text_blocks:
                final_text = "\n".join(text_blocks)
                log_event("agent_reasoning", {"round": round_num, "text": final_text})

            if not tool_use_blocks:
                # Claude is done for this cycle — no more tools requested.
                break

            messages.append({"role": "assistant", "content": response.content})

            tool_results = []
            for block in tool_use_blocks:
                log_event("agent_tool_call", {"round": round_num, "tool": block.name, "input": block.input})
                result_text = await self._dispatcher.dispatch(block.name, block.input)
                log_event("agent_tool_result", {"round": round_num, "tool": block.name, "result": result_text[:500]})
                tool_results.append({
                    "type": "tool_result",
                    "tool_use_id": block.id,
                    "content": result_text,
                })
            messages.append({"role": "user", "content": tool_results})

        else:
            log_event("autonomous_cycle_max_rounds_hit", {"max_rounds": MAX_TOOL_ROUNDS_PER_CYCLE})

        next_check = self._parse_next_check_minutes(final_text)
        log_event("autonomous_cycle_end", {"summary": final_text[-2000:], "next_check_minutes": next_check})
        return next_check

    @staticmethod
    def _parse_next_check_minutes(text: str) -> int:
        match = re.search(r"NEXT_CHECK_MINUTES:\s*(\d+)", text)
        if match:
            minutes = int(match.group(1))
            return max(1, min(minutes, 240))  # sane outer bounds: 1 min to 4 hours
        return DEFAULT_NEXT_CHECK_MINUTES
