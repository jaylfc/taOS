import pathlib
import re

REPO_ROOT = pathlib.Path(__file__).resolve().parents[1]
INSTALL_RKNPU_SH = REPO_ROOT / "scripts/install-rknpu.sh"


def _unit_heredoc_lines(text: str) -> list[str]:
    """The lines of the rkllama.service heredoc, verbatim (indentation kept)."""
    lines = text.splitlines()
    in_heredoc = False
    heredoc_lines = []
    for line in lines:
        if line.strip() == 'sudo tee "$unit" >/dev/null <<EOF':
            in_heredoc = True
            continue
        if in_heredoc and line.strip() == "EOF":
            break
        if in_heredoc:
            heredoc_lines.append(line)
    assert heredoc_lines, "could not find the rkllama.service heredoc"
    return heredoc_lines


def test_fix_freq_line_is_a_privileged_non_blocking_prestart_behind_an_existence_guard():
    text = INSTALL_RKNPU_SH.read_text()
    # '-+' = never block the start, full privileges regardless of User=.
    assert re.search(
        r'^\s*fix_freq_line="ExecStartPre=-\+/bin/bash \$fix_freq 0"$', text, re.MULTILINE
    ), "fix_freq_line must be a '-+' privileged, non-fatal ExecStartPre"
    # rk3568 ships no fix_freq script, so the hook is emitted only when the file exists.
    assert re.search(r'^\s*if \[ -f "\$fix_freq" \]', text, re.MULTILINE), (
        "fix_freq_line must be guarded by an existence check on $fix_freq"
    )


def test_rkllama_unit_runs_fix_freq_privileged_before_exec_start():
    heredoc_lines = _unit_heredoc_lines(INSTALL_RKNPU_SH.read_text())

    # The heredoc is unquoted (<<EOF), so every byte of indentation is written
    # into the unit; the directives must sit at column 0 exactly as emitted.
    pkill_line = heredoc_lines.index(
        "ExecStartPre=-/usr/bin/pkill -9 -f $RKLLAMA_VENV/bin/rkllama_server"
    )
    exec_start_line = heredoc_lines.index("ExecStart=$exec_start")
    fix_freq_line_index = heredoc_lines.index("$fix_freq_line")

    assert pkill_line < fix_freq_line_index < exec_start_line, (
        f"$fix_freq_line (index {fix_freq_line_index}) must sit after the pkill "
        f"ExecStartPre (index {pkill_line}) and before ExecStart (index {exec_start_line})"
    )
    assert not any(line != line.lstrip() and line.strip() for line in heredoc_lines), (
        "no indented directive may be emitted into the unit"
    )
