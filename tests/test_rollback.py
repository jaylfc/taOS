import subprocess
from pathlib import Path

import pytest

from tinyagentos.rollback import (
    ROLLBACK_FILE,
    _ref_safe,
    read_rollback_target,
    record_pre_update,
)

ROLLBACK_SH = Path(__file__).resolve().parent.parent / "scripts" / "rollback.sh"

# Full git object names, as `git rev-parse HEAD` emits them. Short strings would
# not survive the writer's own validation, and pretending otherwise is how the
# old fixtures let an abbreviated sha look legitimate.
SHA_A = "a1b2c3d4e5f60718293a4b5c6d7e8f9012345678"
SHA_B = "b2c3d4e5f60718293a4b5c6d7e8f90123456789a"

# Branch names and whether `git check-ref-format refs/heads/<name>` accepts them,
# plus the two extra rules the readers impose: a name is unusable when empty, and
# when it starts with a dash (`git checkout -B -x` would read it as an option --
# git itself calls `refs/heads/-foo` a perfectly valid ref).
REF_CASES = [
    ("main", True),
    ("dev", True),
    ("feat/x", True),
    ("feat/odd-name", True),
    ("a.b", True),
    ("a-b", True),
    ("HEAD", True),
    ("@", True),
    ("", False),
    ("-foo", False),
    ("feat/..evil", False),
    ("..", False),
    (".hidden", False),
    ("feat/.hidden", False),
    ("x.lock", False),
    ("feat/x.lock", False),
    ("a@{b", False),
    ("a b", False),
    ("a~b", False),
    ("a^b", False),
    ("a:b", False),
    ("a?b", False),
    ("a*b", False),
    ("a[b", False),
    ("a\\b", False),
    ("a.", False),
    ("feat/", False),
    ("/feat", False),
    ("feat//x", False),
    ("a\tb", False),
    ("a\x7fb", False),
]


def _shell_func(name: str) -> str:
    """Return one shell function's source from scripts/rollback.sh.

    Anchored at both ends -- the `name()` line and the first following `}` in
    column 0 -- rather than counting braces, which a `${var}` in the body makes
    a coin flip.
    """
    lines = ROLLBACK_SH.read_text().splitlines()
    start = next(
        (i for i, line in enumerate(lines) if line.startswith(f"{name}()")), None
    )
    assert start is not None, f"{name}() not found in {ROLLBACK_SH}"
    end = next((i for i in range(start + 1, len(lines)) if lines[i] == "}"), None)
    assert end is not None, f"no closing brace for {name}() in {ROLLBACK_SH}"
    return "\n".join(lines[start : end + 1])


def _shell_record_field(record_dir: Path, key: str) -> str:
    """Run rollback.sh's own record_field() against a record file.

    The function is lifted straight out of the script so the shell reader and
    the Python writer are proven to agree on one file, instead of each being
    tested against its own idea of the format.
    """
    script = _shell_func("record_field") + f"\nrecord_field {key}\n"
    return subprocess.check_output(
        ["bash", "-c", script], cwd=str(record_dir), text=True
    )


def _shell_ref_safe(name: str) -> bool:
    """Ask rollback.sh's own ref_safe() whether it would restore this branch."""
    script = _shell_func("ref_safe") + '\nref_safe "$1"\n'
    return subprocess.run(["bash", "-c", script, "bash", name]).returncode == 0


def _shell_sha_safe(sha: str) -> bool:
    """Ask rollback.sh's own sha_safe() whether it would use this commit."""
    script = _shell_func("sha_safe") + '\nsha_safe "$1"\n'
    return subprocess.run(["bash", "-c", script, "bash", sha]).returncode == 0


def test_record_then_read_roundtrip(tmp_path):
    record_pre_update(tmp_path, branch="dev", sha=SHA_A, ts=1700000000)
    target = read_rollback_target(tmp_path)
    assert target == {"branch": "dev", "sha": SHA_A, "ts": "1700000000"}


def test_record_overwrites(tmp_path):
    record_pre_update(tmp_path, branch="dev", sha=SHA_A, ts=1)
    record_pre_update(tmp_path, branch="feat/x", sha=SHA_B, ts=2)
    assert read_rollback_target(tmp_path) == {
        "branch": "feat/x",
        "sha": SHA_B,
        "ts": "2",
    }


def test_read_none_when_absent(tmp_path):
    assert read_rollback_target(tmp_path) is None


def test_shell_parser_reads_the_same_values(tmp_path):
    """rollback.sh parses this file, so bash must read what Python wrote."""
    record_pre_update(tmp_path, branch="feat/odd-name", sha=SHA_A, ts=42)
    assert _shell_record_field(tmp_path, "prev_branch") == "feat/odd-name"
    assert _shell_record_field(tmp_path, "prev_sha") == SHA_A
    assert _shell_record_field(tmp_path, "prev_ts") == "42"


def test_quote_injection_is_safe(tmp_path):
    # A branch name with a quote must survive the escape both readers undo.
    record_pre_update(tmp_path, branch="a'b", sha=SHA_A, ts=1)
    assert read_rollback_target(tmp_path)["branch"] == "a'b"
    assert _shell_record_field(tmp_path, "prev_branch") == "a'b"


