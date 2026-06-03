from __future__ import annotations
import re

class ThemeError(Exception):
    """Raised when a theme config is invalid or unsafe."""

_COLOR_TOKENS = {
    "--color-shell-bg", "--color-shell-bg-deep", "--color-shell-surface",
    "--color-shell-surface-hover", "--color-shell-surface-active",
    "--color-shell-border", "--color-shell-border-strong",
    "--color-shell-text", "--color-shell-text-secondary", "--color-shell-text-tertiary",
    "--color-traffic-close", "--color-traffic-minimize", "--color-traffic-maximize",
    "--color-accent", "--color-accent-glow",
    "--color-dock-bg", "--color-dock-border", "--color-topbar-bg",
    "--color-snap-preview", "--color-snap-border",
}
_LENGTH_TOKENS = {
    "--spacing-topbar-h", "--spacing-dock-h", "--spacing-dock-padding",
    "--spacing-window-radius", "--spacing-dock-radius",
}
_SHADOW_TOKENS = {
    "--shadow-window", "--shadow-window-unfocused", "--shadow-dock",
    "--shadow-card", "--shadow-card-hover",
}
_FONT_TOKENS = {"--font-ui", "--font-mono"}
_ALL_TOKENS = _COLOR_TOKENS | _LENGTH_TOKENS | _SHADOW_TOKENS | _FONT_TOKENS

_VARIANTS = {
    "dock": {"macos-dock", "windows-taskbar", "ubuntu-dock", "hidden"},
    "windowChrome": {"macos", "windows", "linux", "minimal"},
    "topBar": {"default", "hidden"},
    "launcher": {"default", "grid"},
}
_EFFECT_MODULES = {"crt", "scanlines", "glow", "cursor"}
_SAFETY_FLOOR = {"assistant", "launcher"}

_EFFECT_PARAMS = {"cursor": {"cursor"}, "crt": set(), "scanlines": set(), "glow": set()}
_CURSOR_VALUES = {"crosshair", "default", "none", "pointer", "text", "move", "grab",
                  "zoom-in", "zoom-out", "wait", "help", "not-allowed"}

_FORBIDDEN = re.compile(r"(url\s*\(|expression\s*\(|javascript:|</|<script|@import|;\s*}|\\)", re.I)

def _check_value(key: str, value) -> None:
    if not isinstance(value, str) or len(value) > 200:
        raise ThemeError(f"invalid token value for {key}")
    normalized = re.sub(r"/\*.*?\*/", "", value, flags=re.S)
    if _FORBIDDEN.search(normalized):
        raise ThemeError(f"invalid token value for {key}: forbidden content")

def validate_theme_config(cfg: dict) -> dict:
    if not isinstance(cfg, dict):
        raise ThemeError("theme config must be an object")
    tokens = cfg.get("tokens", {}) or {}
    if not isinstance(tokens, dict):
        raise ThemeError("tokens must be an object")
    for key, value in tokens.items():
        if key not in _ALL_TOKENS:
            raise ThemeError(f"unknown token key: {key}")
        _check_value(key, value)

    structure = cfg.get("structure", {}) or {}
    if not isinstance(structure, dict):
        raise ThemeError("structure must be an object")
    for surface, conf in structure.items():
        if surface not in _VARIANTS:
            raise ThemeError(f"unknown structural surface: {surface}")
        variant = (conf or {}).get("variant")
        if variant is not None and variant not in _VARIANTS[surface]:
            raise ThemeError(f"unknown {surface} variant: {variant}")

    effects = cfg.get("effects", []) or []
    if not isinstance(effects, list):
        raise ThemeError("effects must be a list")
    for eff in effects:
        if not isinstance(eff, dict) or eff.get("module") not in _EFFECT_MODULES:
            raise ThemeError(f"unknown effect module: {eff!r}")
        module = eff["module"]
        params = eff.get("params") or {}
        if not isinstance(params, dict):
            raise ThemeError(f"params for {module} must be an object")
        for pk in params:
            if pk not in _EFFECT_PARAMS.get(module, set()):
                raise ThemeError(f"unknown param {pk!r} for effect {module}")
        if module == "cursor":
            cv = params.get("cursor", "crosshair")
            if cv not in _CURSOR_VALUES:
                raise ThemeError(f"unsafe cursor value: {cv!r}")

    requires = set(cfg.get("requires", []) or []) | _SAFETY_FLOOR
    out = dict(cfg)
    out["tokens"] = tokens
    out["structure"] = structure
    out["effects"] = effects
    out["requires"] = sorted(requires)
    return out

def theme_vocabulary() -> dict:
    """Machine-readable vocabulary for the agent's get_theme_schema tool."""
    return {
        "tokens": sorted(_ALL_TOKENS),
        "structure": {k: sorted(v) for k, v in _VARIANTS.items()},
        "effects": sorted(_EFFECT_MODULES),
        "safety_floor": sorted(_SAFETY_FLOOR),
        "asset_limits": {"max_bytes": 5 * 1024 * 1024, "image": ["png", "jpg", "webp"], "font": ["woff2", "ttf"]},
    }
