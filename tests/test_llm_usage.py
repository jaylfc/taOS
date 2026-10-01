"""tinyagentos.llm_usage: usage extraction per wire format, and cost with exact LiteLLM parity."""
from __future__ import annotations

import math

import pytest

from tinyagentos.llm_usage import (
    AnthropicStreamUsage,
    OllamaStreamUsage,
    OpenAIStreamUsage,
    Usage,
    cost_of,
    ensure_stream_usage,
    find_price,
    from_anthropic,
    from_ollama,
    from_openai,
    price_source,
    price_usage,
)
from tinyagentos.llm_usage.pricing import price_table

# ---- extraction ----------------------------------------------------------------------------------

def test_openai_usage_with_cache_and_reasoning():
    u = from_openai({"usage": {"prompt_tokens": 1000, "completion_tokens": 300,
                               "prompt_tokens_details": {"cached_tokens": 400},
                               "completion_tokens_details": {"reasoning_tokens": 120}}})
    assert u == Usage(input_tokens=1000, output_tokens=300, cache_read_tokens=400, reasoning_tokens=120)
    assert u.uncached_input_tokens == 600


def test_deepseek_cache_hit_key_is_read():
    u = from_openai({"usage": {"prompt_tokens": 500, "completion_tokens": 10, "prompt_cache_hit_tokens": 200}})
    assert u.cache_read_tokens == 200


def test_anthropic_input_excludes_cache_so_it_is_folded_into_the_total():
    u = from_anthropic({"usage": {"input_tokens": 504, "output_tokens": 97,
                                  "cache_creation_input_tokens": 123, "cache_read_input_tokens": 40}})
    assert u.input_tokens == 504 + 123 + 40
    assert (u.cache_write_tokens, u.cache_read_tokens, u.uncached_input_tokens) == (123, 40, 504)


def test_the_same_call_in_both_cache_conventions_normalises_identically():
    """OpenAI counts cache INSIDE prompt_tokens, Anthropic OUTSIDE input_tokens. One logical call
    reported both ways must produce the same Usage, or one of them double- or under-counts."""
    openai_style = from_openai({"usage": {"prompt_tokens": 1000, "completion_tokens": 50,
                                          "prompt_tokens_details": {"cached_tokens": 700}}})
    anthropic_style = from_anthropic({"usage": {"input_tokens": 300, "output_tokens": 50,
                                                "cache_read_input_tokens": 700}})
    assert openai_style == anthropic_style


def test_ollama_native_counts():
    assert from_ollama({"done": True, "prompt_eval_count": 26, "eval_count": 298}) == Usage(26, 298)


@pytest.mark.parametrize("extract,body", [
    (from_openai, {"id": "x", "choices": []}),
    (from_openai, {"usage": None}),
    (from_anthropic, {"content": []}),
    (from_ollama, {"message": {"content": "hi"}, "done": True}),
    (from_openai, "not a dict"),
])
def test_missing_usage_is_unknown_not_zero(extract, body):
    u = extract(body)
    assert u.source == "unknown" and not u.known


# ---- streams -------------------------------------------------------------------------------------

def test_ensure_stream_usage_asks_for_usage_without_mutating_the_request():
    req = {"model": "m", "stream": True, "stream_options": {"foo": 1}}
    out = ensure_stream_usage(req)
    assert out["stream_options"] == {"foo": 1, "include_usage": True}
    assert req["stream_options"] == {"foo": 1}, "the caller's request must not be mutated"
    assert "stream_options" not in ensure_stream_usage({"model": "m"}), "non-streams are left alone"


def test_openai_stream_takes_the_final_usage_chunk():
    s = OpenAIStreamUsage()
    for chunk in [{"choices": [{"delta": {"content": "Hel"}}]},
                  {"choices": [{"delta": {"content": "lo"}}], "usage": None},
                  {"choices": [], "usage": {"prompt_tokens": 12, "completion_tokens": 2}}]:
        s.feed(chunk)
    assert s.result() == Usage(12, 2)


def test_openai_stream_without_a_usage_chunk_is_unknown():
    """What a stream looks like when include_usage was NOT requested: no usage anywhere."""
    s = OpenAIStreamUsage()
    s.feed({"choices": [{"delta": {"content": "hi"}}]})
    assert not s.result().known


def test_anthropic_stream_start_plus_cumulative_deltas():
    s = AnthropicStreamUsage()
    s.feed({"type": "message_start", "message": {"usage": {"input_tokens": 25, "output_tokens": 1,
                                                           "cache_read_input_tokens": 100}}})
    s.feed({"type": "content_block_delta", "delta": {"text": "hi"}})
    s.feed({"type": "message_delta", "usage": {"output_tokens": 15}})
    s.feed({"type": "message_delta", "usage": {"output_tokens": 42}})   # cumulative, not additive
    assert s.result() == Usage(input_tokens=125, output_tokens=42, cache_read_tokens=100)


