# Lightweight Agent Framework Research

**Date:** 2026-04-05
**Purpose:** Identify agent frameworks TinyAgentOS should support beyond OpenClaw/nanoclaw/picoclaw, Hermes, Agent Zero

## Top Picks for TinyAgentOS Integration

### Tier 1 — Best fit for SBC/low-power

| Framework | Stars | Language | Why interesting | RAM overhead |
|-----------|-------|----------|----------------|-------------|
| **SmolAgents** (HuggingFace) | 26k+ | Python | ~1000 lines, code-agents use 30% fewer LLM calls than JSON tool calling. Works with any OpenAI-compatible API. | ~200MB |
| **PocketFlow** | Growing | Python/TS/Go/Rust/Java | 100 lines, zero deps. Graph-based: nodes + flows + shared store. Multi-agent, RAG, MCP support. | ~50MB |
| **TinyAgent** (UC Berkeley) | Research | Python | Proves 1-3B models can do reliable tool calling at the edge with LLMCompiler. EMNLP 2024 paper. | ~200MB |

### Tier 2 — Worth evaluating

| Framework | Stars | Why | Concern |
|-----------|-------|-----|---------|
| **Atomic Agents** | ~2k | Composable Pydantic-based building blocks, anti-bloat philosophy | Smaller community |
| **Langroid** | ~4k | Multi-agent message-passing, supports local LLMs (Mistral-7b etc), vector store built-in | Heavier than SmolAgents |
| **txtai** | ~9k | All-in-one embeddings + RAG + agents + SQLite vectors | Overlaps with QMD |
| **Outlines** (dottxt) | ~10k | Structured generation — constrains small models to valid JSON/schemas. Not an agent framework but makes tool calling reliable. | Needs agent logic on top |
| **DSPy** (Stanford) | ~20k | Auto-optimizes prompts for weak models. Could make 3B models much more reliable. | Steep learning curve |

### Tier 3 — Interesting but heavier

| Framework | Why | Concern |
|-----------|-----|---------|
| **CUGA** (IBM) | Enterprise-grade, #1 on AppWorld benchmark, MCP + OpenAPI integration, modular multi-agent | Requires Python 3.12+, heavy deps, enterprise-focused |
| **Llama-Stack** (Meta) | Pluggable provider architecture, built-in RAG/memory | Heavy dependency tree |
| **Swarm** (OpenAI) | Lightweight multi-agent with handoffs | Experimental, OpenAI-centric |
| **LiteLLM** | Universal LLM proxy, retry/fallback logic | Middleware not framework |

## Strategic Recommendations

**For TinyAgentOS MVP compatibility:**
1. **SmolAgents** — first-class support. Most popular lightweight framework, code-agents are efficient.
2. **PocketFlow** — second priority. 100 lines, zero deps, available in multiple languages.
3. **Any OpenAI-compatible agent** — since TinyAgentOS exposes memory via API, any framework that can make HTTP calls can integrate.

**For the local assistant LLM (Phase 2):**
- Use **Outlines** for structured generation to make small models (Qwen3-1.7B/4B) reliable at tool calling
- The **TinyAgent** research validates that 1-3B models CAN do tool calling with proper training
- Consider **DSPy** for auto-optimizing prompts for the specific small model being used

**Integration pattern:**
TinyAgentOS doesn't need to embed these frameworks — it exposes HTTP APIs that any agent framework can consume:
- `/api/memory/search` for RAG retrieval
- `/api/memory/browse` for memory exploration
- QMD serve `/embed`, `/rerank`, `/expand` for inference
- The agent framework runs alongside (in same LXC or on same host) and calls these APIs

## Deep Dive: Outlines (Structured Generation)

**What it does:** Forces LLM output to conform to JSON schemas, regex patterns, or context-free grammars during decoding. Works by adjusting logit probabilities at each token step — the model physically cannot produce invalid output.

**Why it matters for TinyAgentOS:** Small models (1-4B) are unreliable at tool calling because they produce malformed JSON. Outlines eliminates this problem entirely. A Qwen3-1.7B with Outlines can reliably produce valid function call JSON every time.

**Compatibility:**
- Works with: transformers, llama.cpp (via Python bindings), vLLM, exllamav2
- Does NOT work directly with: ollama, rkllama (they manage their own inference loop)
- llama.cpp has its own built-in GBNF grammar support that achieves the same thing
- vLLM has native structured output support via XGrammar

**Integration path for TinyAgentOS:**
- For the Phase 2 local assistant LLM: if running via llama.cpp or vLLM, use their native structured output
- For rkllama: investigate if RKLLM runtime supports logit manipulation (unlikely — may need post-processing validation instead)
- Alternative: fine-tune the small model specifically for tool calling (the TinyAgent/Berkeley approach) so it naturally produces valid output without constrained decoding

**Key insight:** Constrained decoding (Outlines) and fine-tuning (TinyAgent) are complementary approaches. Use both: fine-tune for reliability, constrain for guarantee.

## Deep Dive: DSPy (Prompt Optimization)

**What it does:** Replaces hand-written prompts with programmatic "signatures" that DSPy auto-optimizes. Given input/output examples, it tunes the system prompt so small models produce better outputs. No weight changes — pure prompt engineering, automated.

**Why it matters for TinyAgentOS:** Small models (3-8B) need carefully crafted prompts to be useful. DSPy automates this. You define what you want (e.g., "given a user question and memory results, produce an answer") and DSPy figures out the optimal prompt.

**Setup with Ollama + Qwen3:**
```python
import dspy
lm = dspy.LM("ollama_chat/qwen3:8b", api_base="http://localhost:11434")
dspy.configure(lm=lm)
```

**Key features:**
- Works with any OpenAI-compatible API (Ollama, llama.cpp server, vLLM)
- Prompt optimization runs on CPU (no GPU needed), takes ~2 hours for a medium dataset
- Supports RAG patterns natively — can optimize retrieval + generation together
- Modules: ChainOfThought, ReAct (agent-style), ProgramOfThought

**Integration path for TinyAgentOS:**
- Use DSPy to optimize prompts for the Phase 2 local assistant LLM
- Pre-optimize against the specific Qwen3 model variant running on the target hardware
- Store optimized prompts in TinyAgentOS config — deploy once, reuse across agents
- Could optimize the query expansion prompt in QMD (currently hand-crafted)

**Concern:** DSPy's learning curve is steep. The "programming not prompting" paradigm takes time to internalize. Worth the investment for production but not needed for MVP.

## Sources
- https://github.com/huggingface/smolagents
- https://github.com/the-pocket/PocketFlow
- https://github.com/SqueezeAILab/TinyAgent
- https://github.com/BrainBlend-AI/atomic-agents
- https://github.com/langroid/langroid
- https://github.com/neuml/txtai
- https://github.com/dottxt-ai/outlines
- https://github.com/stanfordnlp/dspy
- https://github.com/cuga-project/cuga-agent
- https://github.com/meta-llama/llama-stack
- https://aimultiple.com/agentic-frameworks
- https://www.firecrawl.dev/blog/best-open-source-agent-frameworks
- https://www.morphllm.com/ai-agent-framework
