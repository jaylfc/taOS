from __future__ import annotations

from pathlib import Path

import yaml

_README = "# {name}\n\nProject workspace managed by taOS.\n"


def project_dir(root: Path, slug: str) -> Path:
    return root / slug


def ensure_project_layout(root: Path, slug: str, name: str | None = None) -> Path:
    base = project_dir(root, slug)
    for sub in ("memory", "canvas", "files"):
        (base / sub).mkdir(parents=True, exist_ok=True)
    readme = base / "README.md"
    if not readme.exists():
        readme.write_text(_README.format(name=name or slug))
    return base


def write_project_yaml(root: Path, slug: str, payload: dict) -> Path:
    base = project_dir(root, slug)
    base.mkdir(parents=True, exist_ok=True)
    target = base / "project.yaml"
    target.write_text(yaml.safe_dump(payload, sort_keys=False))
    return target


def read_project_yaml(root: Path, slug: str) -> dict | None:
    target = project_dir(root, slug) / "project.yaml"
    if not target.exists():
        return None
    return yaml.safe_load(target.read_text())


def ensure_element_folder(
    root: Path, project_slug: str, element_slug: str
) -> Path:
    """Best-effort files subfolder for a project element.

    An element owns ``projects_root/<project-slug>/files/<element-slug>/``.
    If a user already created a folder with that name, the element adopts it
    rather than erroring: the DB row (the element) is the authority, so the
    disk path only needs to exist. Returns the element files path.

    A disk failure must never break element creation, so errors are swallowed
    by the caller; this helper raises nothing on its own for ordinary missing
    parents.
    """
    d = root / project_slug / "files" / element_slug
    d.mkdir(parents=True, exist_ok=True)
    return d
