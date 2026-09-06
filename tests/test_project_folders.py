from pathlib import Path

import yaml

from tinyagentos.projects.folders import (
    ensure_element_folder,
    ensure_project_layout,
    project_dir,
    read_project_yaml,
    write_project_yaml,
)


def test_project_dir_joins_root_and_slug(tmp_path: Path):
    assert project_dir(tmp_path, "my-project") == tmp_path / "my-project"


def test_ensure_project_layout_creates_subdirs_and_readme(tmp_path: Path):
    base = ensure_project_layout(tmp_path, "alpha", name="Alpha Project")

    assert base == tmp_path / "alpha"
    for sub in ("memory", "canvas", "files"):
        assert (base / sub).is_dir()

    readme = base / "README.md"
    assert readme.is_file()
    assert "Alpha Project" in readme.read_text()
    assert "taOS" in readme.read_text()


def test_ensure_project_layout_uses_slug_when_name_omitted(tmp_path: Path):
    base = ensure_project_layout(tmp_path, "beta-slug")

    readme = base / "README.md"
    assert "beta-slug" in readme.read_text()


def test_ensure_project_layout_idempotent_readme(tmp_path: Path):
    base = ensure_project_layout(tmp_path, "gamma", name="Gamma")
    readme = base / "README.md"
    readme.write_text("custom readme")

    ensure_project_layout(tmp_path, "gamma", name="Other Name")

    assert readme.read_text() == "custom readme"


def test_ensure_project_layout_idempotent_subdirs(tmp_path: Path):
    base = ensure_project_layout(tmp_path, "delta")
    marker = base / "memory" / "marker.txt"
    marker.write_text("keep")

    ensure_project_layout(tmp_path, "delta")

    assert marker.read_text() == "keep"


def test_write_project_yaml_creates_file(tmp_path: Path):
    payload = {"name": "Demo", "slug": "demo", "labels": ["test"]}

    target = write_project_yaml(tmp_path, "demo", payload)

    assert target == tmp_path / "demo" / "project.yaml"
    assert target.is_file()
    loaded = yaml.safe_load(target.read_text())
    assert loaded == payload


def test_write_project_yaml_overwrites_existing(tmp_path: Path):
    write_project_yaml(tmp_path, "echo", {"version": 1})
    write_project_yaml(tmp_path, "echo", {"version": 2})

    loaded = read_project_yaml(tmp_path, "echo")
    assert loaded == {"version": 2}


def test_read_project_yaml_returns_none_when_missing(tmp_path: Path):
    assert read_project_yaml(tmp_path, "missing") is None


def test_read_project_yaml_round_trip(tmp_path: Path):
    payload = {"slug": "fox", "nested": {"a": 1, "b": [2, 3]}}
    write_project_yaml(tmp_path, "fox", payload)

    assert read_project_yaml(tmp_path, "fox") == payload


# ---------------------------------------------------------------------------
# Element files folder (slice 4)
# ---------------------------------------------------------------------------


def test_ensure_element_folder_creates_subdir(tmp_path: Path):
    d = ensure_element_folder(tmp_path, "alpha", "website")
    assert d == tmp_path / "alpha" / "files" / "website"
    assert d.is_dir()


def test_ensure_element_folder_adopts_existing(tmp_path: Path):
    """An existing user-made folder with the element slug is adopted, not
    clobbered: pre-existing contents survive element creation."""
    pre = tmp_path / "alpha" / "files" / "website"
    pre.mkdir(parents=True, exist_ok=True)
    (pre / "existing.txt").write_text("keep me")

    d = ensure_element_folder(tmp_path, "alpha", "website")

    assert d == pre
    assert (d / "existing.txt").read_text() == "keep me"


def test_ensure_element_folder_idempotent(tmp_path: Path):
    first = ensure_element_folder(tmp_path, "alpha", "website")
    second = ensure_element_folder(tmp_path, "alpha", "website")
    assert first == second
    assert first.is_dir()