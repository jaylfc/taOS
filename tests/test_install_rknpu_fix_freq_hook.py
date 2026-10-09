import pathlib
import re

REPO_ROOT = pathlib.Path(__file__).resolve().parents[1]
INSTALL_RKNPU_SH = REPO_ROOT / "scripts/install-rknpu.sh"


def test_rkllama_unit_runs_fix_freq_privileged_before_exec_start():
    text = INSTALL_RKNPU_SH.read_text()
    # 2. Check that the heredoc contains the line: $fix_freq_line
    # We need to find the heredoc block. We know it starts after the line with `sudo tee "$unit" >/dev/null <<EOF`
    # and ends with a line that is just `EOF`.
    # We'll split the text into lines and find the heredoc block.

    lines = text.splitlines()
    in_heredoc = False
    heredoc_lines = []
    for line in lines:
        if line.strip() == 'sudo tee "$unit" >/dev/null <<EOF':
            in_heredoc = True
            continue
        if in_heredoc and line.strip() == 'EOF':
            break
        if in_heredoc:
            heredoc_lines.append(line)

    # 3. Check that the $fix_freq_line line is after the pkill ExecStartPre and before the ExecStart line.
    # We'll find the indices of the pkill line and the ExecStart line in heredoc_lines.
    pkill_line = None
    exec_start_line = None
    fix_freq_line_index = None
    for i, line in enumerate(heredoc_lines):
        stripped = line.strip()
        if stripped.startswith('ExecStartPre=-/usr/bin/pkill -9 -f $RKLLAMA_VENV/bin/rkllama_server'):
            pkill_line = i
        if stripped.startswith('ExecStart=$exec_start'):
            exec_start_line = i
        if stripped == '$fix_freq_line':
            fix_freq_line_index = i

    assert pkill_line is not None, "Could not find pkill ExecStartPre line in heredoc"
    assert exec_start_line is not None, "Could not find ExecStart line in heredoc"
    assert fix_freq_line_index is not None, "Could not find $fix_freq_line line in heredoc"

    # Check order: pkill_line < fix_freq_line_index < exec_start_line
    assert pkill_line < fix_freq_line_index, (
        f"$fix_freq_line line (index {fix_freq_line_index}) must come after pkill line (index {pkill_line})"
    )
    assert fix_freq_line_index < exec_start_line, (
        f"$fix_freq_line line (index {fix_freq_line_index}) must come before ExecStart line (index {exec_start_line})"
    )

    # 4. Check that the script contains the existence guard: if [ -f "$fix_freq" ]
    # We'll look for: if [ -f "$fix_freq" ]
    # Note: The $fix_freq is a variable, so we need to escape the $ in the regex for the variable part?
    # We want to match the literal string: if [ -f "$fix_freq" ]
    # In the script, it is written as: if [ -f "$fix_freq" ]
    # So we need to escape the $ and the quotes? Actually, we can use a raw string and escape the $.
    pattern = r'if \[ -f \"\$fix_freq\" \]'
    assert re.search(pattern, text), f"Expected to find existence guard: {pattern}"