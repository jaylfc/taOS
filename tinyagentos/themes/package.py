from __future__ import annotations
import io, zipfile
from pathlib import Path
import yaml
from tinyagentos.themes.schema import validate_theme_config, ThemeError

class ThemePackageError(Exception):
    pass

_REQUIRED = ("id", "name", "version")

def parse_theme_manifest(text: str) -> dict:
    try:
        data = yaml.safe_load(text) or {}
    except yaml.YAMLError as exc:
        raise ThemePackageError(f"invalid theme.yaml: {exc}") from exc
    for key in _REQUIRED:
        if not data.get(key):
            raise ThemePackageError(f"theme manifest missing required field: {key}")
    try:
        validated = validate_theme_config(data)
    except ThemeError as exc:
        raise ThemePackageError(str(exc)) from exc
    data.update(validated)
    return data

def extract_theme_package(data: bytes, themes_root: Path) -> dict:
    try:
        zf = zipfile.ZipFile(io.BytesIO(data))
    except zipfile.BadZipFile as exc:
        raise ThemePackageError("not a valid .taostheme (zip) archive") from exc
    try:
        manifest = parse_theme_manifest(zf.read("theme.yaml").decode("utf-8"))
    except KeyError as exc:
        raise ThemePackageError("theme.yaml missing from package") from exc

    themes_root = Path(themes_root).resolve()
    theme_dir = (themes_root / manifest["id"]).resolve()
    if not str(theme_dir).startswith(str(themes_root) + "/"):
        raise ThemePackageError(f"unsafe theme id: {manifest['id']!r}")
    theme_dir.mkdir(parents=True, exist_ok=True)
    for member in zf.namelist():
        if member.endswith("/"):
            continue
        dest = (theme_dir / member).resolve()
        if not str(dest).startswith(str(theme_dir) + "/") and dest != theme_dir:
            raise ThemePackageError(f"unsafe path in package: {member}")
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(zf.read(member))
    return manifest
