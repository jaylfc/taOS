"""Provider capability definitions and filtering."""
from __future__ import annotations

from typing import Any


# Provider capabilities: setting -> {min, max, allowed}
# None for min/max means no limit
# allowed is a list of allowed values (for enums like reasoning_effort)
PROVIDER_CAPABILITIES: dict[str, dict[str, dict]] = {
    "openai": {
        "temperature": {"min": 0.0, "max": 2.0},
        "top_p": {"min": 0.0, "max": 1.0},
        "max_output_tokens": {"min": 1, "max": 32768},
        "presence_penalty": {"min": -2.0, "max": 2.0},
        "frequency_penalty": {"min": -2.0, "max": 2.0},
        "seed": {"min": 0, "max": 2147483647},
        "reasoning_effort": {"allowed": ["low", "medium", "high"]},
        "stop_sequences": {"type": "array", "items": "string"},
        "system_prompt_addition": {"type": "string"},
    },
    "openrouter": {
        "temperature": {"min": 0.0, "max": 2.0},
        "top_p": {"min": 0.0, "max": 1.0},
        "max_output_tokens": {"min": 1, "max": 32768},
        "presence_penalty": {"min": -2.0, "max": 2.0},
        "frequency_penalty": {"min": -2.0, "max": 2.0},
        "seed": {"min": 0, "max": 2147483647},
        "reasoning_effort": {"allowed": ["low", "medium", "high"]},
        "stop_sequences": {"type": "array", "items": "string"},
        "system_prompt_addition": {"type": "string"},
    },
    "deepseek": {
        "temperature": {"min": 0.0, "max": 2.0},
        "top_p": {"min": 0.0, "max": 1.0},
        "max_output_tokens": {"min": 1, "max": 32768},
        "presence_penalty": {"min": -2.0, "max": 2.0},
        "frequency_penalty": {"min": -2.0, "max": 2.0},
        "seed": {"min": 0, "max": 2147483647},
        "reasoning_effort": {"allowed": ["low", "medium", "high"]},
        "stop_sequences": {"type": "array", "items": "string"},
        "system_prompt_addition": {"type": "string"},
    },
    "anthropic": {
        "temperature": {"min": 0.0, "max": 1.0},
        "top_p": {"min": 0.0, "max": 1.0},
        "top_k": {"min": 1, "max": 100},
        "max_output_tokens": {"min": 1, "max": 8192},
        "stop_sequences": {"type": "array", "items": "string"},
        "system_prompt_addition": {"type": "string"},
    },
    "ollama": {
        "temperature": {"min": 0.0, "max": 2.0},
        "top_p": {"min": 0.0, "max": 1.0},
        "top_k": {"min": 1, "max": 100},
        "min_p": {"min": 0.0, "max": 1.0},
        "max_output_tokens": {"min": 1, "max": 32768},
        "repetition_penalty": {"min": 0.0, "max": 2.0},
        "presence_penalty": {"min": -2.0, "max": 2.0},
        "seed": {"min": 0, "max": 2147483647},
        "stop_sequences": {"type": "array", "items": "string"},
        "system_prompt_addition": {"type": "string"},
        "reasoning_effort": {"allowed": ["low", "medium", "high", "off"]},
    },
}


# Per-model capability overrides (for reasoning support etc.)
MODEL_CAPABILITY_OVERRIDES: dict[str, dict[str, dict]] = {
    "claude-3-7-sonnet": {
        "reasoning_effort": {"allowed": ["low", "medium", "high", "off"]},
    },
    "claude-3-5-sonnet": {
        "reasoning_effort": {"allowed": ["low", "medium", "high", "off"]},
    },
    "o1": {
        "reasoning_effort": {"allowed": ["low", "medium", "high"]},
    },
    "o1-mini": {
        "reasoning_effort": {"allowed": ["low", "medium", "high"]},
    },
    "o3-mini": {
        "reasoning_effort": {"allowed": ["low", "medium", "high"]},
    },
}


# Wire (upstream request-body) name for each internal setting, per provider.
# None means the provider does not accept the setting under any name: it is
# dropped from the outgoing body and noted in x-taos-settings-dropped.
# system_prompt_addition is None everywhere because it is applied to the
# messages list, not to a top-level body parameter.
PROVIDER_WIRE_NAMES: dict[str, dict[str, str | None]] = {
    "openai": {
        "temperature": "temperature",
        "top_p": "top_p",
        "max_output_tokens": "max_tokens",
        "presence_penalty": "presence_penalty",
        "frequency_penalty": "frequency_penalty",
        "seed": "seed",
        "reasoning_effort": "reasoning_effort",
        "stop_sequences": "stop",
        "top_k": None,
        "min_p": None,
        "repetition_penalty": None,
        "system_prompt_addition": None,
    },
    "anthropic": {
        "temperature": "temperature",
        "top_p": "top_p",
        "top_k": "top_k",
        "max_output_tokens": "max_tokens",
        "stop_sequences": "stop_sequences",
        "min_p": None,
        "reasoning_effort": None,
        "repetition_penalty": None,
        "presence_penalty": None,
        "seed": None,
        "system_prompt_addition": None,
    },
    "ollama": {
        "temperature": "temperature",
        "top_p": "top_p",
        "top_k": "top_k",
        "min_p": "min_p",
        "max_output_tokens": "num_predict",
        "repetition_penalty": "repeat_penalty",
        "presence_penalty": "presence_penalty",
        "seed": "seed",
        "stop_sequences": "stop",
        "reasoning_effort": None,
        "system_prompt_addition": None,
    },
}
# OpenAI-shaped upstreams share the OpenAI wire names.
PROVIDER_WIRE_NAMES["openrouter"] = dict(PROVIDER_WIRE_NAMES["openai"])
PROVIDER_WIRE_NAMES["deepseek"] = dict(PROVIDER_WIRE_NAMES["openai"])

