"""
Tests for ``debugger.__main__.make_client`` and ``resolve_provider``.

These exercise the multi-provider client-construction layer without making
any real network calls.  Each provider only needs its environment variables
to be present at construction time; the adapters defer the actual HTTP
request to ``client.messages.create`` / ``client.chat.completions.create``,
which we never invoke here.
"""

from __future__ import annotations

import pytest

from debugger.__main__ import make_client, resolve_provider


# ---------------------------------------------------------------------------
# resolve_provider â€” pure-function table tests
# ---------------------------------------------------------------------------


class TestResolveProvider:
    """One assertion per (model identifier, expected provider) pair."""

    def test_gemini_models(self) -> None:
        assert resolve_provider("gemini-3-flash")            == "gemini"
        assert resolve_provider("gemini-2.5-pro")            == "gemini"
        assert resolve_provider("Gemini-2.5-Flash-Lite")     == "gemini"

    def test_qwen_models_lowercase(self) -> None:
        assert resolve_provider("qwen-3.5-flash")            == "qwen"
        assert resolve_provider("qwen3-72b")                 == "qwen"
        assert resolve_provider("qwen2.5-72b-instruct")      == "qwen"

    def test_qwen_models_path_style(self) -> None:
        # Case-insensitive: Qwen/Qwen3-72B is also routed to the QWEN proxy.
        assert resolve_provider("Qwen/Qwen3-72B-Instruct")   == "qwen"
        assert resolve_provider("Qwen/Qwen2.5-VL-72B")       == "qwen"

    def test_anthropic_perplexity_route(self) -> None:
        # SONNET = Perplexity-routed Anthropic models.
        assert resolve_provider("anthropic/claude-sonnet-4-5") == "sonnet"
        assert resolve_provider("anthropic/claude-sonnet-4-6") == "sonnet"
        assert resolve_provider("anthropic/claude-opus-4-1")   == "sonnet"

    def test_anthropic_direct(self) -> None:
        # Direct Anthropic API uses claude-* without the anthropic/ prefix.
        assert resolve_provider("claude-sonnet-4") == "anthropic"
        assert resolve_provider("claude-opus-4-7")            == "anthropic"

    def test_openai_models(self) -> None:
        assert resolve_provider("gpt-4o-2024-08-06") == "openai"
        assert resolve_provider("o3") == "openai"

    def test_unknown_model_raises(self) -> None:
        with pytest.raises(ValueError) as exc:
            resolve_provider("unknown-model")
        assert "gemini" in str(exc.value) and "qwen" in str(exc.value)

# ---------------------------------------------------------------------------
# make_client â€” env-var driven adapter construction
# ---------------------------------------------------------------------------


@pytest.fixture
def _clean_env(monkeypatch):
    """Strip every provider-related env var so each test starts from zero."""
    for k in (
        "ANTHROPIC_API_KEY",
        "GEMINI_API_KEY",  "GEMINI_BASE_URL",
        "QWEN_API_KEY",    "QWEN_BASE_URL",
        "SONNET_API_KEY",  "SONNET_BASE_URL",
    ):
        monkeypatch.delenv(k, raising=False)
    yield monkeypatch


