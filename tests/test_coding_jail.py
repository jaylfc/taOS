"""Unit tests for the workspace-jail helpers in tinyagentos/routes/coding.py.

The pure, security-critical functions under test are ``_invalid_rel_path`` and
``_resolve_jailed``. Every user-supplied path flows through them before any disk
access, including the validation the ``apply_blocks`` route performs after
resolving (reject None, reject root, reject directory). The filesystem is mocked
with ``tmp_path``; no live app, agent, or container is required. The async route
handlers themselves need a workspace store and git repo, so they are covered by
the HTTP-level tests in ``tests/test_coding_workspaces.py`` and
``tests/test_coding_apply_blocks.py``.
"""

from __future__ import annotations

import pytest

from tinyagentos.routes.coding import _invalid_rel_path, _resolve_jailed


def _make_workspace(tmp_path: pytest.Path) -> pytest.Path:
    """A real directory acting as the workspace root, canonicalized.

    ``_resolve_jailed`` compares the resolved target against ``root`` with
    ``is_relative_to`` / ``==`` (component comparison, not symlink resolution of
    ``root`` itself), so ``root`` must be canonical -- exactly what
    ``_workspace_root`` hands to the routes in production.
    """
    root = (tmp_path / "workspace").resolve()
    root.mkdir(parents=True, exist_ok=True)
    return root


def _make_symlink(src, dst):
    """Create a symlink, skipping on platforms without support."""
    try:
        dst.symlink_to(src)
    except (OSError, NotImplementedError):
        pytest.skip("symlinks unsupported on this platform")


def _apply_blocks_rejects(root, rel):
    """Mirror of the per-block guard in the ``apply_blocks`` route: a block is
    rejected when ``_resolve_jailed`` returns None, equals the root, or
    resolves to a directory."""
    target = _resolve_jailed(root, rel)
    return target is None or target == root or target.is_dir()


@pytest.fixture
def jail_root(tmp_path):
    return _make_workspace(tmp_path)


# ---------------------------------------------------------------------------
# _invalid_rel_path -- the first gate
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("rel", ["", "src/file.py", "a/b/c/d.py", "./x"])
def test_invalid_rel_path_accepts_clean_relative(rel):
    assert _invalid_rel_path(rel) is False


def test_invalid_rel_path_accepts_dotgitignore():
    # ".gitignore" is one component, not the ".git" directory.
    assert _invalid_rel_path(".gitignore") is False


@pytest.mark.parametrize("rel", ["/etc/passwd", "//etc/passwd", "/abs/path"])
def test_invalid_rel_path_rejects_absolute(rel):
    assert _invalid_rel_path(rel) is True


def test_invalid_rel_path_rejects_url_scheme():
    assert _invalid_rel_path("file:///etc/passwd") is True
    assert _invalid_rel_path("http://evil.example/x") is True


def test_invalid_rel_path_rejects_protocol_relative():
    assert _invalid_rel_path("//evil.example/x") is True


@pytest.mark.parametrize(
    "rel", ["../escape.py", "src/../../etc/passwd", "..", "src/../.."]
)
def test_invalid_rel_path_rejects_traversal(rel):
    assert _invalid_rel_path(rel) is True


# ---------------------------------------------------------------------------
# _resolve_jailed -- happy path
# ---------------------------------------------------------------------------


def test_resolve_jailed_resolves_nested_file(jail_root):
    target = _resolve_jailed(jail_root, "src/nested/file.py")
    assert target is not None
    assert target == (jail_root / "src" / "nested" / "file.py")


def test_resolve_jailed_resolves_top_level_file(jail_root):
    assert _resolve_jailed(jail_root, "readme.txt") == jail_root / "readme.txt"


def test_resolve_jailed_resolves_existing_file(jail_root):
    f = jail_root / "hello.py"
    f.write_text("print('hi')")
    assert _resolve_jailed(jail_root, "hello.py") == f


def test_resolve_jailed_allows_root_with_flag(jail_root):
    assert _resolve_jailed(jail_root, "", allow_root=True) == jail_root


def test_resolve_jailed_rejects_root_without_flag(jail_root):
    assert _resolve_jailed(jail_root, "") is None
    assert _resolve_jailed(jail_root, ".") is None


# ---------------------------------------------------------------------------
# _resolve_jailed -- escape attempts
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "rel", ["../escape.py", "../../etc/passwd", "src/../../../outside"]
)
def test_resolve_jailed_rejects_dotdot_traversal(jail_root, rel):
    assert _resolve_jailed(jail_root, rel) is None


