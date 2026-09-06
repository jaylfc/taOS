# KV Cache Quant Validator

`scripts/kv_quant_validator.py` is the needle-in-haystack quality gate that
TAOS requires before defaulting to any asymmetric KV cache configuration on a
model.  Running it once per (model, KV config) pair is mandatory.  Skipping
it is how users end up with gibberish outputs at high context depths.

## Background

llama.cpp KV cache quantisation compresses the K and V attention caches.
Asymmetric configs (e.g. turbo3 for K, turbo2 for V) can degrade recall
significantly on some model families.  On Qwen2.5, the degradation is silent
until the context is deep enough to expose it -- a perplexity test alone will
not catch it.

The validator places three deterministic "secrets" at known positions in a
long filler context, then asks the model to retrieve them.  A model under a
degraded KV config will fail to recall one or more secrets.

## When to run it

Run the validator in these situations:

1. **First-attach worker benchmark** -- after a new worker joins the cluster
   and its hardware has been identified, run the validator to establish the
   safe KV config baseline for each model you intend to serve from that
   worker.

2. **Model download** -- whenever a new GGUF lands in the model store, before
   enabling any non-default KV quant.  The validator is fast; a two-config
   two-context run takes a few minutes.

3. **Before default-enabling a new KV config** -- if you are about to set a
   new `kv_cache_quant_k` / `kv_cache_quant_v` combination as the default for
   a model in the cluster config, the validator must pass for every context
   length you intend to serve.

## Prerequisites

- Python 3.10+, no extra packages beyond the stdlib
- A llama-cli binary with `--cache-type-k` / `--cache-type-v` support.
  The standard path on TAOS worker nodes is
  `~/llama-cpp-turboquant/build/bin/llama-cli`.
  Pass `--llama-cli <path>` to override auto-detection.
- A GGUF model file

## Installation

No install step required.  The script is a single self-contained file.  Clone
or update the tinyagentos repo and run directly with Python 3.

## Basic usage

```bash
python scripts/kv_quant_validator.py \
    --model /path/to/qwen2.5-7b-Q4_K_M.gguf \
    --configs "k=q8_0,v=q8_0;k=turbo3,v=turbo2" \
    --contexts 4096,16384
```

Output:

```
Model      : /path/to/qwen2.5-7b-Q4_K_M.gguf
llama-cli  : /home/jay/llama-cpp-turboquant/build/bin/llama-cli
Configs    : 2
             k=q8_0,v=q8_0
             k=turbo3,v=turbo2
Contexts   : [4096, 16384]
n-predict  : 64
Baseline   : False
CSV output : kv_quant_results.csv

[1/4] config=k=q8_0,v=q8_0 ctx=4096 ... PASS (3/3)
[2/4] config=k=turbo3,v=turbo2 ctx=4096 ... FAIL (1/3)
[3/4] config=k=q8_0,v=q8_0 ctx=16384 ... PASS (3/3)
[4/4] config=k=turbo3,v=turbo2 ctx=16384 ... FAIL (0/3)

KV Quant Validation Results
---------------------------------------------------
config             context  secrets  status
---------------------------------------------------
k=q8_0,v=q8_0        4096      3/3  PASS
k=turbo3,v=turbo2     4096      1/3  FAIL
k=q8_0,v=q8_0       16384      3/3  PASS
k=turbo3,v=turbo2    16384      0/3  FAIL
---------------------------------------------------
Total: 4  PASS: 2  FAIL/ERROR: 2

CSV written to: kv_quant_results.csv
FAIL: 2 run(s) did not pass.
```

Exit code is 0 only when every run passes.

## Options

| Flag | Default | Description |
|------|---------|-------------|
| `--model PATH` | required | Path to the GGUF file |
| `--llama-cli PATH` | auto-detect | Path to the llama-cli binary |
| `--configs SPEC` | `k=q8_0,v=q8_0` | Semicolon-separated k/v config specs |
| `--contexts LENGTHS` | `4096` | Comma-separated context lengths |
| `--n-predict N` | `64` | Max generation tokens per run |
| `--csv PATH` | `kv_quant_results.csv` | Output CSV path |
| `--timeout SECONDS` | `300` | Per-run llama-cli timeout |
| `--grid YAML` | none | YAML grid config (overrides --configs/--contexts) |
| `--baseline` | off | Run fp16/fp16 first as the reference |
| `--verbose` / `-v` | off | Print raw model output |

## Config spec format

The `--configs` argument accepts semicolon-separated entries.  Each entry is a
comma-separated list of `key=value` pairs:

```
k=<quant_type>,v=<quant_type>[,boundary=<layer_count>]
```

Examples:

```
k=q8_0,v=q8_0
k=turbo3,v=turbo2
k=turbo3,v=turbo2,boundary=16
k=q8_0,v=q8_0;k=turbo3,v=turbo2;k=turbo3,v=turbo2,boundary=16
```

The `boundary` field activates the `--cache-quant-boundary` turboquant flag,
which splits the KV cache strategy at the specified layer.  Layers below the
boundary use the K config; layers above use the V config.  This is relevant
for the Qwen2.5 family where the early attention layers are more
quantisation-sensitive.

## Grid mode

For exhaustive testing across all k x v combinations, create a YAML file:

```yaml
k_types:
  - q8_0
  - turbo3
  - turbo2
v_types:
  - q8_0
  - turbo3
  - turbo2
contexts:
  - 1024
  - 8192
  - 16384
boundary: 0
```

Then run:

```bash
python scripts/kv_quant_validator.py \
    --model /path/to/model.gguf \
    --grid scripts/kv_grid.yaml
```

This will run 3 x 3 x 3 = 27 combinations.

## Baseline mode

The `--baseline` flag runs fp16/fp16 first at each context length and uses
its recall set as the pass requirement.  This is useful when the model itself
has known retrieval gaps (e.g. poor instruction following at very short
context windows) that would otherwise penalise every config equally.

```bash
python scripts/kv_quant_validator.py \
    --model /path/to/model.gguf \
    --configs "k=turbo3,v=turbo2" \
    --contexts 8192 \
    --baseline
```

A config passes in baseline mode only if it recalls every secret the fp16
baseline also recalled.

## CSV output

The CSV contains one row per (config, context) run:

| Column | Description |
|--------|-------------|
| `config` | Config label, e.g. `k=turbo3,v=turbo2` |
| `context_length` | Integer context size used |
| `secrets_found` | Count of secrets recalled |
| `secrets_total` | Count of secrets attempted (3 in standard mode) |
| `integer_found` | True/False for the integer secret |
| `proper_noun_found` | True/False for the city name secret |
| `color_found` | True/False for the colour secret |
| `status` | `PASS`, `FAIL`, or `ERROR` |
| `error` | Error message if llama-cli failed, else empty |

## Running the tests

```bash
pytest tests/test_kv_quant_validator.py -v
```

46 tests, no llama-cli required.  All tests use stdlib only.

## Secrets used

The three secrets are hardcoded and deterministic:

- Integer: `482917`
- City (proper noun): `Katowice`
- Colour: `chartreuse`

These values were chosen because they are unlikely to appear as incidental
tokens in standard filler prose.  Do not change them without updating the
tests, as result CSVs from different runs are only comparable when the same
secrets are used.
