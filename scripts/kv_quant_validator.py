#!/usr/bin/env python3
"""kv_quant_validator.py -- KV cache quantisation quality gate for TinyAgentOS.

Purpose
-------
Before TAOS defaults to any asymmetric KV cache configuration on a model (e.g.
turbo3 for K and turbo2 for V), we must confirm the model can still retrieve
information reliably from long contexts under that config.  A degraded KV quant
can cause silent gibberish at high context depths -- this harness catches that
before it ships to users.

The test methodology is a "needle in a haystack" recall benchmark:
  1. Build a long context prompt that hides three deterministic "secrets" at
     known fractional positions inside a wall of filler text.
  2. Ask the model to recall all three secrets verbatim.
  3. Parse the model's output for each secret using substring matching.
  4. Report pass/fail per (config, context_length) combination.

Usage
-----
    python scripts/kv_quant_validator.py \\
        --model /path/to/model.gguf \\
        --configs "k=q8_0,v=q8_0;k=turbo3,v=turbo2" \\
        --contexts 1024,8192 \\
        --llama-cli /path/to/llama-cli

See --help for full option list.
"""

from __future__ import annotations

import argparse
import csv
import os
import shutil
import subprocess
import sys
import textwrap
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

# ---------------------------------------------------------------------------
# Data types
# ---------------------------------------------------------------------------

@dataclass
class KVConfig:
    """A single KV cache quantisation configuration to test."""
    k: str          # K-cache quant type string, e.g. "q8_0" or "turbo3"
    v: str          # V-cache quant type string
    boundary: int   # Layer boundary for split configs; 0 means uniform (no split)

    def label(self) -> str:
        """Human-readable identifier used in CSV output and log lines."""
        if self.boundary:
            return f"k={self.k},v={self.v},boundary={self.boundary}"
        return f"k={self.k},v={self.v}"


@dataclass
class RunResult:
    """Outcome of a single (config, context_length) evaluation run."""
    config_label: str
    context_length: int
    secrets_found: int
    secrets_total: int
    passed: bool
    raw_output: str = field(default="", repr=False)
    error: Optional[str] = None

    def status(self) -> str:
        if self.error:
            return "ERROR"
        return "PASS" if self.passed else "FAIL"


# ---------------------------------------------------------------------------
# Needle / haystack prompt construction
# ---------------------------------------------------------------------------

# The three secrets are deterministic -- same values every run so that results
# across different invocations are directly comparable.
SECRETS = {
    "integer":     "482917",
    "proper_noun": "Katowice",
    "color":       "chartreuse",
}

# Filler paragraph used to pad context to the target length.  Plain prose with
# no numeric or colour tokens that could collide with secrets.
_FILLER_PARAGRAPH = (
    "The mountain range extended far beyond the horizon, its jagged peaks "
    "cutting silhouettes against a pale sky.  Researchers had spent decades "
    "mapping the terrain, noting fault lines, mineral deposits, and ancient "
    "lake beds.  The expedition journals filled shelf after shelf in the "
    "university library, each volume carefully indexed and cross-referenced.  "
    "Local guides passed the knowledge down through generations, memorising "
    "routes that no map had yet captured."
)

