"""Unit tests for scripts/kv_quant_validator.py.

Coverage goals
--------------
1. Prompt assembly is deterministic -- same (context, secrets) every call.
2. build_needle_prompt embeds all three secrets in the output text.
3. parse_secrets_from_output handles case variations and extra whitespace.
4. parse_configs() accepts valid input and rejects malformed entries.
5. parse_contexts() accepts valid input and rejects empty input.
6. KVConfig.label() produces stable, readable strings.
7. build_llama_cli_args() emits the correct flag list including the boundary
   flag only when boundary > 0.
8. write_csv() produces a valid CSV with the expected field names and row
   count for a given result set.
9. The CLI (via subprocess) exits with a non-zero code and a useful message
   when the model path does not exist.
"""

from __future__ import annotations

import csv
import io
import subprocess
import sys
from pathlib import Path

import pytest

# Import the module under test.  The scripts/ directory is not a package so
# we manipulate sys.path to make it importable without an install step.
sys.path.insert(0, str(Path(__file__).parent.parent / "scripts"))
import kv_quant_validator as kv  # noqa: E402 -- path manipulation must precede


# ---------------------------------------------------------------------------
# 1 + 2. Prompt assembly
# ---------------------------------------------------------------------------

class TestBuildNeedlePrompt:
    """Tests for build_needle_prompt()."""

    def test_deterministic(self):
        """Identical context lengths always produce identical prompts."""
        p1, s1 = kv.build_needle_prompt(1024)
        p2, s2 = kv.build_needle_prompt(1024)
        assert p1 == p2, "Prompt must be deterministic for the same context length"
        assert s1 == s2, "Secrets dict must be identical across calls"

    def test_deterministic_large_context(self):
        """Determinism holds at a larger context length."""
        p1, _ = kv.build_needle_prompt(8192)
        p2, _ = kv.build_needle_prompt(8192)
        assert p1 == p2

    def test_different_contexts_differ(self):
        """Prompts for different context lengths should differ in length (more filler)."""
        short, _ = kv.build_needle_prompt(1024)
        long_, _ = kv.build_needle_prompt(8192)
        assert len(long_) > len(short), "Longer context should produce longer prompt"

    def test_all_secrets_present(self):
        """All three secret values must appear verbatim in the prompt."""
        prompt, secrets = kv.build_needle_prompt(2048)
        for kind, value in secrets.items():
            assert value in prompt, f"Secret '{kind}' ({value!r}) not found in prompt"

    def test_secrets_match_module_constants(self):
        """The returned secrets dict matches the module-level SECRETS constant."""
        _, secrets = kv.build_needle_prompt(1024)
        assert secrets == kv.SECRETS

    def test_minimum_context_does_not_crash(self):
        """Very small context length (e.g. 128) should not raise."""
        prompt, _ = kv.build_needle_prompt(128)
        assert isinstance(prompt, str)
        assert len(prompt) > 0

    def test_prompt_contains_instruction(self):
        """The retrieval instruction must be present so the model knows what to do."""
        prompt, _ = kv.build_needle_prompt(1024)
        assert "SECRET-A" in prompt
        assert "SECRET-B" in prompt
        assert "SECRET-C" in prompt


# ---------------------------------------------------------------------------
# 3. Secret parsing
# ---------------------------------------------------------------------------

