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
        "seed": {"min": 0, "max": 2147483647},
        "stop_sequences": {"type": "array", "items": "string"},
        "system_prompt_addition": {"type": "string"},
    },
    "openrouter": {
        "temperature": {"min": 0.0, "max": 2.0},
        "top_p": {"min": 0.0, "max": 1.0},
        "max_output_tokens": {"min": 1, "max": 32768},
        "seed": {"min": 0, "max": 2147483647},
        "stop_sequences": {"type": "array", "items": "string"},
        "system_prompt_addition": {"type": "string"},
    },
    "deepseek": {
        "temperature": {"min": 0.0, "max": 2.0},
        "top_p": {"min": 0.0, "max": 1.0},
        "max_output_tokens": {"min": 1, "max": 32768},
        "seed": {"min": 0, "max": 2147483647},
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


def filter_capabilities(
    settings: dict[str, Any],
    provider: str,
    model: str,
) -> tuple[dict[str, Any], dict[str, dict]]:
    """Filter settings by provider/model capabilities.
    
    Returns (filtered_settings, dropped_settings_with_reasons).
    Raises ValueError if a value is out of range.
    """
    # Get provider capabilities
    provider_caps = PROVIDER_CAPABILITIES.get(provider, {})
    
    # Get model-specific overrides
    model_caps = MODEL_CAPABILITY_OVERRIDES.get(model, {})
    
    # Merge: model caps override provider caps
    all_caps = {**provider_caps, **model_caps}
    
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