def _filler_block(target_chars: int) -> str:
    """Return a block of filler text approximately target_chars characters long."""
    reps = max(1, target_chars // len(_FILLER_PARAGRAPH) + 1)
    return (_FILLER_PARAGRAPH + " ") * reps


def build_needle_prompt(context_length: int) -> tuple[str, dict[str, str]]:
    """Construct a needle-in-haystack prompt padded to approximately context_length tokens.

    The three secrets are injected at 25%, 50%, and 75% of the filler body so
    that no single secret is trivially at the very start or end.

    Returns
    -------
    prompt : str
        The full prompt string ready to pass to the model.
    secrets : dict[str, str]
        Mapping of secret kind -> secret value (same as the module-level SECRETS
        constant; returned here so callers do not need to import it separately).
    """
    # Approximate token-to-character ratio for English prose: ~4 chars/token.
    # We reserve ~80 tokens for the instruction header and retrieval question,
    # so the filler body target is (context_length - 80) * 4 characters.
    filler_char_budget = max(200, (context_length - 80) * 4)

    # Split the budget into four equal segments; secrets go between them.
    seg = filler_char_budget // 4
    seg_a = _filler_block(seg)
    seg_b = _filler_block(seg)
    seg_c = _filler_block(seg)
    seg_d = _filler_block(seg)

    secret_int   = SECRETS["integer"]
    secret_noun  = SECRETS["proper_noun"]
    secret_color = SECRETS["color"]

    # Each secret is wrapped in an explicit marker sentence so the model cannot
    # claim it never appeared in the context.
    needle_1 = f"[SECRET-A: the special number is {secret_int}]"
    needle_2 = f"[SECRET-B: the special city is {secret_noun}]"
    needle_3 = f"[SECRET-C: the special color is {secret_color}]"

    # Retrieval instruction prepended so the model knows what to look for.
    header = textwrap.dedent(f"""\
        You are a precise information retrieval assistant.
        The following text contains three hidden secrets labelled SECRET-A,
        SECRET-B, and SECRET-C.  After the text ends, you will be asked to
        state each secret value exactly as it appears.

        --- BEGIN TEXT ---
        """)

    # Interleave filler and needles at 25 / 50 / 75 percent positions.
    body = (
        seg_a + "\n\n" +
        needle_1 + "\n\n" +
        seg_b + "\n\n" +
        needle_2 + "\n\n" +
        seg_c + "\n\n" +
        needle_3 + "\n\n" +
        seg_d
    )

    footer = textwrap.dedent(f"""\

        --- END TEXT ---

        Now state the three secret values you found in the text above.
        Format your answer exactly like this:
        SECRET-A: <value>
        SECRET-B: <value>
        SECRET-C: <value>
        """)

    prompt = header + body + footer
    return prompt, dict(SECRETS)


# ---------------------------------------------------------------------------
# Output parsing
# ---------------------------------------------------------------------------

def parse_secrets_from_output(output: str, secrets: dict[str, str]) -> dict[str, bool]:
    """Check whether each secret value appears in the model output.

    Matching is case-insensitive substring matching.  We do not require the
    exact label prefix (SECRET-A:) because some models rephrase the framing
    while still producing the correct value.

    Returns
    -------
    dict mapping secret kind -> True if the value was found in output.
    """
    output_lower = output.lower()
    results: dict[str, bool] = {}
    for kind, value in secrets.items():
        results[kind] = value.lower() in output_lower
    return results


# ---------------------------------------------------------------------------
# Config parsing
# ---------------------------------------------------------------------------

def parse_configs(configs_str: str) -> list[KVConfig]:
    """Parse the --configs argument into KVConfig objects.

    Format: semicolon-separated entries, each entry is comma-separated k/v/boundary
    key=value pairs.  The boundary field is optional and defaults to 0.

    Examples
    --------
    "k=q8_0,v=q8_0"
    "k=turbo3,v=turbo2;k=q4_0,v=q4_0,boundary=16"
    """
    configs: list[KVConfig] = []
    for entry in configs_str.split(";"):
        entry = entry.strip()
        if not entry:
            continue
        parts = {
            kk.strip(): vv.strip()
            for p in entry.split(",")
            if "=" in p
            for kk, vv in [p.split("=", 1)]
        }
        if "k" not in parts or "v" not in parts:
            raise ValueError(
                f"Config entry '{entry}' must contain at least k=... and v=... fields."
            )
        configs.append(KVConfig(
            k=parts["k"].strip(),
            v=parts["v"].strip(),
            boundary=int(parts.get("boundary", 0)),
        ))
    if not configs:
        raise ValueError("--configs produced no valid entries.")
    return configs


def parse_contexts(contexts_str: str) -> list[int]:
    """Parse a comma-separated list of integer context lengths."""
    lengths: list[int] = []
    for tok in contexts_str.split(","):
        tok = tok.strip()
        if tok:
            lengths.append(int(tok))
    if not lengths:
        raise ValueError("--contexts produced no valid context lengths.")
    return lengths


# ---------------------------------------------------------------------------
# llama-cli invocation
# ---------------------------------------------------------------------------

# Candidate paths where the turboquant llama-cli binary might live.
_LLAMA_CLI_CANDIDATES = [
    # Fedora LXC path used in the standard TAOS worker install
    Path("/home/jay/llama-cpp-turboquant/build/bin/llama-cli"),
    # Generic system install
    Path("/usr/local/bin/llama-cli"),
    Path("/usr/bin/llama-cli"),
]


def auto_detect_llama_cli() -> Optional[Path]:
    """Return the first llama-cli binary found on candidate paths, or None."""
    # Also check $PATH via shutil.which
    which_result = shutil.which("llama-cli")
    if which_result:
        return Path(which_result)
    for candidate in _LLAMA_CLI_CANDIDATES:
        if candidate.exists():
            return candidate
    return None


def build_llama_cli_args(
    llama_cli: Path,
    model: Path,
    prompt: str,
    context_length: int,
    config: KVConfig,
    n_predict: int = 64,
) -> list[str]:
    """Build the subprocess argument list for a single llama-cli invocation.

    Parameters
    ----------
    llama_cli    : path to the llama-cli binary
    model        : path to the GGUF model file
    prompt       : the full needle-in-haystack prompt string
    context_length: -c value passed to llama-cli
    config       : KVConfig specifying k/v quant types and optional boundary layer
    n_predict    : max tokens to generate (32-64 is sufficient for the retrieval task)

    Notes on flags used
    -------------------
    --cache-type-k / --cache-type-v: standard KV cache quant flags in llama.cpp
    --cache-quant-boundary: turboquant-specific flag for layer-split configs
    -c: context window size
    -n: max generation tokens
    --no-display-prompt: suppress echoing the (very long) prompt to stdout
    --simple-io: plain output without ANSI, easier to parse
    """
    args: list[str] = [
        str(llama_cli),
        "--model", str(model),
        "--ctx-size", str(context_length),
        "--n-predict", str(n_predict),
        "--cache-type-k", config.k,
        "--cache-type-v", config.v,
        "--no-display-prompt",
        "--simple-io",
        "--prompt", prompt,
    ]
    # Only pass the boundary flag when a non-zero boundary layer is specified.
    # This keeps the invocation identical to the baseline for uniform configs.
    if config.boundary:
        args += ["--cache-quant-boundary", str(config.boundary)]
    return args


def invoke_llama_cli(
    llama_cli: Path,
    model: Path,
    prompt: str,
    context_length: int,
    config: KVConfig,
    n_predict: int = 64,
    timeout_seconds: int = 300,
) -> tuple[str, Optional[str]]:
    """Run llama-cli and return (stdout_output, error_message_or_None).

    A non-zero exit code from llama-cli is treated as an error; stderr is
    captured and returned as the error message so callers can log it.
    """
    args = build_llama_cli_args(llama_cli, model, prompt, context_length, config, n_predict)
    try:
        result = subprocess.run(
            args,
            capture_output=True,
            text=True,
            timeout=timeout_seconds,
        )
    except subprocess.TimeoutExpired:
        return "", f"llama-cli timed out after {timeout_seconds}s"
    except FileNotFoundError:
        return "", f"llama-cli binary not found: {llama_cli}"

    if result.returncode != 0:
        stderr_snippet = result.stderr.strip()[-500:] if result.stderr else "(no stderr)"
        return result.stdout, f"llama-cli exited {result.returncode}: {stderr_snippet}"

    return result.stdout, None


# ---------------------------------------------------------------------------
# CSV output
# ---------------------------------------------------------------------------

CSV_FIELDNAMES = [
    "config",
    "context_length",
    "secrets_found",
    "secrets_total",
    "integer_found",
    "proper_noun_found",
    "color_found",
    "status",
    "error",
]


def write_csv(results: list[RunResult], path: Path) -> None:
    """Write the result list to a CSV file at path."""
    with path.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=CSV_FIELDNAMES)
        writer.writeheader()
        for r in results:
            # Per-secret flags are encoded in the raw_output field indirectly;
            # we need to re-derive them from the label and stored counts.
            # For simplicity we store them separately on RunResult via the
            # per_secret attribute set during the run loop below.
            per = getattr(r, "_per_secret", {})
            writer.writerow({
                "config":           r.config_label,
                "context_length":   r.context_length,
                "secrets_found":    r.secrets_found,
                "secrets_total":    r.secrets_total,
                "integer_found":    per.get("integer", ""),
                "proper_noun_found":per.get("proper_noun", ""),
                "color_found":      per.get("color", ""),
                "status":           r.status(),
                "error":            r.error or "",
            })


