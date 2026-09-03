"""Host-specific transcript adapters."""

from agentdebug.capture.hosts.claude_code import ClaudeCodeCaptureAdapter
from agentdebug.capture.hosts.codex import CodexCaptureAdapter

__all__ = ['ClaudeCodeCaptureAdapter', 'CodexCaptureAdapter']