class TestParseSecretsFromOutput:
    """Tests for parse_secrets_from_output()."""

    def _secrets(self):
        return dict(kv.SECRETS)

    def test_all_found_exact(self):
        """Exact values in output -> all True."""
        output = f"SECRET-A: {kv.SECRETS['integer']} SECRET-B: {kv.SECRETS['proper_noun']} SECRET-C: {kv.SECRETS['color']}"
        result = kv.parse_secrets_from_output(output, self._secrets())
        assert result == {"integer": True, "proper_noun": True, "color": True}

    def test_none_found(self):
        """Output with no matching values -> all False."""
        result = kv.parse_secrets_from_output("I don't know the answer.", self._secrets())
        assert result == {"integer": False, "proper_noun": False, "color": False}

    def test_case_insensitive_uppercase(self):
        """Values in ALL CAPS should still match."""
        output = (
            kv.SECRETS["integer"].upper() + " " +
            kv.SECRETS["proper_noun"].upper() + " " +
            kv.SECRETS["color"].upper()
        )
        result = kv.parse_secrets_from_output(output, self._secrets())
        assert all(result.values()), "Case-insensitive match failed for uppercase"

    def test_case_insensitive_mixed(self):
        """Mixed case variations should match."""
        output = "ChArTrEuSe KaToWiCe 482917"
        result = kv.parse_secrets_from_output(output, self._secrets())
        assert result["color"] is True
        assert result["proper_noun"] is True
        assert result["integer"] is True

    def test_whitespace_around_value(self):
        """Values surrounded by various whitespace still match."""
        output = f"  {kv.SECRETS['integer']}  \n  {kv.SECRETS['proper_noun']}  \t{kv.SECRETS['color']}"
        result = kv.parse_secrets_from_output(output, self._secrets())
        assert all(result.values())

    def test_partial_match_integer(self):
        """A substring of the integer value in a longer number should NOT match
        unless the exact value string is present (substring check is inclusive)."""
        # '482917' is a substring of '9482917'; the function uses 'in' so it
        # would match inside a longer number.  Document this known behaviour
        # rather than marking it as a bug -- the secrets are chosen to be
        # unlikely sub-strings of incidental prose.
        output = "the value is 9482917 here"
        result = kv.parse_secrets_from_output(output, self._secrets())
        # The secret '482917' IS a substring of '9482917', so found=True.
        # This test documents the behaviour, not a requirement to reject it.
        assert result["integer"] is True

    def test_empty_output(self):
        """Empty output -> all False, no exception."""
        result = kv.parse_secrets_from_output("", self._secrets())
        assert result == {"integer": False, "proper_noun": False, "color": False}


# ---------------------------------------------------------------------------
# 4. Config parsing
# ---------------------------------------------------------------------------

class TestParseConfigs:
    """Tests for parse_configs()."""

    def test_single_config(self):
        configs = kv.parse_configs("k=q8_0,v=q8_0")
        assert len(configs) == 1
        assert configs[0].k == "q8_0"
        assert configs[0].v == "q8_0"
        assert configs[0].boundary == 0

    def test_multiple_configs_semicolon(self):
        configs = kv.parse_configs("k=q8_0,v=q8_0;k=turbo3,v=turbo2")
        assert len(configs) == 2
        assert configs[1].k == "turbo3"
        assert configs[1].v == "turbo2"

    def test_boundary_field(self):
        configs = kv.parse_configs("k=turbo3,v=turbo2,boundary=16")
        assert configs[0].boundary == 16

    def test_trailing_semicolon_ignored(self):
        """A trailing semicolon should not produce an empty config."""
        configs = kv.parse_configs("k=q8_0,v=q8_0;")
        assert len(configs) == 1

    def test_missing_k_raises(self):
        with pytest.raises(ValueError, match="k="):
            kv.parse_configs("v=q8_0")

    def test_missing_v_raises(self):
        with pytest.raises(ValueError, match="v="):
            kv.parse_configs("k=q8_0")

    def test_empty_string_raises(self):
        with pytest.raises(ValueError):
            kv.parse_configs("")

    def test_whitespace_stripped(self):
        """Spaces around values should not cause failures."""
        configs = kv.parse_configs("k=q8_0 , v=q8_0")
        assert configs[0].k == "q8_0"
        assert configs[0].v == "q8_0"


# ---------------------------------------------------------------------------
# 5. Context parsing
# ---------------------------------------------------------------------------

class TestParseContexts:
    """Tests for parse_contexts()."""

    def test_single(self):
        assert kv.parse_contexts("4096") == [4096]

    def test_multiple(self):
        assert kv.parse_contexts("1024,8192,16384") == [1024, 8192, 16384]

    def test_empty_raises(self):
        with pytest.raises(ValueError):
            kv.parse_contexts("")

    def test_whitespace_between(self):
        assert kv.parse_contexts("1024, 8192") == [1024, 8192]