# ---------------------------------------------------------------------------
# Summary table (plain text)
# ---------------------------------------------------------------------------

def print_summary_table(results: list[RunResult]) -> None:
    """Print a human-readable summary table to stdout."""
    col_widths = {
        "config":   max(len("config"),   max(len(r.config_label) for r in results)),
        "ctx":      max(len("context"),  max(len(str(r.context_length)) for r in results)),
        "found":    len("secrets"),
        "status":   len("status"),
    }
    header = (
        f"{'config':<{col_widths['config']}}  "
        f"{'context':>{col_widths['ctx']}}  "
        f"{'secrets':>{col_widths['found']}}  "
        f"{'status'}"
    )
    sep = "-" * len(header)
    print()
    print("KV Quant Validation Results")
    print(sep)
    print(header)
    print(sep)
    for r in results:
        score = f"{r.secrets_found}/{r.secrets_total}"
        status = r.status()
        print(
            f"{r.config_label:<{col_widths['config']}}  "
            f"{r.context_length:>{col_widths['ctx']}}  "
            f"{score:>{col_widths['found']}}  "
            f"{status}"
        )
    print(sep)
    n_pass = sum(1 for r in results if r.passed and not r.error)
    n_fail = sum(1 for r in results if not r.passed or r.error)
    print(f"Total: {len(results)}  PASS: {n_pass}  FAIL/ERROR: {n_fail}")
    print()


