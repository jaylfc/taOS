import os

import pytest

from tinyagentos.agent_tools.fs_tools import (
    read_file, write_file, file_exists, list_dir, JailViolation,
)


def _ws(tmp_path):
    root = tmp_path / "workspace"
    root.mkdir()
    return root


def test_write_then_read_roundtrip(tmp_path):
    root = _ws(tmp_path)
    n = write_file(root, "sub/dir/file.txt", "hello")
    assert n == 5
    assert read_file(root, "sub/dir/file.txt") == "hello"
    assert file_exists(root, "sub/dir/file.txt") is True
    assert file_exists(root, "nope.txt") is False


def test_escape_via_dotdot_is_refused(tmp_path):
    root = _ws(tmp_path)
    (tmp_path / "secret.txt").write_text("top secret")
    with pytest.raises(JailViolation):
        read_file(root, "../secret.txt")
    with pytest.raises(JailViolation):
        write_file(root, "../escaped.txt", "x")
    assert file_exists(root, "../secret.txt") is False


def test_git_path_is_refused(tmp_path):
    root = _ws(tmp_path)
    with pytest.raises(JailViolation):
        write_file(root, ".git/hooks/pre-commit", "#!/bin/sh\necho pwned")
    with pytest.raises(JailViolation):
        read_file(root, ".git/config")


def test_symlink_escape_is_refused(tmp_path):
    root = _ws(tmp_path)
    (tmp_path / "outside.txt").write_text("secret")
    os.symlink(tmp_path / "outside.txt", root / "link.txt")
    with pytest.raises(JailViolation):
        read_file(root, "link.txt")


def test_list_dir(tmp_path):
    root = _ws(tmp_path)
    write_file(root, "a.txt", "1")
    write_file(root, "b.txt", "2")
    write_file(root, "sub/c.txt", "3")
    assert list_dir(root) == ["a.txt", "b.txt", "sub"]
    assert list_dir(root, "sub") == ["c.txt"]
    with pytest.raises(JailViolation):
        list_dir(root, "../")
