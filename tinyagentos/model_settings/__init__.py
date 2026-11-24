"""Model Settings Package - per-agent and per-chat sampling settings for the LLM gateway."""
from __future__ import annotations

from tinyagentos.model_settings.store import ModelSettingsStore
from tinyagentos.model_settings.resolver import (
    resolve_settings,
    apply_settings,
    ResolvedSettings,
    SettingResolution,
)
from tinyagentos.model_settings.capabilities import (
    PROVIDER_CAPABILITIES,
    MODEL_CAPABILITY_OVERRIDES,
    filter_capabilities,
    get_provider_for_route,
    get_supported_settings,
)
from tinyagentos.model_settings.turn_registry import (
    TurnRegistry,
    get_turn_registry,
    set_turn_registry,
)

__all__ = [
    "ModelSettingsStore",
    "resolve_settings",
    "apply_settings",
    "ResolvedSettings",
    "SettingResolution",
    "PROVIDER_CAPABILITIES",
    "MODEL_CAPABILITY_OVERRIDES",
    "filter_capabilities",
    "get_provider_for_route",
    "get_supported_settings",
    "TurnRegistry",
    "get_turn_registry",
    "set_turn_registry",
]