# ---------------------------------------------------------------------------
# Grid mode helpers
# ---------------------------------------------------------------------------

def load_grid_yaml(path: Path) -> tuple[list[KVConfig], list[int]]:
    """Load a YAML grid config file and return (configs, context_lengths).

    Expected YAML structure::

        k_types: [q8_0, turbo3, turbo2]
        v_types: [q8_0, turbo3, turbo2]
        contexts: [1024, 8192, 16384]
        boundary: 0   # optional; applied to all combinations

    The function expands the full k x v Cartesian product.
    """
    import yaml  # only imported when --grid is used; yaml is a stdlib dep via pyyaml

    with path.open() as fh:
        data = yaml.safe_load(fh)

    k_types   = data.get("k_types", [])
    v_types   = data.get("v_types", [])
    contexts  = [int(c) for c in data.get("contexts", [])]
    boundary  = int(data.get("boundary", 0))

    configs: list[KVConfig] = []
    for k in k_types:
        for v in v_types:
            configs.append(KVConfig(k=str(k), v=str(v), boundary=boundary))

    if not configs:
        raise ValueError(f"Grid YAML at {path} produced no k/v combinations.")
    if not contexts:
        raise ValueError(f"Grid YAML at {path} has no context lengths.")

    return configs, contexts


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------

