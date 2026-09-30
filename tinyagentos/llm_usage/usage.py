"""Token usage, normalised across the wire formats taOS speaks.

LiteLLM used to hand us `response.usage` already normalised, including for streamed replies, where
it assembles the counts itself. Without it, every format reports usage differently, and each
difference silently corrupts spend and budgets if it is not handled:

* **Cache tokens are counted two different ways.** OpenAI's `prompt_tokens` INCLUDES cached tokens
  (`prompt_tokens_details.cached_tokens` is a part of it). Anthropic's `input_tokens` EXCLUDES them:
  `cache_read_input_tokens` and `cache_creation_input_tokens` are reported alongside. We normalise to
  ONE convention, the one pydantic/genai-prices (MIT) uses: `input_tokens` is the TOTAL, and the cache
  counts are partitions of it. Mixing the two conventions double- or under-counts every cached call.
* **Streams carry no usage unless asked.** OpenAI-shaped backends only send it in a final chunk when the
  request has `stream_options: {"include_usage": true}`; see `ensure_stream_usage`.
* **Missing is not zero.** When a backend reports nothing, the result is `Usage.unknown()`: tokens 0
  but `source="unknown"`, so nothing downstream can mistake it for a measured free call.
"""
from __future__ import annotations

import copy
from dataclasses import dataclass, field
from typing import Any

REPORTED = "reported"
UNKNOWN = "unknown"


def _int(value: Any) -> int:
    try:
        return max(0, int(value or 0))
    except (TypeError, ValueError):
        return 0


@dataclass(frozen=True)
class Usage:
    """One call's token usage. `input_tokens` INCLUDES the cache partitions."""

    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_tokens: int = 0
    cache_write_tokens: int = 0
    reasoning_tokens: int = 0
    source: str = REPORTED

    @classmethod
    def unknown(cls) -> "Usage":
        return cls(source=UNKNOWN)

    @property
    def known(self) -> bool:
        return self.source != UNKNOWN

    @property
    def uncached_input_tokens(self) -> int:
        return max(0, self.input_tokens - self.cache_read_tokens - self.cache_write_tokens)


# ---- non-streamed responses --------------------------------------------------------------------

def from_openai(body: Any) -> Usage:
    """OpenAI chat/completions (and every OpenAI-compatible backend: llama.cpp, vLLM, OpenRouter, ...)."""
    u = body.get("usage") if isinstance(body, dict) else None
    if not isinstance(u, dict) or ("prompt_tokens" not in u and "completion_tokens" not in u):
        return Usage.unknown()
    pd = u.get("prompt_tokens_details") or {}
    cd = u.get("completion_tokens_details") or {}
    cache_read = _int(pd.get("cached_tokens"))
    if not cache_read:
        # DeepSeek's OpenAI-compatible API reports cache hits under its own key.
        cache_read = _int(u.get("prompt_cache_hit_tokens"))
    return Usage(
        input_tokens=_int(u.get("prompt_tokens")),
        output_tokens=_int(u.get("completion_tokens")),
        cache_read_tokens=cache_read,
        cache_write_tokens=_int(pd.get("cache_creation_tokens")),
        reasoning_tokens=_int(cd.get("reasoning_tokens")),
    )


def from_anthropic(body: Any) -> Usage:
    """Anthropic Messages API. `input_tokens` there EXCLUDES cache tokens; we fold them into the total."""
    u = body.get("usage") if isinstance(body, dict) else None
    if not isinstance(u, dict) or ("input_tokens" not in u and "output_tokens" not in u):
        return Usage.unknown()
    read = _int(u.get("cache_read_input_tokens"))
    write = _int(u.get("cache_creation_input_tokens"))
    return Usage(
        input_tokens=_int(u.get("input_tokens")) + read + write,
        output_tokens=_int(u.get("output_tokens")),
        cache_read_tokens=read,
        cache_write_tokens=write,
    )


def from_ollama(body: Any) -> Usage:
    """Ollama-native /api/chat. The counts only appear on the final (`done`) message."""
    if not isinstance(body, dict) or ("prompt_eval_count" not in body and "eval_count" not in body):
        return Usage.unknown()
    return Usage(input_tokens=_int(body.get("prompt_eval_count")), output_tokens=_int(body.get("eval_count")))


# ---- streams -----------------------------------------------------------------------------------

def ensure_stream_usage(request_body: dict) -> dict:
    """Return a copy of an OpenAI-shaped request that will report usage on a stream.

    Without this, an OpenAI-shaped stream reports NO usage, and every streamed call would be recorded
    as 0 tokens / $0 - the most common call shape, with per-agent budgets silently off.
    """
    body = copy.deepcopy(request_body)
    if body.get("stream"):
        opts = body.get("stream_options")
        opts = dict(opts) if isinstance(opts, dict) else {}
        opts["include_usage"] = True
        body["stream_options"] = opts
    return body


@dataclass
class OpenAIStreamUsage:
    """Feed every parsed SSE chunk; the usage chunk (sent last, with empty `choices`) wins."""

    _usage: Usage = field(default_factory=Usage.unknown)

    def feed(self, chunk: Any) -> None:
        if isinstance(chunk, dict) and isinstance(chunk.get("usage"), dict):
            got = from_openai(chunk)
            if got.known:
                self._usage = got

    def result(self) -> Usage:
        return self._usage


@dataclass
class AnthropicStreamUsage:
    """Feed every parsed SSE event. `message_start` carries input + cache counts (and an initial
    output count); each `message_delta` carries the CUMULATIVE output count, and newer API versions
    may repeat input/cache counts there too."""

    _seen: bool = False
    _input: int = 0
    _read: int = 0
    _write: int = 0
    _output: int = 0

    def _take(self, u: dict) -> None:
        if "input_tokens" in u:
            self._input = _int(u["input_tokens"])
        if "cache_read_input_tokens" in u:
            self._read = _int(u["cache_read_input_tokens"])
        if "cache_creation_input_tokens" in u:
            self._write = _int(u["cache_creation_input_tokens"])
        if "output_tokens" in u:
            self._output = _int(u["output_tokens"])
        self._seen = True

    def feed(self, event: Any) -> None:
        if not isinstance(event, dict):
            return
        kind = event.get("type")
        if kind == "message_start":
            u = (event.get("message") or {}).get("usage")
            if isinstance(u, dict):
                self._take(u)
        elif kind == "message_delta" and isinstance(event.get("usage"), dict):
            self._take(event["usage"])

    def result(self) -> Usage:
        if not self._seen:
            return Usage.unknown()
        return Usage(input_tokens=self._input + self._read + self._write, output_tokens=self._output,
                     cache_read_tokens=self._read, cache_write_tokens=self._write)


@dataclass
class OllamaStreamUsage:
    """Feed every NDJSON line; the `done` line carries the counts."""

    _usage: Usage = field(default_factory=Usage.unknown)

    def feed(self, chunk: Any) -> None:
        if isinstance(chunk, dict) and chunk.get("done"):
            got = from_ollama(chunk)
            if got.known:
                self._usage = got

    def result(self) -> Usage:
        return self._usage
