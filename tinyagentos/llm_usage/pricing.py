"""Estimated cost of an LLM call, from a vendored price table.

The table is LiteLLM's `model_prices_and_context_window.json` (MIT, see data/NOTICE), vendored by
`scripts/update_model_prices.py` and never fetched at runtime. The formula mirrors LiteLLM's
`generic_cost_per_token` so that, while both paths run side by side, the new one can be held to EXACT
parity with the old; `tests/test_llm_usage.py` asserts that against LiteLLM itself.

Three outcomes, kept apart on purpose:

* a LOCAL backend (llama.cpp, Ollama, rkllama, ...) costs 0.0, and that is a real measurement;
* a cloud model found in the table gets a computed cost;
* anything else is `usd=None, priced=False` - NEVER 0.0. The budget code only charges `cost > 0`, so a
  missing price that read as zero would silently switch budgets off for that model.

These are estimates: provider pricing is not published in a machine-readable form, and even the
curated tables say so. The UI should label cost as estimated.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Mapping

from tinyagentos.llm_usage.usage import Usage

_DATA = Path(__file__).resolve().parent / "data" / "model_prices.json"

#: taOS backend types that run on hardware the user owns: genuinely free per token.
LOCAL_BACKENDS = frozenset({"llama-cpp", "vllm", "exo", "mlx", "ollama", "rkllama", "hailo-ollama"})

#: taOS backend type -> the provider name used by the price table.
PRICE_PROVIDER = {
    "openai": "openai",
    "anthropic": "anthropic",
    "deepseek": "deepseek",
    "openrouter": "openrouter",
}

_TIER = re.compile(r"^input_cost_per_token_above_(\d+)(k?)_tokens$")


@dataclass(frozen=True)
class Cost:
    usd: float | None
    priced: bool
    reason: str
    price_key: str | None = None


@lru_cache(maxsize=1)
def price_table() -> Mapping[str, Mapping[str, float | str]]:
    return json.loads(_DATA.read_text())["models"]


def price_source() -> Mapping[str, str]:
    """Where the vendored table came from (repo, path, upstream commit)."""
    return json.loads(_DATA.read_text())["_source"]


def find_price(backend_type: str, model: str) -> tuple[str, Mapping[str, float | str]] | None:
    """Look a model up. Exact keys only: guessing a DIFFERENT model's price is worse than 'unpriced'."""
    table = price_table()
    provider = PRICE_PROVIDER.get(backend_type)
    model = (model or "").strip()
    if not model:
        return None
    candidates: list[tuple[str, str | None]] = []
    if provider:
        candidates.append((f"{provider}/{model}", provider))
        candidates.append((model, provider))
        if model.startswith(provider + "/"):
            candidates.append((model[len(provider) + 1:], provider))
    else:
        # A gateway backend (kilocode, nous, openai-compatible) that names models "vendor/model".
        candidates.append((model, None))
        if "/" in model:
            vendor, rest = model.split("/", 1)
            candidates.append((rest, vendor))
    for key, want in candidates:
        entry = table.get(key)
        if entry is None:
            continue
        if want is not None and entry.get("litellm_provider") != want:
            continue
        return key, entry
    return None


def _rate(entry: Mapping, key: str, default: float = 0.0) -> float:
    value = entry.get(key)
    return float(value) if isinstance(value, (int, float)) else default


def _tier_suffix(entry: Mapping, prompt_tokens: int) -> str | None:
    """The highest `_above_<N>_tokens` tier this prompt exceeds, as LiteLLM picks it."""
    tiers = []
    for key in entry:
        m = _TIER.match(key)
        if m and isinstance(entry[key], (int, float)):
            n = int(m.group(1)) * (1000 if m.group(2) else 1)
            tiers.append((n, f"{m.group(1)}{m.group(2)}"))
    for threshold, label in sorted(tiers, reverse=True):
        if prompt_tokens > threshold:
            return label
    return None


def price_usage(entry: Mapping, usage: Usage) -> float:
    """LiteLLM's generic_cost_per_token for text usage. A missing rate counts as 0.0, exactly as there.

    Note that means cached tokens are billed at $0 for a model with no cache rate in the table - an
    underestimate inherited from LiteLLM, kept for parity during the side-by-side run.
    """
    in_rate = _rate(entry, "input_cost_per_token")
    out_rate = _rate(entry, "output_cost_per_token")
    read_rate = _rate(entry, "cache_read_input_token_cost")
    write_rate = _rate(entry, "cache_creation_input_token_cost")
    tier = _tier_suffix(entry, usage.input_tokens)
    if tier:
        in_rate = _rate(entry, f"input_cost_per_token_above_{tier}_tokens", in_rate)
        out_rate = _rate(entry, f"output_cost_per_token_above_{tier}_tokens", out_rate)
        read_rate = _rate(entry, f"cache_read_input_token_cost_above_{tier}_tokens", read_rate)
        write_rate = _rate(entry, f"cache_creation_input_token_cost_above_{tier}_tokens", write_rate)
    cost = (usage.uncached_input_tokens * in_rate
            + usage.cache_read_tokens * read_rate
            + usage.cache_write_tokens * write_rate)
    if usage.reasoning_tokens > 0:
        text_out = max(0, usage.output_tokens - usage.reasoning_tokens)
        cost += text_out * out_rate
        cost += usage.reasoning_tokens * _rate(entry, "output_cost_per_reasoning_token", out_rate)
    else:
        cost += usage.output_tokens * out_rate
    return cost


def cost_of(backend_type: str, model: str, usage: Usage,
            override: Mapping[str, float] | None = None) -> Cost:
    """The estimated cost of one call.

    `override` is a per-backend price (same field names as the table), for endpoints the table cannot
    know - a custom openai-compatible server, a private deployment. It wins over the table.
    """
    if backend_type in LOCAL_BACKENDS and not override:
        return Cost(0.0, True, "local backend")
    if not usage.known:
        return Cost(None, False, "usage not reported by the backend")
    if override:
        return Cost(price_usage(override, usage), True, "per-backend price override")
    found = find_price(backend_type, model)
    if found is None:
        return Cost(None, False, f"no price for {backend_type}:{model}")
    key, entry = found
    return Cost(price_usage(entry, usage), True, "price table", key)