def test_anthropic_stream_with_no_usage_events_is_unknown():
    s = AnthropicStreamUsage()
    s.feed({"type": "content_block_delta", "delta": {"text": "hi"}})
    assert not s.result().known


def test_ollama_stream_reads_the_done_line_only():
    s = OllamaStreamUsage()
    s.feed({"message": {"content": "a"}, "done": False})
    s.feed({"done": True, "prompt_eval_count": 9, "eval_count": 4})
    assert s.result() == Usage(9, 4)


# ---- pricing -------------------------------------------------------------------------------------

def test_local_backends_are_genuinely_free():
    c = cost_of("llama-cpp", "qwen3-4b", Usage(1000, 500))
    assert (c.usd, c.priced) == (0.0, True)


def test_an_unpriced_cloud_model_is_None_never_zero():
    """The budget code only charges cost > 0. A missing price that read as 0.0 would silently switch
    budgets off for that model, so it must come back as None / priced=False."""
    c = cost_of("openai-compatible", "some-private-model-v9", Usage(1000, 500))
    assert c.usd is None and c.priced is False
    assert "no price" in c.reason


def test_unknown_usage_on_a_cloud_model_is_not_priced():
    c = cost_of("openai", "gpt-4o", Usage.unknown())
    assert c.usd is None and c.priced is False


def test_override_prices_an_endpoint_the_table_cannot_know():
    c = cost_of("openai-compatible", "in-house-7b", Usage(1_000_000, 1_000_000),
                override={"input_cost_per_token": 1e-7, "output_cost_per_token": 2e-7})
    assert c.priced and math.isclose(c.usd, 0.1 + 0.2)


def test_lookup_never_borrows_another_providers_price():
    assert find_price("openai", "claude-sonnet-4-5") is None, "an OpenAI backend must not price a Claude id"
    assert find_price("anthropic", "claude-sonnet-4-5")[0] == "claude-sonnet-4-5"


def test_gateway_backends_resolve_vendor_prefixed_ids():
    key, entry = find_price("kilocode", "anthropic/claude-sonnet-4-5")
    assert key == "claude-sonnet-4-5" and entry["litellm_provider"] == "anthropic"


def test_long_prompts_switch_to_the_context_tier():
    _, entry = find_price("anthropic", "claude-sonnet-4-5")
    small = price_usage(entry, Usage(200_000, 0))
    big = price_usage(entry, Usage(200_001, 0))
    assert math.isclose(small, 200_000 * entry["input_cost_per_token"])
    assert math.isclose(big, 200_001 * entry["input_cost_per_token_above_200k_tokens"])


def test_vendored_table_records_its_provenance_and_licence():
    src = price_source()
    assert src["repo"] == "BerriAI/litellm" and len(src["commit"]) == 40
    assert len(price_table()) > 1000
    from pathlib import Path
    import tinyagentos.llm_usage as pkg
    notice = (Path(pkg.__file__).parent / "data" / "NOTICE").read_text()
    assert "MIT License" in notice and "Berri AI" in notice


# Every reasoning-priced model we route to (OpenRouter's Gemini 3 family) happens to charge the SAME
# rate for reasoning as for output, so real data cannot tell "reasoning rate applied" from "ignored".
# A synthetic model with a DIFFERENT reasoning rate is the only case that can.
_SYNTH = {"input_cost_per_token": 1e-6, "output_cost_per_token": 2e-6,
          "output_cost_per_reasoning_token": 9e-6, "litellm_provider": "openai", "mode": "chat"}


def test_a_distinct_reasoning_rate_is_applied_to_reasoning_tokens_only():
    u = Usage(input_tokens=1_000, output_tokens=500, reasoning_tokens=200)
    assert math.isclose(price_usage(_SYNTH, u), 1_000 * 1e-6 + 300 * 2e-6 + 200 * 9e-6)


