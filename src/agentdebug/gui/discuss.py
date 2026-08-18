"""
Interactive discussion engine for the debugger annotation panel.

Lets the human annotator chat with the debugger LLM in the context of the
current task's trajectory and RCA result.  Reuses the same tool dispatch
infrastructure as the RCA engine but with a discussion-specific system prompt
and the propose_annotation tool (which is NOT available during RCA).
"""

from pathlib import Path
from typing import Generator, Optional

from .config import load_config
from .dispatch import dispatch_tool
from .prompts_discuss import DISCUSSION_SYSTEM_PROMPT
from .tools import DISCUSSION_TOOLS
from .trajectory import load_normalized_trajectory

# Budget / limits for the discussion agent
_cfg = load_config()
THINKING_BUDGET = _cfg.discuss_thinking_budget
MAX_TOKENS = _cfg.discuss_max_tokens
MAX_TOOL_ROUNDS = _cfg.discuss_max_tool_rounds


def _is_anthropic_client(client) -> bool:
    """Check if client is a native Anthropic client (supports streaming)."""
    try:
        from anthropic import Anthropic
        return isinstance(client, Anthropic)
    except ImportError:
        return False


class DiscussionSession:
    """Manages an interactive discussion about an RCA result."""

    def __init__(
        self,
        client,
        model: str,
        task_rca: dict,
        traj_path: str,
    ):
        self.client = client
        self.model = model
        self.task_rca = task_rca
        self.traj_path = traj_path
        self.supports_stream = _is_anthropic_client(client)

        # Pre-load trajectory data so the agent doesn't need a path
        self._traj_data: Optional[dict] = None
        traj_dir = Path(traj_path)
        if traj_dir.is_dir():
            try:
                self._traj_data = load_normalized_trajectory(traj_dir)
            except Exception:
                pass

        self._osworld_root = Path(traj_path).parent if traj_path else Path(".")

        # Build the initial context message with the RCA summary
        context = self._build_context(task_rca)
        self.messages: list[dict] = [
            {"role": "user", "content": context},
        ]

        self._initial_response: Optional[str] = None
        # Filled after streaming turn completes (for message history)
        self._last_proposal: Optional[dict] = None

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def get_initial_greeting(self) -> str:
        """Run the first agent turn (greeting / context acknowledgment)."""
        if self._initial_response is not None:
            return self._initial_response
        text, _ = self._run_turn()
        self._initial_response = text
        return text

    def send_message(self, user_message: str) -> tuple[str, Optional[dict]]:
        """Send a user message (non-streaming). Returns (text, proposal)."""
        self.messages.append({"role": "user", "content": user_message})
        return self._run_turn()

    def send_message_stream(self, user_message: str) -> Generator[str, None, tuple[str, Optional[dict]]]:
        """
        Send a user message and yield text chunks as they arrive.

        Usage:
            gen = session.send_message_stream("Why step 5?")
            full_text = ""
            proposal = None
            for chunk in gen:
                full_text += chunk
                display(chunk)
            # After generator exhausts, retrieve final result:
            full_text, proposal = session.get_stream_result()

        Falls back to non-streaming for providers that don't support it.
        """
        self.messages.append({"role": "user", "content": user_message})

        if not self.supports_stream:
            # Fallback: yield the full response at once
            text, proposal = self._run_turn()
            self._last_proposal = proposal
            self._stream_full_text = text
            yield text
            return

        # Streaming path (Anthropic SDK)
        full_text, proposal = yield from self._run_turn_stream()
        self._last_proposal = proposal
        self._stream_full_text = full_text

    def get_stream_result(self) -> tuple[str, Optional[dict]]:
        """Get the final text and proposal after streaming completes."""
        return getattr(self, "_stream_full_text", ""), self._last_proposal

    # ------------------------------------------------------------------
    # Internal — non-streaming
    # ------------------------------------------------------------------

    def _build_context(self, rca: dict) -> str:
        lines = [
            "We are reviewing the following RCA analysis together.",
            "",
            f"Task ID: {rca.get('task_id', 'unknown')}",
            f"Instruction: {rca.get('instruction', 'N/A')}",
            f"Total steps: {rca.get('total_steps', '?')}",
            "",
            "## Original RCA Result",
            f"Root Error Step: {rca.get('root_error_step', '?')}",
            f"Taxonomy Tag: {rca.get('taxonomy_tag', '?')}",
            f"Confidence: {rca.get('confidence', '?')}",
            "",
            "### Evidence",
            rca.get("evidence", "(none)"),
            "",
            "### Correction",
            rca.get("correction", "(none)"),
            "",
            "The trajectory is already loaded. You can call get_step_details "
            "to re-examine any step. I'm the human annotator — I'll ask "
            "questions about your analysis.",
        ]
        return "\n".join(lines)

    def _run_turn(self) -> tuple[str, Optional[dict]]:
        """Non-streaming turn: run until end_turn or budget exhausted."""
        proposal: Optional[dict] = None
        assistant_text = ""

        for _round in range(MAX_TOOL_ROUNDS):
            try:
                response = self.client.messages.create(
                    model=self.model,
                    max_tokens=MAX_TOKENS,
                    thinking={"type": "enabled", "budget_tokens": THINKING_BUDGET},
                    system=DISCUSSION_SYSTEM_PROMPT,
                    tools=DISCUSSION_TOOLS,
                    messages=self.messages,
                )
            except Exception as e:
                return f"LLM API error: {e}", None

            self.messages.append({"role": "assistant", "content": response.content})

            for block in response.content:
                btype = getattr(block, "type", None)
                if btype == "text" and getattr(block, "text", ""):
                    assistant_text += block.text

            if response.stop_reason == "end_turn":
                return assistant_text.strip(), proposal

            tool_results = self._process_tool_calls(response.content)
            proposal = self._extract_proposal(response.content) or proposal
            self.messages.append({"role": "user", "content": tool_results})

        if not assistant_text:
            assistant_text = "(The agent used all available tool rounds without producing a text response.)"
        return assistant_text.strip(), proposal

    # ------------------------------------------------------------------
    # Internal — streaming (Anthropic only)
    # ------------------------------------------------------------------

    def _run_turn_stream(self) -> Generator[str, None, tuple[str, Optional[dict]]]:
        """
        Streaming turn: yield text deltas, handle tool calls internally.
        Returns (full_text, proposal) when done.
        """
        proposal: Optional[dict] = None
        full_text = ""

        for _round in range(MAX_TOOL_ROUNDS):
            try:
                round_text, round_content, stop_reason = yield from self._stream_one_round()
            except Exception as e:
                error = f"LLM API error: {e}"
                yield error
                return error, None

            full_text += round_text
            self.messages.append({"role": "assistant", "content": round_content})

            if stop_reason == "end_turn":
                return full_text.strip(), proposal

            # Process tool calls (not streamed to user)
            tool_results = self._process_tool_calls(round_content)
            proposal = self._extract_proposal(round_content) or proposal
            self.messages.append({"role": "user", "content": tool_results})

        if not full_text:
            full_text = "(The agent used all available tool rounds without producing a text response.)"
        return full_text.strip(), proposal

    def _stream_one_round(self) -> Generator[str, None, tuple[str, list, str]]:
        """
        Stream a single API call. Yields text deltas.
        Returns (round_text, content_blocks, stop_reason).
        """
        round_text = ""
        content_blocks = []
        stop_reason = "end_turn"

        with self.client.messages.stream(
            model=self.model,
            max_tokens=MAX_TOKENS,
            thinking={"type": "enabled", "budget_tokens": THINKING_BUDGET},
            system=DISCUSSION_SYSTEM_PROMPT,
            tools=DISCUSSION_TOOLS,
            messages=self.messages,
        ) as stream:
            for event in stream:
                etype = getattr(event, "type", "")
                if etype == "content_block_delta":
                    delta = getattr(event, "delta", None)
                    if delta and getattr(delta, "type", "") == "text_delta":
                        chunk = delta.text
                        round_text += chunk
                        yield chunk

            # After stream completes, get the full response
            response = stream.get_final_message()
            content_blocks = response.content
            stop_reason = response.stop_reason

        return round_text, content_blocks, stop_reason

    # ------------------------------------------------------------------
    # Internal — shared helpers
    # ------------------------------------------------------------------

    def _process_tool_calls(self, content_blocks) -> list[dict]:
        """Dispatch tool calls and return tool_result messages."""
        tool_results: list[dict] = []
        for block in content_blocks:
            if not hasattr(block, "type") or block.type != "tool_use":
                continue
            if block.name == "propose_annotation":
                tool_results.append({
                    "type": "tool_result",
                    "tool_use_id": block.id,
                    "content": "Annotation proposal submitted. The human can now review and apply it.",
                })
            else:
                content, self._traj_data = dispatch_tool(
                    block.name,
                    block.input,
                    self._traj_data,
                    self._osworld_root,
                )
                tool_results.append({
                    "type": "tool_result",
                    "tool_use_id": block.id,
                    "content": content,
                })
        return tool_results

    def _extract_proposal(self, content_blocks) -> Optional[dict]:
        """Extract propose_annotation input if present."""
        for block in content_blocks:
            if hasattr(block, "type") and block.type == "tool_use" and block.name == "propose_annotation":
                return dict(block.input)
        return None
