import pytest
from tinyagentos.themes.schema import validate_theme_config, ThemeError

def _base():
    return {"tokens": {}, "structure": {}, "effects": [], "requires": ["assistant", "launcher"]}

def test_valid_minimal_config_passes():
    cfg = _base()
    cfg["tokens"] = {"--color-shell-bg": "#000000", "--color-accent": "rgb(0,255,70)"}
    out = validate_theme_config(cfg)
    assert out["tokens"]["--color-shell-bg"] == "#000000"

def test_unknown_token_key_rejected():
    cfg = _base(); cfg["tokens"] = {"--evil-key": "#000"}
    with pytest.raises(ThemeError, match="unknown token"):
        validate_theme_config(cfg)

def test_token_value_code_injection_rejected():
    cfg = _base(); cfg["tokens"] = {"--color-shell-bg": "url(javascript:alert(1))"}
    with pytest.raises(ThemeError, match="invalid token value"):
        validate_theme_config(cfg)

def test_unknown_structural_variant_rejected():
    cfg = _base(); cfg["structure"] = {"dock": {"variant": "evil-dock"}}
    with pytest.raises(ThemeError, match="unknown .*variant"):
        validate_theme_config(cfg)

def test_unknown_effect_module_rejected():
    cfg = _base(); cfg["effects"] = [{"module": "mine-bitcoin", "params": {}}]
    with pytest.raises(ThemeError, match="unknown effect"):
        validate_theme_config(cfg)

def test_missing_safety_floor_requirement_injected():
    cfg = _base(); cfg["requires"] = []
    out = validate_theme_config(cfg)
    assert "assistant" in out["requires"] and "launcher" in out["requires"]