# ---- frozen LiteLLM parity: runs WITHOUT LiteLLM, and outlives its removal -----------------------
# LiteLLM is no longer a dependency (removal stage 2b-2a), so the live parity tests that called it are
# gone. These numbers were produced by LiteLLM's own `cost_per_token` (1.94.x) over synthetic entries
# that cover every branch of the formula. Synthetic on purpose: a price-table refresh can never break them. `no-cache-rate` pins LiteLLM's quirk that
# cached tokens cost $0 when a model has no cache rate.
GOLDEN_ENTRIES = {
    "cache+tier200k": {"input_cost_per_token": 3e-06, "output_cost_per_token": 1.5e-05,
                       "cache_read_input_token_cost": 3e-07, "cache_creation_input_token_cost": 3.75e-06,
                       "input_cost_per_token_above_200k_tokens": 6e-06,
                       "output_cost_per_token_above_200k_tokens": 2.25e-05,
                       "cache_read_input_token_cost_above_200k_tokens": 6e-07,
                       "cache_creation_input_token_cost_above_200k_tokens": 7.5e-06},
    "read-only-cache": {"input_cost_per_token": 2.5e-06, "output_cost_per_token": 1e-05,
                        "cache_read_input_token_cost": 1.25e-06},
    "reasoning-rate": {"input_cost_per_token": 1e-06, "output_cost_per_token": 2e-06,
                       "output_cost_per_reasoning_token": 9e-06},
    "no-cache-rate": {"input_cost_per_token": 1e-06, "output_cost_per_token": 2e-06},
    "two-tiers": {"input_cost_per_token": 1e-06, "output_cost_per_token": 4e-06,
                  "input_cost_per_token_above_200k_tokens": 2e-06, "output_cost_per_token_above_200k_tokens": 6e-06,
                  "input_cost_per_token_above_272k_tokens": 3e-06, "output_cost_per_token_above_272k_tokens": 8e-06},
}
# (entry, [input, output, cache_read, cache_write, reasoning], LiteLLM's cost)
GOLDEN = [
    ("cache+tier200k", [1000, 200, 0, 0, 0], 0.006),
    ("cache+tier200k", [5000, 700, 2000, 1000, 0], 0.02085),
    ("cache+tier200k", [250000, 4000, 10000, 5000, 0], 1.5435000000000003),
    ("cache+tier200k", [300000, 1000, 0, 0, 0], 1.8225),
    ("cache+tier200k", [2000, 1500, 0, 0, 1000], 0.028500000000000004),
    ("cache+tier200k", [8000, 100, 6000, 0, 0], 0.0093),
    ("read-only-cache", [1000, 200, 0, 0, 0], 0.0045000000000000005),
    ("read-only-cache", [5000, 700, 2000, 1000, 0], 0.014499999999999999),
    ("read-only-cache", [250000, 4000, 10000, 5000, 0], 0.64),
    ("read-only-cache", [300000, 1000, 0, 0, 0], 0.7600000000000001),
    ("read-only-cache", [2000, 1500, 0, 0, 1000], 0.02),
    ("read-only-cache", [8000, 100, 6000, 0, 0], 0.013500000000000002),
    ("reasoning-rate", [1000, 200, 0, 0, 0], 0.0014),
    ("reasoning-rate", [5000, 700, 2000, 1000, 0], 0.0034000000000000002),
    ("reasoning-rate", [250000, 4000, 10000, 5000, 0], 0.243),
    ("reasoning-rate", [300000, 1000, 0, 0, 0], 0.302),
    ("reasoning-rate", [2000, 1500, 0, 0, 1000], 0.012000000000000002),
    ("reasoning-rate", [8000, 100, 6000, 0, 0], 0.0022),
    ("no-cache-rate", [1000, 200, 0, 0, 0], 0.0014),
    ("no-cache-rate", [5000, 700, 2000, 1000, 0], 0.0034000000000000002),
    ("no-cache-rate", [250000, 4000, 10000, 5000, 0], 0.243),
    ("no-cache-rate", [300000, 1000, 0, 0, 0], 0.302),
    ("no-cache-rate", [2000, 1500, 0, 0, 1000], 0.005),
    ("no-cache-rate", [8000, 100, 6000, 0, 0], 0.0022),
    ("two-tiers", [1000, 200, 0, 0, 0], 0.0018),
    ("two-tiers", [5000, 700, 2000, 1000, 0], 0.0048000000000000004),
    ("two-tiers", [250000, 4000, 10000, 5000, 0], 0.494),
    ("two-tiers", [300000, 1000, 0, 0, 0], 0.908),
    ("two-tiers", [2000, 1500, 0, 0, 1000], 0.008),
    ("two-tiers", [8000, 100, 6000, 0, 0], 0.0024000000000000002),
]


@pytest.mark.parametrize("name,t,expected", GOLDEN, ids=[f"{g[0]}-{g[1][0]}-{g[1][4]}" for g in GOLDEN])
def test_cost_matches_frozen_litellm_output(name, t, expected):
    u = Usage(input_tokens=t[0], output_tokens=t[1], cache_read_tokens=t[2], cache_write_tokens=t[3],
              reasoning_tokens=t[4])
    assert math.isclose(price_usage(GOLDEN_ENTRIES[name], u), expected, rel_tol=1e-12, abs_tol=1e-15)