# ---------------------------------------------------------------------------
# 6. KVConfig label
# ---------------------------------------------------------------------------

class TestKVConfigLabel:
    """Tests for KVConfig.label()."""

    def test_no_boundary(self):
        cfg = kv.KVConfig(k="q8_0", v="q8_0", boundary=0)
        assert cfg.label() == "k=q8_0,v=q8_0"

    def test_with_boundary(self):
        cfg = kv.KVConfig(k="turbo3", v="turbo2", boundary=16)
        assert cfg.label() == "k=turbo3,v=turbo2,boundary=16"

    def test_label_stable(self):
        """label() returns the same string on repeated calls (no state mutation)."""
        cfg = kv.KVConfig(k="turbo3", v="f16", boundary=0)
        assert cfg.label() == cfg.label()


# ---------------------------------------------------------------------------
# 7. llama-cli arg builder
# ---------------------------------------------------------------------------

class TestBuildLlamaCLIArgs:
    """Tests for build_llama_cli_args()."""

    def _base_config(self, **kwargs):
        defaults = dict(k="q8_0", v="q8_0", boundary=0)
        defaults.update(kwargs)
        return kv.KVConfig(**defaults)

    def test_required_flags_present(self):
        args = kv.build_llama_cli_args(
            llama_cli=Path("/usr/bin/llama-cli"),
            model=Path("/models/test.gguf"),
            prompt="hello",
            context_length=1024,
            config=self._base_config(),
        )
        assert "--model" in args
        assert "--ctx-size" in args
        assert "--cache-type-k" in args
        assert "--cache-type-v" in args
        assert "--n-predict" in args

    def test_cache_type_values(self):
        args = kv.build_llama_cli_args(
            llama_cli=Path("/bin/llama-cli"),
            model=Path("/m.gguf"),
            prompt="test",
            context_length=512,
            config=self._base_config(k="turbo3", v="turbo2"),
        )
        k_idx = args.index("--cache-type-k")
        v_idx = args.index("--cache-type-v")
        assert args[k_idx + 1] == "turbo3"
        assert args[v_idx + 1] == "turbo2"

    def test_no_boundary_flag_when_zero(self):
        """boundary=0 must NOT add --cache-quant-boundary to the args."""
        args = kv.build_llama_cli_args(
            llama_cli=Path("/bin/llama-cli"),
            model=Path("/m.gguf"),
            prompt="test",
            context_length=512,
            config=self._base_config(boundary=0),
        )
        assert "--cache-quant-boundary" not in args

    def test_boundary_flag_when_nonzero(self):
        """boundary>0 must add --cache-quant-boundary with the correct value."""
        args = kv.build_llama_cli_args(
            llama_cli=Path("/bin/llama-cli"),
            model=Path("/m.gguf"),
            prompt="test",
            context_length=512,
            config=self._base_config(boundary=16),
        )
        assert "--cache-quant-boundary" in args
        b_idx = args.index("--cache-quant-boundary")
        assert args[b_idx + 1] == "16"

    def test_n_predict_value(self):
        args = kv.build_llama_cli_args(
            llama_cli=Path("/bin/llama-cli"),
            model=Path("/m.gguf"),
            prompt="test",
            context_length=512,
            config=self._base_config(),
            n_predict=32,
        )
        idx = args.index("--n-predict")
        assert args[idx + 1] == "32"

    def test_ctx_size_value(self):
        args = kv.build_llama_cli_args(
            llama_cli=Path("/bin/llama-cli"),
            model=Path("/m.gguf"),
            prompt="x",
            context_length=4096,
            config=self._base_config(),
        )
        idx = args.index("--ctx-size")
        assert args[idx + 1] == "4096"

    def test_no_display_prompt_flag(self):
        """--no-display-prompt must be present to suppress echoing long prompts."""
        args = kv.build_llama_cli_args(
            llama_cli=Path("/bin/llama-cli"),
            model=Path("/m.gguf"),
            prompt="x",
            context_length=512,
            config=self._base_config(),
        )
        assert "--no-display-prompt" in args


# ---------------------------------------------------------------------------
# 8. CSV output
# ---------------------------------------------------------------------------

