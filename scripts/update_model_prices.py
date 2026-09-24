#!/usr/bin/env python3
"""Refresh the vendored LLM price table used by tinyagentos.llm_usage.

    python scripts/update_model_prices.py            # latest upstream commit
    python scripts/update_model_prices.py <sha>      # a pinned commit

Source: BerriAI/litellm `model_prices_and_context_window.json` at the repo root. That file sits outside
litellm's `enterprise/` directory, so it is MIT-licensed (see tinyagentos/llm_usage/data/NOTICE).
This script is the ONLY way the table changes: never hand-edit the JSON, and never fetch it at runtime.
It keeps just what taOS prices (text chat/completion/embedding/responses models, and the per-token rate
fields `tinyagentos.llm_usage.pricing` reads), and records the upstream commit it came from.
"""
import json
import re
import subprocess
import sys
import urllib.request
from pathlib import Path

REPO = "BerriAI/litellm"
PATH = "model_prices_and_context_window.json"
OUT = Path(__file__).resolve().parent.parent / "tinyagentos" / "llm_usage" / "data" / "model_prices.json"
MODES = {"chat", "completion", "embedding", "responses", None}
RATE = re.compile(r"^(input_cost_per_token|output_cost_per_token|cache_read_input_token_cost|"
                  r"cache_creation_input_token_cost)(_above_\d+k?_tokens)?$|^output_cost_per_reasoning_token$")


def latest_sha() -> str:
    out = subprocess.run(["gh", "api", f"repos/{REPO}/commits?path={PATH}&per_page=1", "--jq", ".[0].sha"],
                         capture_output=True, text=True, check=True).stdout.strip()
    if not re.fullmatch(r"[0-9a-f]{40}", out):
        sys.exit(f"could not resolve the latest commit for {PATH}: {out!r}")
    return out


def main() -> None:
    sha = sys.argv[1] if len(sys.argv) > 1 else latest_sha()
    url = f"https://raw.githubusercontent.com/{REPO}/{sha}/{PATH}"
    with urllib.request.urlopen(url, timeout=60) as r:
        src = json.load(r)
    src.pop("sample_spec", None)
    models = {}
    for key, entry in sorted(src.items()):
        if not isinstance(entry, dict) or entry.get("mode") not in MODES:
            continue
        rates = {k: v for k, v in entry.items() if RATE.match(k) and isinstance(v, (int, float))}
        if not rates:
            continue
        rates["litellm_provider"] = entry.get("litellm_provider")
        if entry.get("mode"):
            rates["mode"] = entry["mode"]
        models[key] = rates
    doc = {"_source": {"repo": REPO, "path": PATH, "commit": sha, "licence": "MIT (see NOTICE)"},
           "models": models}
    OUT.write_text(json.dumps(doc, indent=0, sort_keys=True, separators=(",", ":")) + "\n")
    print(f"wrote {len(models)} models from {REPO}@{sha[:12]} -> {OUT} ({OUT.stat().st_size / 1e6:.2f} MB)")


if __name__ == "__main__":
    main()
