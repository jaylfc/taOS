import subprocess
from pathlib import Path

import pytest

from tinyagentos.rollback import ROLLBACK_FILE, read_rollback_target, record_pre_update

ROLLBACK_SH = Path(__file__).resolve().parent.parent / "scripts" / "rollback.sh"


def _shell_record_field(record_dir: Path, key: str) -> str:
    """Run rollback.sh's own record_field() against a record file.

    The function is lifted straight out of the script so the shell reader and
    the Python writer are proven to agree on one file, instead of each being
    tested against its own idea of the format.
    """
    collected: list[str] = []
    depth = 0
    for line in ROLLBACK_SH.read_text().splitlines():
        if not collected and not line.startswith("record_field()"):
            continue
        collected.append(line)
        depth += line.count("{") - line.count("}")
        if depth <= 0 and len(collected) > 1:
            break
    script = "\n".join(collected) + f"\nrecord_field {key}\n"
    return subprocess.check_output(
        ["bash", "-c", script], cwd=str(record_dir), text=True
    )


def test_record_then_read_roundtrip(tmp_path):
    record_pre_update(tmp_path, branch="dev", sha="abc123def", ts=1700000000)
    target = read_rollback_target(tmp_path)
    assert target == {"branch": "dev", "sha": "abc123def", "ts": "1700000000"}


def test_record_overwrites(tmp_path):
    record_pre_update(tmp_path, branch="dev", sha="aaaaaaa", ts=1)
    record_pre_update(tmp_path, branch="feat/x", sha="bbbbbbb", ts=2)
    assert read_rollback_target(tmp_path) == {
        "branch": "feat/x",
        "sha": "bbbbbbb",
        "ts": "2",
    }


def test_read_none_when_absent(tmp_path):
    assert read_rollback_target(tmp_path) is None


def test_shell_parser_reads_the_same_values(tmp_path):
    """rollback.sh parses this file, so bash must read what Python wrote."""
    record_pre_update(tmp_path, branch="feat/odd-name", sha="deadbeef", ts=42)
    assert _shell_record_field(tmp_path, "prev_branch") == "feat/odd-name"
    assert _shell_record_field(tmp_path, "prev_sha") == "deadbeef"
    assert _shell_record_field(tmp_path, "prev_ts") == "42"


def test_quote_injection_is_safe(tmp_path):
    # A branch name with a quote must survive the escape both readers undo.
    record_pre_update(tmp_path, branch="a'b", sha="ccccccc", ts=1)
    assert read_rollback_target(tmp_path)["branch"] == "a'b"
    assert _shell_record_field(tmp_path, "prev_branch") == "a'b"


def test_record_rejects_a_non_sha(tmp_path):
    """Only a git object name is recordable, so no reader has to guess."""
    with pytest.raises(ValueError):
        record_pre_update(tmp_path, branch="dev", sha="origin/dev", ts=1)
    assert not (tmp_path / ROLLBACK_FILE).exists()


def test_record_rejects_a_newline_in_the_branch(tmp_path):
    """A newline would forge a second prev_sha= line in the record."""
    with pytest.raises(ValueError):
        record_pre_update(tmp_path, branch="dev\nprev_sha='beef'", sha="deadbeef", ts=1)
    assert not (tmp_path / ROLLBACK_FILE).exists()


def test_read_rejects_a_tampered_sha(tmp_path):
    """A truncated or edited record reads as no record, not as a bad target."""
    (tmp_path / ROLLBACK_FILE).write_text("prev_branch='dev'\nprev_sha='ab")
    assert read_rollback_target(tmp_path) is None


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
            return (0, "abc1234567\n")
        if "status --porcelain" in joined:
            return (0, "")  # clean
        return (0, "")

    monkeypatch.setattr(ur, "_run", fake_run)
    await ur.update_to_master(tmp_path)
    target = read_rollback_target(tmp_path)
    assert target is not None
    assert target["branch"] == "dev"
    assert target["sha"] == "abc1234567"
