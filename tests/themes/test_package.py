import io, zipfile, yaml
import pytest
from tinyagentos.themes.package import extract_theme_package, ThemePackageError

def _zip(manifest: dict, extra: dict | None = None):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("theme.yaml", yaml.safe_dump(manifest))
        for name, data in (extra or {}).items():
            z.writestr(name, data)
    return buf.getvalue()

MANIFEST = {
    "id": "matrix-terminal", "name": "Matrix Terminal", "version": "1.0.0",
    "tokens": {"--color-shell-bg": "#000000", "--color-accent": "#00ff46"},
    "structure": {"dock": {"variant": "windows-taskbar"}},
    "effects": [{"module": "crt", "params": {}}],
    "requires": ["assistant", "launcher"],
}

def test_extract_valid_theme(tmp_path):
    m = extract_theme_package(_zip(MANIFEST, {"assets/wall.png": "x"}), themes_root=tmp_path)
    assert m["id"] == "matrix-terminal"
    assert (tmp_path / "matrix-terminal" / "assets" / "wall.png").exists()

def test_invalid_config_rejected(tmp_path):
    bad = dict(MANIFEST); bad["tokens"] = {"--evil": "x"}
    with pytest.raises(ThemePackageError):
        extract_theme_package(_zip(bad), themes_root=tmp_path)

def test_path_traversal_blocked(tmp_path):
    with pytest.raises(ThemePackageError):
        extract_theme_package(_zip(MANIFEST, {"../../../etc/x": "y"}), themes_root=tmp_path)