def build_arg_parser() -> argparse.ArgumentParser:
    """Return the fully configured argument parser."""
    parser = argparse.ArgumentParser(
        prog="kv_quant_validator",
        description=(
            "Needle-in-haystack KV cache quant quality gate for TinyAgentOS. "
            "Exits 0 only when every (config, context) combination recalls all "
            "three hidden secrets correctly."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=textwrap.dedent("""\
            Examples
            --------
            # Test two configs at two context lengths
            python scripts/kv_quant_validator.py \\
                --model /models/qwen2.5-7b.Q4_K_M.gguf \\
                --configs "k=q8_0,v=q8_0;k=turbo3,v=turbo2" \\
                --contexts 4096,16384

            # Full grid via YAML
            python scripts/kv_quant_validator.py \\
                --model /models/qwen2.5-7b.Q4_K_M.gguf \\
                --grid scripts/kv_grid.yaml

            # Baseline comparison (fp16/fp16 runs first, rest compared to it)
            python scripts/kv_quant_validator.py \\
                --model /models/qwen2.5-7b.Q4_K_M.gguf \\
                --configs "k=turbo3,v=turbo2" \\
                --contexts 8192 \\
                --baseline
        """),
    )
    parser.add_argument(
        "--model", required=True, metavar="PATH",
        help="Path to the GGUF model file to test.",
    )
    parser.add_argument(
        "--llama-cli", dest="llama_cli", default=None, metavar="PATH",
        help=(
            "Path to the llama-cli binary.  Defaults to auto-detection: checks "
            "$PATH, then ~/llama-cpp-turboquant/build/bin/llama-cli, then "
            "/usr/local/bin/llama-cli."
        ),
    )
    parser.add_argument(
        "--configs", default="k=q8_0,v=q8_0", metavar="SPEC",
        help=(
            "Semicolon-separated KV config specs.  Each spec is "
            "comma-separated key=value pairs: k=<type>,v=<type>[,boundary=N].  "
            "Example: \"k=q8_0,v=q8_0;k=turbo3,v=turbo2\""
        ),
    )
    parser.add_argument(
        "--contexts", default="4096", metavar="LENGTHS",
        help="Comma-separated list of context lengths to test.  Default: 4096.",
    )
    parser.add_argument(
        "--n-predict", dest="n_predict", type=int, default=64, metavar="N",
        help="Max tokens to generate per run.  32-64 is sufficient.  Default: 64.",
    )
    parser.add_argument(
        "--csv", default=None, metavar="PATH",
        help="Write CSV results to this path.  Default: kv_quant_results.csv.",
    )
    parser.add_argument(
        "--timeout", type=int, default=300, metavar="SECONDS",
        help="Per-run llama-cli timeout in seconds.  Default: 300.",
    )
    parser.add_argument(
        "--grid", default=None, metavar="YAML",
        help=(
            "Path to a YAML grid config file.  When supplied, --configs and "
            "--contexts are ignored.  See docs/kv-quant-validator.md for format."
        ),
    )
    parser.add_argument(
        "--baseline", action="store_true",
        help=(
            "Run fp16/fp16 first and treat its output as the ground-truth reference.  "
            "A config passes only if it finds every secret the baseline found."
        ),
    )
    parser.add_argument(
        "--verbose", "-v", action="store_true",
        help="Print raw llama-cli output for each run.",
    )
    return parser


def run_validation(
    model: Path,
    llama_cli: Path,
    configs: list[KVConfig],
    context_lengths: list[int],
    n_predict: int,
    timeout: int,
    baseline_mode: bool,
    verbose: bool,
) -> list[RunResult]:
    """Execute all (config, context) combinations and return the result list.

    In baseline_mode the fp16/fp16 config runs first at each context length.
    The baseline's found-secret set becomes the pass requirement for that
    context length (so if the baseline misses a secret due to a model quirk,
    other configs are not penalised for that miss).
    """
    results: list[RunResult] = []
    total_runs = len(configs) * len(context_lengths)
    run_n = 0

    # In baseline mode we inject fp16/fp16 at the front of the config list
    # per context length and record what it found.
    baseline_config = KVConfig(k="f16", v="f16", boundary=0)

    for ctx in context_lengths:
        prompt, secrets = build_needle_prompt(ctx)
        secrets_total = len(secrets)

        # Determine the pass threshold for this context length.
        if baseline_mode:
            print(
                f"  [baseline] running fp16/fp16 at ctx={ctx} to establish reference...",
                flush=True,
            )
            b_out, b_err = invoke_llama_cli(
                llama_cli, model, prompt, ctx,
                baseline_config, n_predict, timeout,
            )
            if b_err:
                print(f"  [baseline] ERROR: {b_err}", file=sys.stderr)
                # Fall back to requiring all secrets
                required_secrets = set(secrets.keys())
            else:
                b_found = parse_secrets_from_output(b_out, secrets)
                required_secrets = {k for k, v in b_found.items() if v}
                print(
                    f"  [baseline] found {len(required_secrets)}/{secrets_total} secrets: "
                    + ", ".join(sorted(required_secrets))
                )
        else:
            required_secrets = set(secrets.keys())

        for config in configs:
            run_n += 1
            label = config.label()
            print(
                f"[{run_n}/{total_runs}] config={label} ctx={ctx} ... ",
                end="",
                flush=True,
            )

            output, error = invoke_llama_cli(
                llama_cli, model, prompt, ctx,
                config, n_predict, timeout,
            )

            if verbose and output:
                print()
                print("--- raw output ---")
                print(output[:2000])
                print("--- end output ---")

            per_secret = parse_secrets_from_output(output, secrets)

            # In baseline mode: only count secrets the baseline also found.
            if baseline_mode:
                effective_per = {k: v for k, v in per_secret.items() if k in required_secrets}
                secrets_found = sum(effective_per.values())
                effective_total = len(required_secrets)
            else:
                effective_per = per_secret
                secrets_found = sum(per_secret.values())
                effective_total = secrets_total

            passed = (secrets_found == effective_total) and error is None
            result = RunResult(
                config_label=label,
                context_length=ctx,
                secrets_found=secrets_found,
                secrets_total=effective_total,
                passed=passed,
                raw_output=output,
                error=error,
            )
            # Stash per-secret breakdown for CSV writer
            result._per_secret = per_secret  # type: ignore[attr-defined]

            results.append(result)
            print(f"{result.status()} ({secrets_found}/{effective_total})")
            if error:
                print(f"  error: {error}", file=sys.stderr)

    return results


def main() -> None:
    """CLI entry point."""
    parser = build_arg_parser()
    args = parser.parse_args()

    # --- Resolve model path ---
    model_path = Path(args.model)
    if not model_path.exists():
        parser.error(f"Model file not found: {model_path}")

    # --- Resolve llama-cli path ---
    if args.llama_cli:
        llama_cli_path = Path(args.llama_cli)
        if not llama_cli_path.exists():
            parser.error(f"llama-cli not found at specified path: {llama_cli_path}")
    else:
        llama_cli_path = auto_detect_llama_cli()
        if llama_cli_path is None:
            parser.error(
                "Could not auto-detect llama-cli.  Pass --llama-cli <path> explicitly.  "
                "Expected locations: $PATH, "
                + ", ".join(str(p) for p in _LLAMA_CLI_CANDIDATES)
            )
        print(f"Auto-detected llama-cli: {llama_cli_path}")

    # --- Resolve configs and contexts ---
    if args.grid:
        grid_path = Path(args.grid)
        if not grid_path.exists():
            parser.error(f"Grid YAML not found: {grid_path}")
        try:
            configs, context_lengths = load_grid_yaml(grid_path)
        except Exception as exc:
            parser.error(f"Failed to load grid YAML: {exc}")
    else:
        try:
            configs = parse_configs(args.configs)
        except ValueError as exc:
            parser.error(str(exc))
        try:
            context_lengths = parse_contexts(args.contexts)
        except ValueError as exc:
            parser.error(str(exc))

    csv_path = Path(args.csv) if args.csv else Path("kv_quant_results.csv")

    # --- Print run summary ---
    print(f"Model      : {model_path}")
    print(f"llama-cli  : {llama_cli_path}")
    print(f"Configs    : {len(configs)}")
    for c in configs:
        print(f"             {c.label()}")
    print(f"Contexts   : {context_lengths}")
    print(f"n-predict  : {args.n_predict}")
    print(f"Baseline   : {args.baseline}")
    print(f"CSV output : {csv_path}")
    print()

    # --- Run ---
    results = run_validation(
        model=model_path,
        llama_cli=llama_cli_path,
        configs=configs,
        context_lengths=context_lengths,
        n_predict=args.n_predict,
        timeout=args.timeout,
        baseline_mode=args.baseline,
        verbose=args.verbose,
    )

    # --- Output ---
    write_csv(results, csv_path)
    print_summary_table(results)
    print(f"CSV written to: {csv_path}")

    # Exit non-zero if any run failed or errored
    failures = [r for r in results if not r.passed or r.error]
    if failures:
        print(
            f"FAIL: {len(failures)} run(s) did not pass.  "
            "Review the table above and the CSV for details.",
            file=sys.stderr,
        )
        sys.exit(1)

    print("All runs passed.")
    sys.exit(0)


if __name__ == "__main__":
    main()