# Backend types that speak the OpenAI wire format (provider "openai") but also
# accept the ollama-family sampling parameters, so they are classified by
# backend_type instead of losing top_k / min_p / repetition_penalty to the
# openai capability table.
BACKEND_TYPE_SETTINGS: dict[str, list[str]] = {
    "llama-cpp": ["top_k", "min_p", "repetition_penalty", "presence_penalty"],
    "llama.cpp": ["top_k", "min_p", "repetition_penalty", "presence_penalty"],
    "vllm": ["top_k", "min_p", "repetition_penalty", "presence_penalty"],
    "llama-swap": ["top_k", "min_p", "repetition_penalty", "presence_penalty"],
}


def get_provider_for_route(route: Any) -> str:
    """Get the provider key for capability lookup from a Route."""
    provider = getattr(route, "provider", "")
    backend_type = getattr(route, "backend_type", "")

    # Map backend types to provider keys
    if provider == "anthropic":
        return "anthropic"
    if provider in ("openai", "openrouter", "deepseek"):
        return provider
    if provider in ("ollama", "ollama_chat") or backend_type in ("ollama", "rkllama", "hailo-ollama"):
        return "ollama"
    if backend_type in BACKEND_TYPE_SETTINGS:
        # OpenAI wire format, ollama-family sampling parameters.
        return "openai"

    # Default to openai-compatible
    return "openai"


def _check_value(setting: str, value: Any, cap: dict) -> None:
    """Check if a value is within the capability constraints. Raises ValueError if not."""
    # Type check
    if "type" in cap:
        if cap["type"] == "array" and not isinstance(value, list):
            raise ValueError(f"{setting} must be an array")
        if cap["type"] == "string" and not isinstance(value, str):
            raise ValueError(f"{setting} must be a string")
    
    # Range check for numbers
    if "min" in cap and isinstance(value, (int, float)):
        if value < cap["min"]:
            raise ValueError(f"{setting}={value} below minimum {cap['min']}")
    if "max" in cap and isinstance(value, (int, float)):
        if value > cap["max"]:
            raise ValueError(f"{setting}={value} above maximum {cap['max']}")
    
    # Allowed values check (enum)
    if "allowed" in cap:
        if value not in cap["allowed"]:
            raise ValueError(f"{setting}={value!r} not in allowed values {cap['allowed']}")


def _all_caps(provider: str, model: str, route: Any = None) -> dict[str, dict]:
    """Merged capability table for a provider/model, plus settings a
    backend type accepts even though the provider table omits them."""
    provider_caps = PROVIDER_CAPABILITIES.get(provider, {})
    model_caps = MODEL_CAPABILITY_OVERRIDES.get(model, {})
    all_caps = {**provider_caps, **model_caps}
    if route is not None:
        backend_type = getattr(route, "backend_type", "")
        for setting in BACKEND_TYPE_SETTINGS.get(backend_type, []):
            all_caps.setdefault(setting, {})
    return all_caps


def filter_capabilities(
    settings: dict[str, Any],
    provider: str,
    model: str,
    route: Any = None,
) -> tuple[dict[str, Any], dict[str, dict]]:
    """Filter settings by provider/model capabilities.
    
    Returns (filtered_settings, dropped_settings_with_reasons).
    Raises ValueError if a value is out of range.
    """
    all_caps = _all_caps(provider, model, route)
    
    filtered = {}
    dropped = {}
    
    for setting, value in settings.items():
        if value is None:
            continue
        
        cap = all_caps.get(setting)
        if cap is None:
            # Setting not supported by this provider
            dropped[setting] = {"reason": "unsupported_by_provider"}
            continue
        
        # Check value constraints
        try:
            _check_value(setting, value, cap)
            filtered[setting] = value
        except ValueError as e:
            # Re-raise with context
            raise ValueError(f"{setting}: {e}")
    
    return filtered, dropped


def get_supported_settings(provider: str, model: str = "") -> list[str]:
    """Get list of settings supported by a provider/model."""
    provider_caps = PROVIDER_CAPABILITIES.get(provider, {})
    model_caps = MODEL_CAPABILITY_OVERRIDES.get(model, {})
    return list({**provider_caps, **model_caps}.keys())