import pathlib
import re

REPO_ROOT = pathlib.Path(__file__).resolve().parents[1]
INSTALL_RKLLAMACPP_SH = REPO_ROOT / "scripts/install-rk-llama-cpp.sh"


def test_ctx_default_and_override_line_exists():
    text = INSTALL_RKLLAMACPP_SH.read_text()
    # CTX="${TAOS_RKLLAMACPP_CTX:-12288}" with a comment about 12288 being the measured NPU ceiling
    assert re.search(r'CTX="\$\{TAOS_RKLLAMACPP_CTX:-12288\}"', text), (
        'CTX="${TAOS_RKLLAMACPP_CTX:-12288}" line not found'
    )


def test_execstart_contains_ctx_flag():
    text = INSTALL_RKLLAMACPP_SH.read_text()
    # ExecStart line should contain " -c $CTX" after --port $PORT
    assert re.search(r'ExecStart=.*--port \$\w+\s+-c \$CTX', text), (
        'ExecStart line must contain " -c $CTX" after --port'
    )


def test_ctx_digits_check_before_unit_write():
    text = INSTALL_RKLLAMACPP_SH.read_text()
    lines = text.splitlines()

    # Find the line index of the digits check on CTX
    ctx_check_idx = None
    for i, line in enumerate(lines):
        stripped = line.strip()
        # Match [[ "$CTX" =~ ^[0-9]+$ ]] or [[ ! "$CTX" =~ ^[0-9]+$ ]] or equivalent case pattern
        if re.search(r'\[\[\s*!?\s*"\$CTX"\s*=~\s*\^\[0-9\]\+\$\s*\]\]', stripped) or \
           re.search(r'case\s+\$CTX\s+in\s+.*\[0-9\].*\)', stripped):
            ctx_check_idx = i
            break

    assert ctx_check_idx is not None, "Digits check on CTX not found (e.g., [[ \"$CTX\" =~ ^[0-9]+$ ]])"

    # Find the line index of `sudo tee "$UNIT_PATH"`
    unit_write_idx = None
    for i, line in enumerate(lines):
        if 'sudo tee "$UNIT_PATH"' in line:
            unit_write_idx = i
            break

    assert unit_write_idx is not None, 'sudo tee "$UNIT_PATH" line not found'

    # The digits check must come BEFORE the unit write
    assert ctx_check_idx < unit_write_idx, (
        f"CTX digits check (line {ctx_check_idx}) must be before sudo tee \"$UNIT_PATH\" (line {unit_write_idx})"
    )


def test_env_var_documented_in_header():
    text = INSTALL_RKLLAMACPP_SH.read_text()
    # Header comment should document TAOS_RKLLAMACPP_CTX
    assert re.search(r'TAOS_RKLLAMACPP_CTX\s+llama-server context size \(default: 12288\)', text), (
        "TAOS_RKLLAMACPP_CTX not documented in header comment"
    )


def test_log_line_includes_ctx():
    text = INSTALL_RKLLAMACPP_SH.read_text()
    # The log line should include ctx value: (port $PORT, ctx $CTX)
    assert re.search(r'log "writing \$UNIT_PATH \(port \$PORT, ctx \$CTX\)"', text), (
        'log line must include ctx value like "(port $PORT, ctx $CTX)"'
    )