@pytest.mark.parametrize(
    "sha",
    [SHA_A + "\n", SHA_A + " ", " " + SHA_A, SHA_A + "\r\n", SHA_A + "\t"],
)
def test_record_rejects_a_sha_with_surrounding_whitespace(tmp_path, sha):
    """Whitespace around the object name makes it unusable, so refuse to write it.

    Python's ``$`` matches before one final newline, so a plain ``re.match`` lets
    ``<40 hex>\\n`` through -- and bash's ``=~`` does not, which is the worse
    half: the writer would happily record a value the shell then refuses, losing
    the rollback target rather than reporting anything.
    """
    with pytest.raises(ValueError):
        record_pre_update(tmp_path, branch="dev", sha=sha, ts=1)
    assert not (tmp_path / ROLLBACK_FILE).exists()


@pytest.mark.parametrize(
    "sha", [SHA_A, "c" * 64, SHA_A + "\n", SHA_A + " ", " " + SHA_A, SHA_A[:39], "dev"]
)
def test_shell_sha_safe_matches_the_writer(tmp_path, sha):
    """Both ends must draw the line in the same place, whitespace included.

    Compares the shell's own sha_safe() against what the writer will actually
    put in the file: a value the writer accepts but the shell refuses is a
    rollback target that silently does not work.
    """
    try:
        record_pre_update(tmp_path, branch="dev", sha=sha, ts=1)
        writable = True
    except ValueError:
        writable = False
    assert _shell_sha_safe(sha) == writable, (
        f"shell sha_safe({sha!r}) disagrees with the writer"
    )


def test_record_rejects_a_non_sha(tmp_path):
    """Only a git object name is recordable, so no reader has to guess."""
    with pytest.raises(ValueError):
        record_pre_update(tmp_path, branch="dev", sha="origin/dev", ts=1)
    assert not (tmp_path / ROLLBACK_FILE).exists()


@pytest.mark.parametrize("sha", ["abc123d", "abc1234567", SHA_A[:12], SHA_A[:39]])
def test_record_rejects_an_abbreviated_sha(tmp_path, sha):
    """The writer records `git rev-parse HEAD`, which is never abbreviated.

    A short value in the record is therefore a truncated or forged one, so it
    must not be writable and must not read back as a usable target.
    """
    with pytest.raises(ValueError):
        record_pre_update(tmp_path, branch="dev", sha=sha, ts=1)
    assert not (tmp_path / ROLLBACK_FILE).exists()

    (tmp_path / ROLLBACK_FILE).write_text(f"prev_branch='dev'\nprev_sha='{sha}'\n")
    assert read_rollback_target(tmp_path) is None


def test_record_accepts_a_sha256_object_name(tmp_path):
    """A sha256 checkout emits 64-hex names; that is a full name, not a forgery."""
    sha256 = "c" * 64
    record_pre_update(tmp_path, branch="dev", sha=sha256, ts=1)
    assert read_rollback_target(tmp_path)["sha"] == sha256


def test_record_rejects_a_newline_in_the_branch(tmp_path):
    """A newline would forge a second prev_sha= line in the record."""
    with pytest.raises(ValueError):
        record_pre_update(tmp_path, branch="dev\nprev_sha='beef'", sha=SHA_A, ts=1)
    assert not (tmp_path / ROLLBACK_FILE).exists()


def test_read_rejects_a_tampered_sha(tmp_path):
    """A truncated or edited record reads as no record, not as a bad target."""
    (tmp_path / ROLLBACK_FILE).write_text("prev_branch='dev'\nprev_sha='ab")
    assert read_rollback_target(tmp_path) is None


@pytest.mark.parametrize("name,valid", REF_CASES)
def test_ref_safe_matches_git_check_ref_format(name, valid):
    """The Python rule is a reimplementation, so pin it to git's own checker.

    `git check-ref-format` is what scripts/rollback.sh asks, so agreeing with it
    is what makes the two readers agree with each other.
    """
    by_git = (
        name != ""
        and not name.startswith("-")
        and subprocess.run(
            ["git", "check-ref-format", f"refs/heads/{name}"],
            capture_output=True,
        ).returncode
        == 0
    )
    assert by_git == valid, f"REF_CASES disagrees with git for {name!r}"
    assert _ref_safe(name) == valid, f"python _ref_safe({name!r}) != {valid}"


@pytest.mark.parametrize("name,valid", REF_CASES)
def test_shell_ref_safe_matches_python(name, valid):
    """Same rule on the shell end, asked of the script's own function."""
    assert _shell_ref_safe(name) == valid, f"shell ref_safe({name!r}) != {valid}"


def test_read_drops_an_unsafe_branch_but_keeps_the_commit(tmp_path):
    """Matching the shell: a bad branch costs the branch, never the commit."""
    (tmp_path / ROLLBACK_FILE).write_text(
        f"prev_branch='feat/..evil'\nprev_sha='{SHA_A}'\nprev_ts='7'\n"
    )
    assert read_rollback_target(tmp_path) == {"branch": "", "sha": SHA_A, "ts": "7"}


@pytest.mark.asyncio
async def test_update_records_rollback_target(tmp_path, monkeypatch):
    """update_to_master records the pre-update branch + sha before mutating."""
    import tinyagentos.update_runner as ur

    calls = {"n": 0}

    async def fake_run(args, cwd):
        # Simulate: fetch ok, on branch 'dev', HEAD sha, clean tree, ff-merge ok.
        joined = " ".join(args)
        if "rev-parse --abbrev-ref" in joined:
            return (0, "dev\n")
        if "rev-parse HEAD" in joined:
            return (0, f"{SHA_A}\n")
        if "status --porcelain" in joined:
            return (0, "")  # clean
        return (0, "")

    monkeypatch.setattr(ur, "_run", fake_run)
    await ur.update_to_master(tmp_path)
    target = read_rollback_target(tmp_path)
    assert target is not None
    assert target["branch"] == "dev"
    assert target["sha"] == SHA_A
