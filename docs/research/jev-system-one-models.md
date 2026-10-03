# Jev and System One Models: Typed Fast Decisions for taOS Agent Navigation

**Date:** 2026-10-03
**Purpose:** Research card tsk-6xsyk2. What a TypeSafe "System One" model (Jev) is,
what it would cost and save inside the taOS agent loop, and which open-source or
local models are bundleable in an AGPL-3.0 repo and runnable on the
`Pi-NPU-16GB` profile (RK3588, 16 GB, 6 TOPS NPU).
**Card:** "there are now open source/local alternatives" -- confirmed, see
[section 2](#2-the-open-source--local-alternatives).

Every external claim below carries its source URL. **Every benchmark number in this
document is author-reported** on the publisher's own suite. The two registries we
lean on say so themselves: DecisionEval notes "Vendor-reported figures are labelled
as such and are not independently verified"
(<https://decisioneval.dev/compare/>), and the System One registry notes "Benchmarks
in this space are young and mostly self-reported on different datasets"
(<https://laya-ai.com/system-one-models>). Treat the tables as a shortlist input,
never as a verdict.

---

## 1. What a System One model is

### 1.1 The class

A "System One model" is a **discriminative** model. You hand it a `state` (text, a
JSON object, or an array of text) plus a map of **typed questions** whose answer
space you declare in advance, and it returns a typed value plus a probability for
every allowed answer, in one parallel pass. It never writes text
(<https://docs.typesafe.ai/concepts/system-one>,
<https://en.wikipedia.org/wiki/Jev_(AI_model)>).

The name is Kahneman's System 1 thinking: fast and intuitive, as against slow
deliberate System 2 reasoning (<https://docs.typesafe.ai/concepts/system-one>,
<https://typesafe.ai/blog/introducing-system-one-models-and-jev>).

The contrast TypeSafe draws against an LLM is not marketing fluff, it is structural:

| | LLM | System One model |
|---|---|---|
| Output | strings, to be parsed and validated | type-safe values inside a schema you defined |
| Sampling | sequential, one token at a time | parallel, all outputs in one query |
| Confidence | self-reported, inconsistent even when asked | calibrated, always attached |
| Training | RLHF / RLVR (human preference) | RLCD, Reinforcement Learning for Calibrated Decisions |

(<https://typesafe.ai/blog/introducing-system-one-models-and-jev>)

Wikipedia's framing adds the operational detail that matters most for an agent
runtime: Jev performs **zero-shot** classification over categories supplied at
prediction time, so a new answer space needs no retraining
(<https://en.wikipedia.org/wiki/Jev_(AI_model)>). Cloudflare describes the same
property as the reason this class exists: classifiers need retraining for every new
category, LLMs are non-deterministic and expensive, decision models do not
(<https://blog.cloudflare.com/clef-decision-models/>).

### 1.2 The three primitives

Every question is one of three types (<https://docs.typesafe.ai/api>):

| Primitive | Question | Answers | Returns |
|---|---|---|---|
| `noul` | "Does X hold?" | true / false | `noul` in `[0, 1]` |
| `choice` | "Which of these?" | up to **255** options, each with a rubric in `criteria` | chosen option, full probability map, `confidence` |
| `score` | "How much X, on this scale?" | ordered **2 to 10** levels | weighted score, level legend, probability map, `confidence` |

All questions in one request share one `state` and are answered in parallel
(<https://docs.typesafe.ai/concepts/system-one>). That is the whole performance
story: the state is read once and every question is a read-out of the same pass
(<https://www.langchain.com/blog/building-a-harness-with-jev>).

**Language:** English is the primary training language; other languages including
CJK are handled but not equally well (<https://docs.typesafe.ai/models>).

### 1.3 API shape

One endpoint: `POST https://api.typesafe.ai/v1/systemone`, bearer-keyed
(<https://docs.typesafe.ai/api>).

```json
{
  "model": "jev-latest",
  "state": {"focused_app": "projects", "message": "kick off the Qwen deploy"},
  "questions": {
    "target_app": {
      "type": "choice",
      "instructions": "Which app should the agent open?",
      "criteria": {
        "projects": "Project list, builds, deployments",
        "terminal": "Shell output and manual commands",
        "models": "Model catalogue and backend health"
      }
    },
    "needs_user_confirmation": {
      "type": "noul",
      "instructions": "Does this action start a production deployment?",
      "criteria": {"true": "Touches production", "false": "Local or read-only"}
    }
  }
}
```

The response returns `answers` keyed by the same question ids, each carrying `type`,
the typed value, `probabilities`, and (for `choice`/`score`) a `confidence` in
`[0, 1]` derived from the distribution. A `usage` object reports `input_tokens` and
`output_tokens` (<https://docs.typesafe.ai/api>).

Errors are ordinary HTTP: `401` bad key, `422` schema failure, `429` over the rate
limit, `529` overloaded (<https://docs.typesafe.ai/api>).

**`instructions` can be a structured object**, with the question in one field and
the data it references in others, so a question can point at a nested `state` value
by name (<https://docs.typesafe.ai/api>). This matters for taOS: a screenshot-derived
layout object is exactly this shape.

### 1.4 Latency, price, limits

| | Jev 1.13.0 |
|---|---|
| Price | **$42 / billion input tokens = $0.042 / Mtok**, output tokens free |
| Rate limits | 100K tokens/s, 80 requests/s |
| Context | 64k tokens per request; 32k for `state` + the single longest question |
| Input | text only: string, JSON object, or array of text. No image, audio or video |

(<https://docs.typesafe.ai/models>)

Latency, three ways:

- **TypeSafe's claim:** 70-500 ms end-to-end, 40x-200x faster than frontier LLMs,
  with peak figures of 193.6x faster and 444.6x cheaper on their own workflow evals
  (<https://typesafe.ai/blog/introducing-system-one-models-and-jev>).
- **Independent measurements of Jev through TypeSafe's own API** are consistently
  higher: 404 ms median (<https://huggingface.co/blog/manjunathshiva/opendecider-beats-laya-and-jev>),
  524.1 ms median / 536.0 ms p95 (<https://blog.cloudflare.com/clef-decision-models/>),
  710 ms per case (<https://github.com/ikermoel/open-alternative-jev>), 0.65 s p50
  (<https://github.com/wfzyx/von>).
- **The frontier-LLM baseline is 1-4 s for the same class of work:** 2.22 s for
  GPT-6 Astra, 4.27 s for Claude Fable 5.1, 1.02 s for MiniMax M3, on the same
  general-decision set (<https://huggingface.co/blog/manjunathshiva/opendecider-beats-laya-and-jev>).

So the defensible claim is **one to two orders of magnitude less latency than an
LLM turn**, not a specific multiple.

### 1.5 Licence: not bundleable

Jev is **proprietary, API-only, weights unpublished**
(<https://en.wikipedia.org/wiki/Jev_(AI_model)>). TypeSafe's own terms grant only a
"limited, non-exclusive, non-transferable, non-sublicensable, revocable license"
for the site, and state that use of the products is governed by a separate
agreement (<https://typesafe.ai/legal/terms>). Wikipedia records that TypeSafe has
not published the architecture, weights, or a technical paper, describing it only as
transformer-based and trained on synthetic data
(<https://en.wikipedia.org/wiki/Jev_(AI_model)>).

**For taOS this is a call, not an integration.** Calling Jev is possible (a hosted
dependency, `TYPESAFE_API_KEY` in config, per-call egress of user desktop state).
Bundling it is impossible, and routing screenshots through it is doubly bad: it is
text-only (<https://docs.typesafe.ai/models>) and taOS is a privacy-first self-hosted
OS (<https://typesafe.ai/legal/terms> lists zero data retention only for enterprise
agreements).

### 1.6 The part that actually matters: a confidence you can gate on

The design decision worth copying is not the wire format, it is the **calibrated
confidence attached to every answer**, which turns "ask the LLM what to do" into
"branch in code, escalate only below a threshold"
(<https://docs.typesafe.ai/concepts/system-one>). TypeSafe's argument is that a
model which can do a task 95% of the time but cannot say when it is in the other 5%
cannot be automated (<https://typesafe.ai/blog/introducing-system-one-models-and-jev>).

Measured on a public benchmark, at a 0.9 confidence gate:
`jevk5-4b` answers 65% of items that confidently and is right 96% of those times;
`gemma-4-e2b` reports 1.000 confidence on wrong answers
(<https://github.com/khimaros/verdict>). Calibration is a property of the
checkpoint, not of the API. **Measure the gate on taOS's own data.**

Two harnesses already do the thing we would be building:

- **Model routing.** `ModelRouterMiddleware` picks the cheapest model that can
  finish the task, from criteria you write, and leaves the probabilities in agent
  state (<https://www.langchain.com/blog/building-a-harness-with-jev>).
- **Auto Mode.** `AutoModeMiddleware(tools=["bash"])` scores a tool call for risk and
  **blocks it before the tool executes**. LangChain's framing is that coding
  harnesses shipped this classifier step and kept it inside closed-source harness
  code; a cheap fast classifier makes it available to everyone
  (<https://www.langchain.com/blog/building-a-harness-with-jev>).

Both are small, general, and directly portable. That is the strongest argument in
this card.

### 1.7 Reference adapter, for A/B only

TypeSafe ships [`system-one-adapter-python`](https://github.com/typesafe-ai/system-one-adapter-python)
(**MIT**), a drop-in `TypeSafeClient` replacement backed by LLM APIs. It constrains
an ordinary chat model to Jev's typed schema, with optional native structured
outputs, per-label probability normalisation, and corrective retries. It is how
TypeSafe's own evals measure LLMs (<https://typesafe.ai/blog/introducing-system-one-models-and-jev>).

Not a local model, but the cheapest possible way to run the **control arm** of the
PoC in section 4: same request shape, same answer fields, a frontier LLM instead of
a 68M encoder.

---

## 2. The open-source / local alternatives

Jev's weights are not public, and the ecosystem filled in within days. The
System One registry counted **more than twenty open-weights System One models by 24
September 2026, eleven of which serve Jev's own API**
(<https://systemonemodels.tech/blog/open-source-jev-alternatives>). DecisionEval's
directory holds 66 original models, **53 under Apache-2.0 or MIT**
(<https://decisioneval.dev/compare/>).

### 2.1 The licence rule for this repo

taOS is **AGPL-3.0** (`LICENSE` at the repo root). A model must be *bundleable*:

- **Apache-2.0 / MIT / BSD / permissive**: compatible with distribution as part of
  an AGPL-3.0 work. All the recommended options below qualify.
- **GPL-3.0-or-later**: bundleable, because AGPLv3 section 13 grants explicit
  permission to link or combine any GPLv3-covered work into a single combined work.
  The AGPL terms continue to apply to the AGPL-covered part, and section 13's network
  clause then reaches the combination
  (<https://spdx.org/licenses/AGPL-3.0-or-later.html>, and the FSF's plain reading:
  "written into section 13 of both the GPLv3 and the AGPLv3 is the explicit
  permission to link or combine any covered work under the other license",
  <https://www.fsf.org/bulletin/2021/fall/the-fundamentals-of-the-agplv3>). Keep
  such a component behind a process boundary, not a Python import.
- **Non-commercial / research-only / ShareAlike-with-NC / undefined**: not bundleable
  as a product dependency. Flagged below.

### 2.2 Tier 1: encoders, CPU-sized, bundleable today

| Model | Licence | Params / footprint | Runs on RK3588 16GB? | Notes |
|---|---|---|---|---|
| **[Anarkali](https://github.com/ToufiqQureshi/anarkali)** | **Apache-2.0** (Ettin backbone MIT, typed-decisions data Apache-2.0) | **68M**, one **273 MB ONNX file**, no PyTorch to serve, 512-token context | **Yes**, CPU; 110 ms/decision on a laptop CPU, 14 ms on a T4 | `/v1/systemone` server; `noul`/`choice`/`score` + `abstain` flag. 74.0% vs Jev 72.7% on typed-decisions (author-reported); ECE 0.135 vs Jev 0.144; answers at p>=0.7 are 94.8% right |
| **[Von](https://github.com/wfzyx/von)** | **Apache-2.0** | 395M ModernBERT, ~3 GB fetch | **Yes**, CPU via OpenVINO | `/v1/systemone`; raw p50 **96 ms on a 4-vCPU Xeon 8488C** (OpenVINO), 23 ms on an A10G. Ships `von calibrate` to refit the confidence map on your own labels. Highest stars in the space (816) |
| **[Laya](https://github.com/NandhaKishorM/laya)** (`laya-multilingual`) | **Apache-2.0** | 322M mmBERT | **Yes**, CPU; **193 ms** per question on a 4-core EPYC 9R14 | 100+ languages. The English 421M ModernBERT checkpoints measure 580-584 ms/question on the same box |
| **[OpenDecider](https://pypi.org/project/opendecider/)** (`-nano`) | **Apache-2.0** (Ettin MIT, Qwen3-4B Apache-2.0) | ~400M, 2.0 GiB, 2,048-token context | **Yes**, CPU, but ~10x slower than its 17 ms GPU figure | 0.796 on typed-decisions vs Jev 0.754 (author). Also `-small` at 4B / 8.9 GiB |
| **[OpenDecision](https://pypi.org/project/open-decision-ai/)** | **Apache-2.0** | ~150M ModernBERT-base, 558 MB zip, 0.96 GB at inference | **Yes**, CPU (~10x its 10 ms GPU figure) | Different API (`/v1/decisions`, not `/v1/systemone`). Good ECE 0.047 and order-invariance. **Its `score` head is documented as non-functional in v0.2** |

Latency sources: Von (<https://github.com/wfzyx/von>), Laya CPU
(<https://laya-ai.com/guides/laya-cpu-performance>), Anarkali
(<https://github.com/ToufiqQureshi/anarkali>), OpenDecider
(<https://pypi.org/project/opendecider/>), OpenDecision
(<https://pypi.org/project/open-decision-ai/>).

### 2.3 Tier 2: right licence, wrong hardware

| Model | Licence | Size | Why it is out for `Pi-NPU-16GB` |
|---|---|---|---|
| **[Clef / Clef-flash](https://huggingface.co/Cloudflare/clef)** (Cloudflare) | **Apache-2.0** | 27B / 9B | Cloudflare's product manager states Clef-flash needs **>= 41 GB VRAM** and Clef **85 GB VRAM** (<https://www.theregister.com/ai-and-ml/2026/10/01/cloudflare-tries-to-outplay-jev-with-open-weight-clef-models/5300649>). Also the most *interesting* option for the future: it is multimodal (image/video in), which Jev is not (<https://blog.cloudflare.com/clef-decision-models/>), and 64k context. Median 209 ms / 38.8 ms on Workers AI, $0.24/M hosted |
| **[Kev](https://github.com/jaredpalmer/kev)** | Apache-2.0 | 0.8B / 4B / 9B / 27B on Qwen3.5/3.8 | The 0.8B is "any Apple Silicon Mac, L4"; 4B wants "32 GB Mac, L40S, H100" (<https://github.com/jaredpalmer/kev>). Reasonable on a taOS **GPU box**, not on the Pi |
| **[Decider](https://huggingface.co/Mapika/decider-4b)** | Apache-2.0 | 0.8B / 2B / 4B / 35B-A3B | vLLM serving, CUDA path; bf16 8.4 GB for the 4B (<https://huggingface.co/Mapika/decider-4b>) |
| **[open-alternative-jev](https://github.com/ikermoel/open-alternative-jev)** (`so1`) | Apache-2.0 | wraps any open-weights LLM | Its headline measurement is Qwen3.6-27B 8-bit on an H200 MIG slice (582 ms/case); the HF demo uses Qwen3.5-4B. Reads option probabilities at each answer position, no generation. **Training-free and architecture-agnostic, which makes it the right thing to point at whatever RKLLM weights taOS already installs** |
| **[verdict](https://github.com/khimaros/verdict)** | **GPL-3.0-or-later** | wraps any GGUF via llama-server | Bundleable under AGPLv3 section 13, but process-isolate it. Strongest measured calibration of any option: `jevk5-4b` ECE **0.030** with 65% answered at 0.9+ and 96% of those right (<https://github.com/khimaros/verdict>). Needs llama.cpp, which taOS does not currently run |

### 2.4 Licence flags: research-only / NC / unclear

These are **not** bundleable as shipped. Cite them, do not depend on them.

| Model | Licence | Flag |
|---|---|---|
| **[Hopper](https://systemonemodels.tech/blog/open-source-jev-alternatives)** | "Research only" | **Not for commercial use.** taOS is AGPL, so out |
| **[System One scorer](https://systemonemodels.tech/blog/open-source-jev-alternatives)** (`pngwn/system-one-qwen3-5-4b-scorer`) | **CC-BY-NC-4.0** | **Non-commercial.** Out |
| **[Tev1-4B-experimental](https://laya-ai.com/system-one-models)** (Together AI) | weights licence **still being finalised**, code MIT | Unresolved. Out until published |
| `laya-vision-smolvlm-256m` | **CC-BY-NC-SA-4.0** | Non-commercial **and** ShareAlike. The only open *vision* decision model in this family, so the flag hurts twice (<https://decisioneval.dev/compare/>) |
| `modernbert-ja-310m-jev` | **CC-BY-SA-4.0** | ShareAlike is compatible with AGPL-3.0, so this one is usable; still verify the attribution chain |
| **[JevK5](https://laya-ai.com/system-one-models)** | Apache-2.0 | **Licence-clean but provenance-dirty**: most training labels came from an OpenAI model (<https://systemonemodels.tech/blog/open-source-jev-alternatives>). Read OpenAI's terms before shipping |
| **[Bespoke Nimble 9B](https://laya-ai.com/system-one-models)** | not stated in the repository | Unresolved |

### 2.5 Hosted competitors, for the record

Not bundleable either, but they set the bar and two are worth monitoring:
**Liquid AI `d1`** (same System One API, free tier, no weights,
<https://laya-ai.com/system-one-models>); **OpenAI Decisions API** (announced at
DevDay 29 Sep 2026, ~150 ms vs 1.6 s for GPT-6 Luna, limited preview, own
interface, <https://laya-ai.com/system-one-models>). **Cloudflare Workers AI** is the
interesting one because the *weights* are Apache-2.0: the constraint is hardware, not
licence (<https://developers.cloudflare.com/changelog/post/2026-10-01-clef-workers-ai/>).

### 2.6 The RK3588 verdict, stated honestly

The `Pi-NPU-16GB` profile is RK3588 with 16 GB unified memory and a 6 TOPS NPU
(`docs/catalog-platform-status.md`; `docs/getting-started.md`).

**CPU: yes, comfortably.** The Tier-1 encoders are 68M to 400M parameters. Measured
CPU latency for this family runs from **96 ms** on a 4-vCPU Xeon 8488C (Von, OpenVINO,
<https://github.com/wfzyx/von>) to **193 ms** on a 4-core EPYC 9R14 (Laya multilingual,
<https://laya-ai.com/guides/laya-cpu-performance>), with the 421M English Laya
checkpoints at ~580 ms (<https://laya-ai.com/guides/laya-cpu-performance>) and
Anarkali at ~110 ms on a laptop CPU (<https://github.com/ToufiqQureshi/anarkali>).
An RK3588's 4x A76 + 4x A55 will be slower than any of those, by a factor we have
**not measured**. The honest claim is: it fits in RAM many times over, and it is in
the same order of magnitude as the round trip to a hosted LLM today. **Measure it on
the box before promising a number.**

Two caveats from the measured CPU numbers:
- **Batching questions barely helps on CPU.** Ten questions in one call take roughly
  as long as ten separate calls, because there is no fixed overhead to amortise
  (<https://laya-ai.com/guides/laya-cpu-performance>). Pack questions only for the
  GPU path.
- **Thread settings are worth ~12x.** On a Ryzen 9 6900HX under WSL2, three
  questions took 9,396 ms p50 with torch's default threads and **783 ms** after
  `torch.set_num_threads(8)` and `torch.set_num_interop_threads(1)`
  (<https://laya-ai.com/guides/laya-cpu-performance>). Whatever we adopt must set
  thread counts, or it will look ten times worse than it is.

**NPU: possible but not the first move.** RKNN-Toolkit2 supports RK3588 and requires
conversion on an **x86 PC**, then `RKNN-Toolkit-Lite2` or the C runtime on the board
(<https://github.com/airockchip/rknn-toolkit2>). The RKNN model zoo's text-side
examples are OCR and `lite_transformer`, not decision heads
(<https://github.com/airockchip/rknn_model_zoo>), and
[`rk-transformers`](https://github.com/emapco/rk-transformers) does ship BERT /
MiniLM `w8a8` exports at `max_seq_length` 128 on RK3588 -- a shape a `noul` head would
fit. But that is a conversion project on its own, gated behind an x86 build host,
and ModernBERT (Von's, Laya's, Anarkali's ancestor) is not in the RKNN path at all.
**Defer NPU work until a CPU number exists.** taOS already runs `rkllama` on `:7833`
as an OpenAI-compatible backend (`tinyagentos/backend_adapters.py`), which makes a
training-free readout over whatever chat weights are already installed the cheaper
second move, not RKNN conversion.

**Int8 note:** ONNX Runtime int8 exports of ModernBERT-large exist upstream
(<https://huggingface.co/onnx-community/ModernBERT-large-ONNX>), but Anarkali's own
export notes **"every int8 variant failed and was dropped"**, so its fp32 ONNX is the
only one it publishes (<https://github.com/ToufiqQureshi/anarkali>). Do not assume an
int8 win.

---

## 3. Where it plugs into taOS

### 3.1 Today's loop

The OS-native agent's desktop channel is fixed and narrow: **`POST
/api/desktop/command`** with `{kind: "open-app" | "window", payload}`, plus
`POST /api/desktop/screenshot` and `POST /api/desktop/layout`
(`docs/desktop-control.md`, `.claude/skills/taos-agent/SKILL.md`). The agent tools
are thin wrappers over it: `open_app`, `arrange_windows`, `read_layout`
(`tinyagentos/tools/desktop_tools.py:27-80`). Alongside them the OS agent carries
`todo_list_lists`, `todo_add_item`, `todo_set_done`, `notes_*`, `add_task`,
`create_project`, `request_decision`, `notify_user`
(`tinyagentos/skills.py:268-742`).

The harness is opencode on desktop, PicoClaw on a handset, with the model coming from
the controller's LLM gateway through `taos-default`
(`tinyagentos/taos_agent_runtime.py`, `.claude/skills/taos-agent/SKILL.md`). So every
tool selection today costs **one full LLM turn**: read the message, choose a tool,
emit the JSON, then the tool runs. Screenshots go back in as image attachments where
supported.

### 3.2 Five plug-in points, ranked by value / risk

**(1) Pre-answer the Decisions flow. Best value, lowest risk.**
`request_decision` raises a question to the user; answering it can carry consent side
effects, and the code already separates those cases into
`_apply_execution_grant`, `_apply_app_grant`, `_apply_project_create_grant`,
`_apply_delegation_grant`, `_apply_device_pairing_grant`
(`tinyagentos/routes/decisions.py:708-1077`). A `choice`/`score` call over
`{question, type, options, context, priority}` (`tinyagentos/decisions/decision_store.py:29`)
can answer the **non-consent** decisions inline when confidence clears a threshold,
and only escalate below it. The grant-bearing ones stay hard-wired to the user on
day one. This is `AutoModeMiddleware` inverted: instead of blocking a risky call, we
stop raising a question the model is sure about.

**(2) App / window routing. Highest frequency.**
`open_app`'s schema already enumerates 17 app ids in `KNOWN_APPS`
(`tinyagentos/tools/desktop_tools.py:21-25`). "Open the projects app" currently costs
an LLM turn; a `choice` over those 17 ids costs one encoder pass. Same shape as
LangChain's `ModelRouterMiddleware`
(<https://www.langchain.com/blog/building-a-harness-with-jev>).

**(3) Risk gate before a tool executes.**
A `noul` over `{tool, args}` before `POST /api/desktop/command` or before
`add_task` / `request_decision`, blocking anything above a risk threshold. This is
LangChain's `AutoModeMiddleware` directly
(<https://www.langchain.com/blog/building-a-harness-with-jev>) and it also fits
`verdict`'s finding that on-device agents need a **separate** DONE gate from a
target gate: "is this the right target" and "is this safe" are different questions and
should not share a threshold (<https://github.com/khimaros/verdict>).

**(4) Backend / model routing.**
taOS already has a LiteLLM provider config with `ollama`, `rkllama` and
`hailo-ollama` adapters (`tinyagentos/backend_adapters.py:233`,
`tinyagentos/litellm_config.py:74-85`). A `choice` can pick which backend answers a
turn, which is the difference between an rkllama 1.5B on the NPU and a frontier API.

**(5) Todo / Notes triage. Smallest win.**
`todo_add_item` vs `notes_add_entry` is a 2-way `choice`; urgency for `due_at` is a
`score` over 2-10 levels (`tinyagentos/routes/todo.py:148`). Worth doing, worth
doing last.

### 3.3 The screenshot problem, stated plainly

**Jev is text-only** (<https://docs.typesafe.ai/models>). Every encoder in Tier 1 is
text-only too. The `POST /api/desktop/screenshot` response is a PNG. To use any of
this on window navigation we must first reduce the frame to text, and the only
in-repo source of that is `POST /api/desktop/layout`, which already returns screen
size plus every window's `appId`, bounds, and lifecycle state
(`docs/desktop-control.md`). **`getLayout` is the decision state; the PNG is for the
human.** This is the single most important finding for section 4: the decision model
does not need vision for window navigation, because taOS already has a structured
layout read.

Where vision *would* matter -- deciding what is on screen inside a window, clicking a
widget -- the only open Apache-2.0 multimodal decision model is Clef at 27B / >=85 GB
VRAM (<https://www.theregister.com/ai-and-ml/2026/10/01/cloudflare-tries-to-outplay-jev-with-open-weight-clef-models/5300649>).
That is a GPU-box project, not an RK3588 project.

### 3.4 What it saves, and what it does not

**Saves, measured on the reference numbers:**

- **Latency per routing decision.** 1-4 s of LLM turn becomes a sub-0.6 s CPU
  encoder pass (<https://huggingface.co/blog/manjunathshiva/opendecider-beats-laya-and-jev>,
  <https://github.com/wfzyx/von>). On a GPU box with Clef the same step is 39 ms
  (<https://blog.cloudflare.com/clef-decision-models/>).
- **LLM calls.** Each decision the classifier absorbs is one fewer turn in the loop.
  The classifier's own cost is 0 tokens if local.
- **Type errors and hallucinated tool calls.** The answer space is declared by us, so
  a bad `appId` or a malformed `options` list cannot be produced
  (<https://typesafe.ai/blog/introducing-system-one-models-and-jev>). For an agent
  driving a live desktop this is the reliability win, and TypeSafe calls it an
  absolute deal-breaker when a hallucinated tool call sits under a latency guarantee
  (same source).
- **Egress.** A local model keeps desktop state on the box, which is the taOS default
  position (<https://typesafe.ai/legal/terms> for the ZDR contrast).

**Does not save:**

- **Nothing, if we still call the LLM to write the sentence.** Jev does not generate
  text (<https://docs.typesafe.ai/concepts/system-one>), so the reply still needs a
  System-Two model. The win is a smaller loop, not a chatless one.
- **Hard decisions.** Jev trails frontier LLMs on general knowledge and multi-step
  reasoning: Clef scores 82.7 on MMLU-Pro against Jev's 91.7, 78.3 vs 48.0 on GPQA
  Diamond, 92.9 vs 73.7 on BBH (<https://tpsreport.news/news/cloudflare-clef-27b-decision-model>).
  Plan for a **cascade**, which is what everyone in this space converged on: cheap
  model first, escalate below threshold. OpenDecider's cascade hand-off saved little
  time on its own benchmark because the first stage was rarely confident enough
  (<https://huggingface.co/mvbalaji/od1-typed-decisions>); Von's did
  (<https://github.com/wfzyx/von>); OpenDecider's nano is 94-95% accurate on the
  confident half against Jev's 88% (<https://huggingface.co/blog/manjunathshiva/opendecider-beats-laya-and-jev>).
- **taOS per-step cost, which nobody has measured.** There is no baseline in this
  repo of "turns per `open_app`" or "tokens per desktop action". **The PoC must
  establish it**, otherwise the saving is unquantifiable.

---

## 4. Recommendation and the smallest proof of concept

### 4.1 Recommendation

**Ship the interface, not the vendor.** Adopt `POST /v1/systemone` as a private
taOS-side protocol for "the agent's cheap typed decisions", with a backend
interface so the model is swappable. Reasons: eleven open models already serve that
exact shape (<https://systemonemodels.tech/blog/open-source-jev-alternatives>), so
nothing is lost by starting local; and it is the only shape where a later swap to
Clef or Jev is a URL change
(<https://github.com/khimaros/verdict> shows the pattern, including the third-party
client conformance test that proves the wire shape).

**Model: [Anarkali](https://github.com/ToufiqQureshi/anarkali), 68M, Apache-2.0, as
the default local backend.**

- 273 MB **single ONNX file, no PyTorch to serve** -- it cannot destabilise the taOS
  dependency set.
- 68M params fits an RK3588's 16 GB with room to spare, and only the 273 MB file
  ships.
- Apache-2.0 code, MIT backbone, Apache-2.0 benchmark data, `NOTICE` included: clean
  to bundle under AGPL-3.0 with no exceptions to argue about.
- It is the only one of these with **coding-decision packs already measured**,
  including `coding_agent_step` at 70.0% and `coding_pr_triage` at 78.8% over 440
  held-out synthetic cases (<https://github.com/ToufiqQureshi/anarkali>) -- which is
  the shape our PoC actually needs.
- It already ships `noul`, `choice`, `score` **and an `abstain` flag**, which is the
  gating primitive we need (same source).

**Honest caveats, stated so nobody is surprised later:**

- **Very low maturity.** Two GitHub stars, two commits, one author, no issues.
  Its 74.0%-vs-Jev-72.7% figure is author-reported on a fine-tuned split, with Jev's
  number imported from the Laya project rather than measured by them; the dataset's
  own labelling teacher agrees with itself only 73.5% of the time
  (<https://github.com/ToufiqQureshi/anarkali>). Vendor the ONNX **with a pinned
  sha256**, and treat the checkpoint as replaceable.
- **English only, 512-token context**, and weaker the further your questions drift
  from its training workflows (same source). Switch to
  [Laya multilingual](https://laya-ai.com/system-one-models) (322M, Apache-2.0,
  193 ms) if any state is non-English; states here can be user messages.

**Fallback, if we want maturity over size:**
[Von](https://github.com/wfzyx/von) (Apache-2.0, 395M, 816 stars, 208 commits,
OpenVINO CPU path measured at 96 ms p50 on 4 vCPU, and a `von calibrate` command to
refit the confidence map on taOS's own labels).

**Not now:** Clef, Kev-4B+, Decider, Tev1, Jeeves. All either >= 41 GB VRAM or
GPU-only (<https://www.theregister.com/ai-and-ml/2026/10/01/cloudflare-tries-to-outplay-jev-with-open-weight-clef-models/5300649>,
<https://github.com/jaredpalmer/kev>). Revisit when a taOS **GPU box** profile
exists. Revisit the **NPU** only after a CPU baseline exists, and only via RKNN
conversion on an x86 host (<https://github.com/airockchip/rknn-toolkit2>).

### 4.2 The smallest proof of concept

**Scope: one entry point, one file of diff, one number.**

**Surface: `request_decision`, non-consent decisions only.**
One decision model call in front of `tinyagentos/routes/decisions.py:318`
(`create_decision`) and its skill wrapper
`tinyagentos/routes/skill_exec.py:468`. Nothing else changes.

**Design:**

1. Build the `state` from the decision's own fields: `{question, type, options,
   context, priority}` (`tinyagentos/decisions/decision_store.py:29`).
2. Ask one `choice` over the declared `options`, with a `criteria` rubric per option,
   plus one `noul`: "is this decision asking the user for permission to grant an
   app / execution / device-pairing / delegation / project-create grant?" If yes, or
   if the choice confidence is **< 0.7**, fall through and create the decision
   exactly as today. Otherwise answer it via the existing
   `POST /api/decisions/{id}/answer` path.
3. Ship Anarkali vendored under Apache-2.0 with its `NOTICE`, pinned by sha256,
   served by ONNX Runtime CPU with explicit thread caps (the 12x lesson from
   <https://laya-ai.com/guides/laya-cpu-performance>).
4. The gate is **off by default** and behind a setting, so the control arm is the
   shipping behaviour.

**Why this surface first:** it is the smallest closed loop in the whole card. The
consent-bearing cases are *already* enumerable in code, so the blast radius of a
wrong answer is bounded by construction. It produces no desktop side effects, so
nothing here can move a window. And the outcome is a count, not a judgement.

**What it must measure (the proof):**

| Metric | Where from |
|---|---|
| Decisions raised to the user that were auto-answered, and how many were wrong when the user later overrode them | `decisions` store: created vs `answer`-ed, cross-read against the later answer |
| Accuracy at the 0.7 gate, on taOS's own decisions | the auto-answered set vs the eventual human answer |
| Latency of the classifier on a real Pi-NPU-16GB, p50 and p95 | new timer; this is the number we do not have |
| Tokens and turns saved | LiteLLM gateway counters before/after (`tinyagentos/llm_gateway.py`) |

**Falsification, agreed in advance.** If taOS-measured accuracy at the 0.7 gate is
below 90%, or if the Pi-NPU-16GB p95 exceeds the p50 of one current LLM turn, the
PoC fails and we keep today's loop. Both are cheap to measure and neither is worth
arguing about.

**Second surface, only after the first lands:** `open_app` routing
(`tinyagentos/tools/desktop_tools.py:21-25`), where the answer space is the 17
`KNOWN_APPS` and the payoff is the highest in section 3.2.

**Explicitly out of scope for the PoC:** anything that reaches
`POST /api/desktop/command` for a window mutation, and anything that reads raw
screenshot pixels (no text-only model can).

---

## Sources

**TypeSafe (primary)**
- <https://typesafe.ai/blog/introducing-system-one-models-and-jev>
- <https://docs.typesafe.ai/concepts/system-one>
- <https://docs.typesafe.ai/models>
- <https://docs.typesafe.ai/api>
- <https://typesafe.ai/legal/terms>
- <https://github.com/typesafe-ai/system-one-adapter-python>
- <https://en.wikipedia.org/wiki/Jev_(AI_model)>

**Harnesses using Jev**
- <https://www.langchain.com/blog/building-a-harness-with-jev>

**Open alternatives, bundleable**
- <https://github.com/ToufiqQureshi/anarkali>
- <https://github.com/wfzyx/von>
- <https://github.com/NandhaKishorM/laya>
- <https://laya-ai.com/guides/laya-cpu-performance>
- <https://laya-ai.com/system-one-models>
- <https://laya-ai.com/jev-alternatives>
- <https://pypi.org/project/opendecider/>
- <https://huggingface.co/blog/manjunathshiva/opendecider-beats-laya-and-jev>
- <https://huggingface.co/mvbalaji/od1-typed-decisions>
- <https://pypi.org/project/open-decision-ai/>
- <https://github.com/jaredpalmer/kev>
- <https://huggingface.co/Mapika/decider-4b>
- <https://github.com/ikermoel/open-alternative-jev>
- <https://github.com/khimaros/verdict>
- <https://blog.cloudflare.com/clef-decision-models/>
- <https://developers.cloudflare.com/changelog/post/2026-10-01-clef-workers-ai/>
- <https://developers.cloudflare.com/workers-ai/models/clef/>
- <https://www.theregister.com/ai-and-ml/2026/10/01/cloudflare-tries-to-outplay-jev-with-open-weight-clef-models/5300649>
- <https://tpsreport.news/news/cloudflare-clef-27b-decision-model>

**Registries and licence flags**
- <https://systemonemodels.tech/blog/open-source-jev-alternatives>
- <https://decisioneval.dev/compare/>
- <https://www.fsf.org/bulletin/2021/fall/the-fundamentals-of-the-agplv3>
- <https://spdx.org/licenses/AGPL-3.0-or-later.html>

**RK3588 / ONNX**
- <https://github.com/airockchip/rknn-toolkit2>
- <https://github.com/airockchip/rknn_model_zoo>
- <https://github.com/emapco/rk-transformers>
- <https://huggingface.co/onnx-community/ModernBERT-large-ONNX>

**In-repo**
- `docs/desktop-control.md`, `docs/getting-started.md`, `docs/catalog-platform-status.md`,
  `.claude/skills/taos-agent/SKILL.md`
- `tinyagentos/tools/desktop_tools.py`, `tinyagentos/skills.py`,
  `tinyagentos/routes/decisions.py`, `tinyagentos/decisions/decision_store.py`,
  `tinyagentos/routes/todo.py`, `tinyagentos/routes/skill_exec.py`,
  `tinyagentos/backend_adapters.py`, `tinyagentos/litellm_config.py`,
  `tinyagentos/taos_agent_runtime.py`