class TestMakeClientOpenAICompat:
    """The three OpenAI-compat providers (gemini / qwen / sonnet) all use
    the same ``OpenAICompatAdapter`` â€” only their env-var prefix differs."""

    def test_gemini(self, _clean_env) -> None:
        _clean_env.setenv("GEMINI_API_KEY",  "key-gemini")
        _clean_env.setenv("GEMINI_BASE_URL", "https://gemini.example/v1")
        c = make_client("gemini", "gemini-3-flash")
        from debugger.openai_adapter import OpenAICompatAdapter
        assert isinstance(c, OpenAICompatAdapter)
        # The adapter stashes the OpenAI client; verify it points at the
        # configured base URL so we know the env-prefix wiring worked.
        assert str(c._client.base_url).startswith("https://gemini.example/")

    def test_qwen(self, _clean_env) -> None:
        _clean_env.setenv("QWEN_API_KEY",  "key-qwen")
        _clean_env.setenv("QWEN_BASE_URL", "https://qwen.example/v1")
        c = make_client("qwen", "qwen3-flash")
        from debugger.openai_adapter import OpenAICompatAdapter
        assert isinstance(c, OpenAICompatAdapter)
        assert str(c._client.base_url).startswith("https://qwen.example/")

    def test_sonnet(self, _clean_env) -> None:
        # SONNET == Perplexity; model identifier carries the anthropic/ prefix.
        # Note: SONNET uses PerplexityResponsesAdapter (NOT OpenAICompatAdapter)
        # because Perplexity 404s anthropic/* models on /v1/chat/completions
        # and only serves them via /v1/responses.
        _clean_env.setenv("SONNET_API_KEY",  "test-key")
        _clean_env.setenv("SONNET_BASE_URL", "https://api.perplexity.ai/v1")
        c = make_client("sonnet", "anthropic/claude-sonnet-4-5")
        from debugger.perplexity_adapter import PerplexityResponsesAdapter
        assert isinstance(c, PerplexityResponsesAdapter)
        assert str(c._client.base_url).startswith("https://api.perplexity.ai/")


class TestMakeClientErrorPaths:
    """make_client uses sys.exit on bad configuration; verify that path."""

    def test_unknown_provider_exits(self, _clean_env) -> None:
        with pytest.raises(SystemExit):
            make_client("does-not-exist", "any-model")

    def test_missing_gemini_base_url_exits(self, _clean_env) -> None:
        # API key present but BASE_URL missing â†’ sys.exit.
        _clean_env.setenv("GEMINI_API_KEY", "key")
        with pytest.raises(SystemExit):
            make_client("gemini", "gemini-3-flash")

    def test_missing_qwen_base_url_exits(self, _clean_env) -> None:
        _clean_env.setenv("QWEN_API_KEY", "key")
        with pytest.raises(SystemExit):
            make_client("qwen", "qwen3-flash")

    def test_missing_sonnet_base_url_exits(self, _clean_env) -> None:
        _clean_env.setenv("SONNET_API_KEY", "test-key")
        with pytest.raises(SystemExit):
            make_client("sonnet", "anthropic/claude-sonnet-4-5")

    def test_missing_anthropic_key_exits(self, _clean_env) -> None:
        with pytest.raises(SystemExit):
            make_client("anthropic", "claude-opus-4-7")


# ---------------------------------------------------------------------------
# Integration: resolve_provider + make_client end-to-end
# ---------------------------------------------------------------------------


class TestResolveAndMakeTogether:
    """The 3 run_*.py scripts call ``make_client(resolve_provider(model), model)``
    â€” verify that this composition produces the right adapter for each
    canonical model name we plan to support."""

    @pytest.mark.parametrize(
        "model, env_prefix, expected_url, expected_adapter_attr",
        [
            ("gemini-3-flash",              "GEMINI", "https://gemini.example/v1",   "OpenAICompatAdapter"),
            ("qwen3-flash",                 "QWEN",   "https://qwen.example/v1",     "OpenAICompatAdapter"),
            ("anthropic/claude-sonnet-4-5", "SONNET", "https://api.perplexity.ai/v1", "PerplexityResponsesAdapter"),
        ],
    )
    def test_end_to_end_routing(
        self,
        _clean_env,
        model: str,
        env_prefix: str,
        expected_url: str,
        expected_adapter_attr: str,
    ) -> None:
        _clean_env.setenv(f"{env_prefix}_API_KEY",  "test-key")
        _clean_env.setenv(f"{env_prefix}_BASE_URL", expected_url)

        provider = resolve_provider(model)
        client = make_client(provider, model)

        if expected_adapter_attr == "OpenAICompatAdapter":
            from debugger.openai_adapter import OpenAICompatAdapter as Expected
        else:
            from debugger.perplexity_adapter import PerplexityResponsesAdapter as Expected
        assert isinstance(client, Expected)
        assert str(client._client.base_url).startswith(expected_url.rstrip("/v1"))


if __name__ == "__main__":
    pytest.main([__file__, "-v"])




