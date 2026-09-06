import pytest
from tinyagentos.themes.schema import validate_theme_config, ThemeError

def test_schema_tool_lists_vocabulary():
    from tinyagentos.themes.schema import theme_vocabulary
    v = theme_vocabulary()
    assert "--color-accent" in v["tokens"]
    assert "windows-taskbar" in v["structure"]["dock"]
    assert "crt" in v["effects"]

def test_create_theme_validates():
    with pytest.raises(ThemeError):
        validate_theme_config({"tokens": {"--evil": "x"}})