class TestWriteCSV:
    """Tests for write_csv()."""

    def _make_result(self, label="k=q8_0,v=q8_0", ctx=1024, found=3, total=3, passed=True):
        r = kv.RunResult(
            config_label=label,
            context_length=ctx,
            secrets_found=found,
            secrets_total=total,
            passed=passed,
        )
        r._per_secret = {"integer": True, "proper_noun": True, "color": True}  # type: ignore[attr-defined]
        return r

    def test_csv_has_header_and_rows(self, tmp_path):
        results = [self._make_result(), self._make_result(label="k=turbo3,v=turbo2", passed=False, found=2)]
        path = tmp_path / "out.csv"
        kv.write_csv(results, path)
        assert path.exists()
        with path.open(newline="") as fh:
            rows = list(csv.DictReader(fh))
        assert len(rows) == 2

    def test_csv_field_names(self, tmp_path):
        results = [self._make_result()]
        path = tmp_path / "out.csv"
        kv.write_csv(results, path)
        with path.open(newline="") as fh:
            reader = csv.DictReader(fh)
            fieldnames = reader.fieldnames
        for expected in kv.CSV_FIELDNAMES:
            assert expected in fieldnames, f"Missing CSV field: {expected}"

    def test_csv_status_pass(self, tmp_path):
        results = [self._make_result(passed=True)]
        path = tmp_path / "out.csv"
        kv.write_csv(results, path)
        with path.open(newline="") as fh:
            rows = list(csv.DictReader(fh))
        assert rows[0]["status"] == "PASS"

    def test_csv_status_fail(self, tmp_path):
        results = [self._make_result(passed=False, found=1)]
        path = tmp_path / "out.csv"
        kv.write_csv(results, path)
        with path.open(newline="") as fh:
            rows = list(csv.DictReader(fh))
        assert rows[0]["status"] == "FAIL"

    def test_csv_error_field(self, tmp_path):
        r = self._make_result()
        r.error = "llama-cli timed out"
        path = tmp_path / "out.csv"
        kv.write_csv([r], path)
        with path.open(newline="") as fh:
            rows = list(csv.DictReader(fh))
        assert "timed out" in rows[0]["error"]


# ---------------------------------------------------------------------------
# 9. CLI exit codes
# ---------------------------------------------------------------------------

class TestCLIExitCodes:
    """Integration-style tests that invoke the CLI as a subprocess.

    These tests deliberately do NOT invoke llama-cli or require a real model.
    They only test argument validation and early-exit behaviour.
    """

    def _run_cli(self, args: list[str]) -> subprocess.CompletedProcess:
        """Run the validator CLI with the given arguments."""
        return subprocess.run(
            [sys.executable, str(Path(__file__).parent.parent / "scripts" / "kv_quant_validator.py")]
            + args,
            capture_output=True,
            text=True,
        )

    def test_help_exits_zero(self):
        """--help must exit 0."""
        result = self._run_cli(["--help"])
        assert result.returncode == 0

    def test_missing_model_exits_nonzero(self):
        """A model path that does not exist must exit non-zero with a useful message."""
        result = self._run_cli(["--model", "/nonexistent/model.gguf"])
        assert result.returncode != 0
        # argparse error messages go to stderr
        combined = result.stderr + result.stdout
        assert "model" in combined.lower() or "error" in combined.lower()

    def test_missing_required_model_arg_exits_nonzero(self):
        """Omitting --model entirely must exit non-zero."""
        result = self._run_cli([])
        assert result.returncode != 0

    def test_bad_configs_exits_nonzero(self):
        """Malformed --configs (missing k=) must exit non-zero."""
        result = self._run_cli([
            "--model", "/nonexistent/model.gguf",
            "--configs", "v=q8_0",  # missing k=
        ])
        assert result.returncode != 0

    def test_grid_missing_file_exits_nonzero(self):
        """A --grid path that doesn't exist must exit non-zero."""
        result = self._run_cli([
            "--model", "/nonexistent/model.gguf",
            "--grid", "/nonexistent/grid.yaml",
        ])
        assert result.returncode != 0
