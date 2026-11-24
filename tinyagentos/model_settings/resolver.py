"""Model Settings Resolver - resolves settings with precedence and capability filtering."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from tinyagentos.llm_gateway.resolve import Route
from tinyagentos.model_settings.store import ModelSettingsStore
from tinyagentos.model_settings.capabilities import filter_capabilities, get_provider_for_route


@dataclass
class SettingResolution:
    """Resolution result for a single setting."""
    value: Any
    source: str | None  # "model", "agent", "chat", "request", or None
    forced: bool
    dropped_reason: str | None = None


@dataclass
class ResolvedSettings:
    """All resolved settings for a request."""
    temperature: SettingResolution = field(default_factory=lambda: SettingResolution(None, None, False))
    top_p: SettingResolution = field(default_factory=lambda: SettingResolution(None, None, False))
    top_k: SettingResolution = field(default_factory=lambda: SettingResolution(None, None, False))
    min_p: SettingResolution = field(default_factory=lambda: SettingResolution(None, None, False))
    max_output_tokens: SettingResolution = field(default_factory=lambda: SettingResolution(None, None, False))
    reasoning_effort: SettingResolution = field(default_factory=lambda: SettingResolution(None, None, False))
    repetition_penalty: SettingResolution = field(default_factory=lambda: SettingResolution(None, None, False))
    presence_penalty: SettingResolution = field(default_factory=lambda: SettingResolution(None, None, False))
    seed: SettingResolution = field(default_factory=lambda: SettingResolution(None, None, False))
    stop_sequences: SettingResolution = field(default_factory=lambda: SettingResolution(None, None, False))
    system_prompt_addition: SettingResolution = field(default_factory=lambda: SettingResolution(None, None, False))
    
    # Metadata
    dropped_settings: dict[str, str] = field(default_factory=dict)  # setting -> reason
    
    def to_dict(self) -> dict[str, Any]:
        """Convert to dict for upstream body."""
        result = {}
        for setting_name in [
            "temperature", "top_p", "top_k", "min_p", "max_output_tokens",
            "reasoning_effort", "repetition_penalty", "presence_penalty",
            "seed", "stop_sequences", "system_prompt_addition"
        ]:
            resolution = getattr(self, setting_name)
            if resolution.value is not None:
                result[setting_name] = resolution.value
        return result
    
    def get_dropped_header_value(self) -> str:
        """Format dropped settings for x-taos-settings-dropped header."""
        if not self.dropped_settings:
            return ""
        return ",".join(f"{k}:{v}" for k, v in self.dropped_settings.items())


async def _resolve_single_setting(
    store: ModelSettingsStore,
    caller_id: str,
    model_name: str,
    chat_id: str | None,
    setting: str,
    request_value: Any,
    route: Route,
) -> SettingResolution:
    """Resolve a single setting through the precedence chain."""
    
    # Level 1: Model default (lowest)
    model_default = await store.get_model_default(model_name, setting)
    
    # Level 2: Agent (both forced and default)
    agent_forced = await store._get_raw_setting("agent", caller_id, setting, "forced")
    agent_default = await store._get_raw_setting("agent", caller_id, setting, "default")
    
    # Level 3: Chat (if chat_id provided)
    chat_forced = None
    chat_default = None
    if chat_id:
        chat_forced = await store._get_raw_setting("chat", chat_id, setting, "forced")
        chat_default = await store._get_raw_setting("chat", chat_id, setting, "default")
    
    # Level 4: Request (explicit value in body)
    has_request_value = request_value is not None
    
    # Resolution logic:
    # 1. Highest forced entry wins
    # 2. Request explicit value
    # 3. Highest default entry
    # 4. Model default
    # 5. Unset
    
    # Check forced entries (highest level first)
    for level, forced_val in [
        ("chat", chat_forced),
        ("agent", agent_forced),
        ("model", None),  # Model level doesn't have forced mode
    ]:
        if forced_val is not None:
            return SettingResolution(
                value=forced_val,
                source=level,
                forced=True,
                dropped_reason=f"forced {level} setting overrides request" if has_request_value else None,
            )
    
    # Request explicit value
    if has_request_value:
        return SettingResolution(
            value=request_value,
            source="request",
            forced=False,
        )
    
    # Check default entries (highest level first)
    for level, default_val in [
        ("chat", chat_default),
        ("agent", agent_default),
        ("model", model_default),
    ]:
        if default_val is not None:
            return SettingResolution(
                value=default_val,
                source=level,
                forced=False,
            )
    
    # Unset
    return SettingResolution(
        value=None,
        source=None,
        forced=False,
    )


async def resolve_settings(
    store: ModelSettingsStore,
    caller_id: str,
    model_name: str,
    chat_id: str | None,
    request_body: dict,
    route: Route,
) -> ResolvedSettings:
    """Resolve all settings for a gateway request.
    
    Precedence (lowest to highest):
    1. Model default (admin-editable)
    2. Agent (default)
    3. Chat/session (default)
    4. Request explicit value
    
    Forced mode overrides everything at that level and below.
    """
    result = ResolvedSettings()
    provider = get_provider_for_route(route)
    
    # Collect all setting names
    setting_names = [
        "temperature", "top_p", "top_k", "min_p", "max_output_tokens",
        "reasoning_effort", "repetition_penalty", "presence_penalty",
        "seed", "stop_sequences", "system_prompt_addition"
    ]
    
    # First pass: resolve each setting through precedence
    raw_resolutions = {}
    for setting in setting_names:
        request_value = request_body.get(setting)
        resolution = await _resolve_single_setting(
            store, caller_id, model_name, chat_id, setting, request_value, route
        )
        raw_resolutions[setting] = resolution
        setattr(result, setting, resolution)
    
    # Second pass: capability filtering
    # Build the dict of values that would be sent
    values_to_send = {k: v.value for k, v in raw_resolutions.items() if v.value is not None}
    
    # Filter by provider capabilities
    try:
        filtered, dropped = filter_capabilities(values_to_send, provider, route.upstream_model)
    except ValueError as e:
        # Value out of range - reject the request
        raise ValueError(f"Setting value rejected: {e}")
    
    # Update resolutions with filtered values and dropped reasons
    for setting in setting_names:
        resolution = raw_resolutions[setting]
        if setting in filtered:
            # Value is supported and in range
            resolution.value = filtered[setting]
        elif setting in dropped:
            # Value was dropped due to capability filtering
            result.dropped_settings[setting] = dropped[setting]["reason"]
            resolution.dropped_reason = dropped[setting]["reason"]
            resolution.value = None  # Don't send
        # If not in either, value was None anyway
    
    return result


def apply_settings(
    body: dict,
    resolved: ResolvedSettings,
    route: Route,
) -> tuple[dict, str | None]:
    """Apply resolved settings to a request body.
    
    Returns the modified body and the x-taos-settings-dropped header value (or None).
    """
    new_body = dict(body)
    
    # Apply each setting that has a value
    for setting_name in [
        "temperature", "top_p", "top_k", "min_p", "max_output_tokens",
        "reasoning_effort", "repetition_penalty", "presence_penalty",
        "seed", "stop_sequences"
    ]:
        resolution = getattr(resolved, setting_name)
        if resolution.value is not None:
            new_body[setting_name] = resolution.value
    
    # Handle system_prompt_addition specially - append to system message
    sys_addition = getattr(resolved, "system_prompt_addition")
    if sys_addition.value:
        # Find or create system message
        system_idx = None
        for i, msg in enumerate(new_body.get("messages", [])):
            if msg.get("role") == "system":
                system_idx = i
                break
        
        if system_idx is not None:
            # Append to existing system message
            current = new_body["messages"][system_idx].get("content", "")
            if current:
                new_body["messages"][system_idx]["content"] = current + "\n\n" + sys_addition.value
            else:
                new_body["messages"][system_idx]["content"] = sys_addition.value
        else:
            # Prepend system message
            new_body["messages"].insert(0, {"role": "system", "content": sys_addition.value})
    
    dropped_header = resolved.get_dropped_header_value()
    return new_body, dropped_header if dropped_header else None