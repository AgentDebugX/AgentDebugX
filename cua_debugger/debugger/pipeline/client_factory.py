"""Provider client factory.

API keys come from env vars (per-provider: OPENAI_API_KEY, GEMINI_API_KEY, etc.).
`base_url` is passed in by caller (typically `cfg.base_url`).
Any provider other than `anthropic`/`together` routes through OpenAI-compatible.
"""

import os
import sys

from .runtime import log


def _require_env(name: str) -> str:
    value = os.environ.get(name)
    if not value:
        log.error(f"ERROR: Set {name} environment variable.")
        sys.exit(1)
    return value


def make_client(provider: str, model: str, base_url: str | None = None):
    if provider == "anthropic":
        from anthropic import Anthropic
        return Anthropic(api_key=_require_env("ANTHROPIC_API_KEY"))

    if provider == "together":
        from debugger.together_adapter import TogetherAnthropicAdapter
        return TogetherAnthropicAdapter(model=model, api_key=_require_env("TOGETHER_API_KEY"))

    # OpenAI-compatible (openai, gemini, azure, openrouter, vllm, ...)
    from debugger.openai_adapter import OpenAICompatAdapter
    api_key = _require_env(f"{provider.upper()}_API_KEY")
    if provider == "openai" and not base_url:
        base_url = "https://api.openai.com/v1"
    if not base_url:
        log.error(
            f"ERROR: provider='{provider}' needs a base_url. "
            f"Add base_urls['{provider}'] in debugger/config/debugger.json, "
            f"or pass --base-url."
        )
        sys.exit(1)
    return OpenAICompatAdapter(model=model, api_key=api_key, base_url=base_url)