@pytest.mark.parametrize("rel", ["/etc/passwd", "/abs/path"])
def test_resolve_jailed_rejects_absolute(jail_root, rel):
    assert _resolve_jailed(jail_root, rel) is None


def test_resolve_jailed_rejects_protocol_relative(jail_root):
    assert _resolve_jailed(jail_root, "//evil.example/x") is None


def test_resolve_jailed_rejects_url_scheme(jail_root):
    assert _resolve_jailed(jail_root, "http://evil.example/x") is None


def test_resolve_jailed_rejects_any_dotdot_component(jail_root):
    # "sub/../file.py" contains a ".." part and is rejected even though it would
    # resolve to a path inside the workspace; "src/file.py" has no traversal
    # and resolves inside the jail.
    assert _resolve_jailed(jail_root, "sub/../file.py") is None
    assert _resolve_jailed(jail_root, "src/file.py") is not None


# ---------------------------------------------------------------------------
# _resolve_jailed -- .git directory
# ---------------------------------------------------------------------------


def test_resolve_jailed_rejects_dotgit_config(jail_root):
    assert _resolve_jailed(jail_root, ".git/config") is None


def test_resolve_jailed_rejects_dotgit_hooks(jail_root):
    assert _resolve_jailed(jail_root, ".git/hooks/pre-commit") is None


def test_resolve_jailed_rejects_dotgit_anywhere(jail_root):
    assert _resolve_jailed(jail_root, "subdir/.git/config") is None


def test_resolve_jailed_rejects_dotgit_root_itself(jail_root):
    assert _resolve_jailed(jail_root, ".git") is None


def test_resolve_jailed_accepts_dotgitignore(jail_root):
    assert _resolve_jailed(jail_root, ".gitignore") == jail_root / ".gitignore"


def test_resolve_jailed_accepts_unrelated_dir_as_file_target(jail_root):
    (jail_root / "src").mkdir()
    assert _resolve_jailed(jail_root, "src/file.py") == (
        jail_root / "src" / "file.py"
    )


# ---------------------------------------------------------------------------
# _resolve_jailed -- symlink traversal
# ---------------------------------------------------------------------------


def test_resolve_jailed_symlink_dir_escaping_root_rejected(tmp_path):
    root = _make_workspace(tmp_path)
    external = tmp_path / "outside"
    external.mkdir()
    (external / "secret.txt").write_text("shh")
    _make_symlink(external, root / "escape")
    assert _resolve_jailed(root, "escape/secret.txt") is None


def test_resolve_jailed_symlink_file_escaping_root_rejected(tmp_path):
    root = _make_workspace(tmp_path)
    external = tmp_path / "outside"
    external.mkdir()
    secret = external / "secret.txt"
    secret.write_text("shh")
    _make_symlink(secret, root / "link_file")
    assert _resolve_jailed(root, "link_file") is None


def test_resolve_jailed_symlink_pointing_to_root_parent_rejected(tmp_path):
    root = _make_workspace(tmp_path)
    # tmp_path is the parent of root (i.e. outside the jail).
    (tmp_path / "secret.txt").write_text("outside")
    _make_symlink(tmp_path, root / "up")
    assert _resolve_jailed(root, "up/secret.txt") is None


def test_resolve_jailed_symlink_anchored_inside_jail_allowed(tmp_path):
    root = _make_workspace(tmp_path)
    inner = root / "real_dir"
    inner.mkdir()
    (inner / "real_file.py").write_text("hi")
    _make_symlink(inner, root / "alias")
    result = _resolve_jailed(root, "alias/real_file.py")
    assert result is not None
    assert result == (inner / "real_file.py")


# ---------------------------------------------------------------------------
# apply-blocks validation gate (replicated at unit level)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "rel", ["../escape.py", "/etc/passwd", ".git/config", "sub/../x.py"]
)
def test_apply_blocks_rejects_jailed_out_paths(jail_root, rel):
    assert _apply_blocks_rejects(jail_root, rel) is True


def test_apply_blocks_rejects_empty_path(jail_root):
    assert _apply_blocks_rejects(jail_root, "") is True


def test_apply_blocks_rejects_dot_path(jail_root):
    assert _apply_blocks_rejects(jail_root, ".") is True


def test_apply_blocks_rejects_directory_target(jail_root):
    (jail_root / "subdir").mkdir()
    assert _apply_blocks_rejects(jail_root, "subdir") is True


def test_apply_blocks_accepts_valid_file_path(jail_root):
    assert _apply_blocks_rejects(jail_root, "ok.py") is False


def test_apply_blocks_accepts_nested_new_file(jail_root):
    assert _apply_blocks_rejects(jail_root, "deep/nested/ok.py") is False
