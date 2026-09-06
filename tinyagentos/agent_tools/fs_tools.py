from __future__ import annotations

from pathlib import Path

from tinyagentos.routes.coding import _resolve_jailed


class JailViolation(ValueError):
    """Raised when a path escapes the workspace, targets .git, or is otherwise refused.

    These are the file primitives the Coding Studio agent calls for live edits.
    Every path is resolved through the same workspace jail the coding routes use
    (_resolve_jailed), so an escape via ``..``, an absolute path, a symlink that
    resolves outside the workspace, or anything under ``.git`` is refused.
    """


def _resolve(workspace_root, rel_path: str, *, allow_root: bool = False) -> Path:
    root = Path(workspace_root).resolve()
    target = _resolve_jailed(root, rel_path, allow_root=allow_root)
    if target is None:
        raise JailViolation(f"path refused (escapes workspace or targets .git): {rel_path!r}")
    return target


def read_file(workspace_root, rel_path: str) -> str:
    return _resolve(workspace_root, rel_path).read_text()


def write_file(workspace_root, rel_path: str, content: str) -> int:
    target = _resolve(workspace_root, rel_path)
    target.parent.mkdir(parents=True, exist_ok=True)
    return target.write_text(content)


def file_exists(workspace_root, rel_path: str) -> bool:
    try:
        return _resolve(workspace_root, rel_path).is_file()
    except JailViolation:
        return False


def list_dir(workspace_root, rel_path: str = ".") -> list[str]:
    target = _resolve(workspace_root, rel_path, allow_root=True)
    if not target.is_dir():
        raise JailViolation(f"not a directory: {rel_path!r}")
    return sorted(p.name for p in target.iterdir())
