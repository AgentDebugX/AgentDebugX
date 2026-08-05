"""Token/cost accounting on the LLM client and on AttributionResult.

Before this, `raw['usage']` reached `CompletionResult.raw` and stopped there. A caller
driving thousands of attributions had no way to bill them, so downstream cost tables
reported 0.0 for every AgentDebugX diagnosis — which makes a cheap attributor and an
expensive one look identical, and that is precisely the comparison anyone choosing between
all_at_once, binary_search and ensemble has to make.
"""

from __future__ import annotations

import agentdebug as ad


def test_parses_openai_field_names():
    u = ad.TokenUsage.from_response(
        {"usage": {"prompt_tokens": 100, "completion_tokens": 50}},
        price_in=1.0, price_out=2.0,
    )
    assert (u.prompt_tokens, u.completion_tokens, u.calls) == (100, 50, 1)
    assert u.total_tokens == 150
    # 100 * $1/1M + 50 * $2/1M
    assert u.cost_usd == 0.0002


def test_parses_alternate_field_names():
    """Some gateways report input_tokens/output_tokens instead."""
    u = ad.TokenUsage.from_response({"usage": {"input_tokens": 10, "output_tokens": 5}})
    assert (u.prompt_tokens, u.completion_tokens) == (10, 5)


def test_missing_usage_block_is_counted_as_a_call_with_zero_tokens():
    """A gateway that omits usage must not crash accounting, and the call still happened —
    losing the call count would hide that requests were made at all."""
    u = ad.TokenUsage.from_response({})
    assert (u.prompt_tokens, u.completion_tokens, u.calls) == (0, 0, 1)


def test_usage_adds():
    a = ad.TokenUsage(prompt_tokens=10, completion_tokens=5, calls=1, cost_usd=0.001)
    b = ad.TokenUsage(prompt_tokens=1, completion_tokens=2, calls=1, cost_usd=0.002)
    total = a + b
    assert (total.prompt_tokens, total.completion_tokens, total.calls) == (11, 7, 2)
    assert total.cost_usd == 0.003


def test_prices_are_optional_so_token_counts_survive_opaque_billing():
    u = ad.TokenUsage.from_response({"usage": {"prompt_tokens": 999, "completion_tokens": 1}})
    assert u.cost_usd == 0.0
    assert u.total_tokens == 1000


def test_client_accumulates_and_publishes_usage():
    seen: list[ad.TokenUsage] = []
    client = ad.OpenAICompatClient(
        base_url="http://example/v1", api_key="k", model="m",
        price_in=1.0, price_out=2.0, on_usage=seen.append,
    )
    client._record_usage({"usage": {"prompt_tokens": 1000, "completion_tokens": 500}})
    client._record_usage({"usage": {"prompt_tokens": 2000, "completion_tokens": 100}})
    assert client.usage_total.prompt_tokens == 3000
    assert client.usage_total.completion_tokens == 600
    assert client.usage_total.calls == 2
    assert len(seen) == 2, "the callback lets an external meter see spend as it happens"


def test_a_raising_callback_never_loses_a_completion():
    """The caller already paid for the tokens; a misbehaving meter must not discard them."""
    def boom(_usage: ad.TokenUsage) -> None:
        raise RuntimeError("meter exploded")

    client = ad.OpenAICompatClient(
        base_url="http://example/v1", api_key="k", model="m", on_usage=boom,
    )
    client._record_usage({"usage": {"prompt_tokens": 7, "completion_tokens": 3}})
    assert client.usage_total.total_tokens == 10


def test_existing_construction_sites_keep_working():
    """Backward compatibility: both new fields are defaulted, so third-party Attributors
    and any existing CompletionResult(...) call are unaffected."""
    assert ad.CompletionResult(text="x", raw={}).usage.total_tokens == 0
    assert ad.AttributionResult(method="m", hypotheses=[]).usage.calls == 0
    assert ad.OpenAICompatClient(
        base_url="http://example/v1", api_key="k", model="m"
    ).usage_total.calls == 0
