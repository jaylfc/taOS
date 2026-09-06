from pathlib import Path
import yaml
import pytest

from tinyagentos.projects.folders import (
    project_dir,
    ensure_project_layout,
    write_project_yaml,
    read_project_yaml,
)


def test_project_dir_uses_root_and_slug(tmp_path):
    p = project_dir(tmp_path, "my-slug")
    assert p == tmp_path / "my-slug"


def test_ensure_project_layout_creates_subdirs(tmp_path):
    root = ensure_project_layout(tmp_path, "alpha")
    for sub in ("memory", "canvas", "files"):
        assert (root / sub).is_dir(), sub
    assert (root / "README.md").exists()


def test_write_and_read_project_yaml(tmp_path):
    ensure_project_layout(tmp_path, "alpha")
    payload = {
        "id": "prj-aaa",
        "slug": "alpha",
        "name": "Alpha",
        "members": [{"member_id": "agent-1", "member_kind": "native"}],
    }
    write_project_yaml(tmp_path, "alpha", payload)
    again = read_project_yaml(tmp_path, "alpha")
    assert again == payload
    raw = yaml.safe_load((tmp_path / "alpha" / "project.yaml").read_text())
    assert raw["id"] == "prj-aaa"


def test_read_missing_yaml_returns_none(tmp_path):
    assert read_project_yaml(tmp_path, "missing") is None
