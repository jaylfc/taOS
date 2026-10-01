#!/usr/bin/env python3
"""Gateway check: the in-process LLM gateway, as one real agent.

NOT a test. Run it on a live controller. It sends prompts, as one real agent
(its own key, read from config.yaml, never printed), to the gateway's agent
listener ``http://127.0.0.1:<server.llm_gateway_port>/v1`` (default 7838).

The side-by-side LiteLLM comparison it used to run is gone with LiteLLM
(removal stage 2b-2a); what was ``--gateway-only`` is now the only mode
(the flag is still accepted, as a no-op).

Per prompt, non-streamed and streamed, it asserts:
  status        200
  shape         a chat completion (or chunks + [DONE]); wherever the answer
                carries ``reasoning`` it also carries ``reasoning_content``
  usage         the gateway reported token usage
  recorded      a new ``llm_call`` row in the agent's trace
                (<data-dir>/trace/<agent>/*.db; spend alone is 0 on free models)
and then one embeddings call (``--embedding-model``, default
``taos-embedding-default``): 200, a non-empty vector, usage, a new trace row.
``--skip-embeddings`` leaves that out (an agent whose key does not allow the
embedding model is told so plainly and fails).

  sudo -u taos /opt/taos/venv/bin/python scripts/llm_gateway_parity.py \\
      --data-dir /opt/taos/data --agent naira
"""
from __future__ import annotations

import argparse
import json
import sqlite3
import sys
import time
from pathlib import Path

# A missing dependency is reported from main(), not at import: importing this
# module (the deleted-symbols gate loads it from the merge tree with a bare
# CI python) must not raise SystemExit.
try:
    import httpx
    import yaml
except ImportError as _exc:  # pragma: no cover - run from the taOS venv
    httpx = yaml = None
    _IMPORT_ERROR: ImportError | None = _exc
else:
    _IMPORT_ERROR = None

DEFAULT_PROMPTS = [
    "Reply with the single word: pong",
    "List three primary colours as a JSON array of strings, nothing else.",
    "In one sentence, what is an incus proxy device?",
]
TIMEOUT = httpx.Timeout(connect=10.0, read=180.0, write=30.0, pool=10.0) if httpx else None


def _load_agent(data_dir: Path, name: str) -> tuple[dict, dict]:
    cfg = yaml.safe_load((data_dir / "config.yaml").read_text()) or {}
    for agent in cfg.get("agents") or []:
        if isinstance(agent, dict) and agent.get("name") == name:
            return cfg, agent
    raise SystemExit(f"agent {name!r} not found in {data_dir / 'config.yaml'}")


def _trace_calls(data_dir: Path, agent: str) -> int | None:
    """``llm_call`` rows across the agent's hourly trace buckets.

    A legacy bucket with no ``trace_events`` table holds no llm_call rows and
    counts 0. Any other sqlite error (locked, corrupt) makes the whole count
    None: unknown is not zero.
    """
    trace_dir = data_dir / "trace" / agent
    if not trace_dir.is_dir():
        return 0
    total = 0
    for db in sorted(trace_dir.glob("*.db")):
        try:
            with sqlite3.connect(f"file:{db}?mode=ro", uri=True, timeout=5) as conn:
                has_table = conn.execute(
                    "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'trace_events'"
                ).fetchone()
                if not has_table:
                    continue
                row = conn.execute(
                    "SELECT COUNT(*) FROM trace_events WHERE kind = 'llm_call'"
                ).fetchone()
        except sqlite3.Error:
            return None
        total += int(row[0]) if row else 0
    return total


def _usage(u) -> dict | None:
    if not isinstance(u, dict):
        return None
    return {k: u.get(k) for k in ("prompt_tokens", "completion_tokens", "total_tokens")}


def _shape_plain(body) -> dict:
    if not isinstance(body, dict):
        return {"type": type(body).__name__}
    shape = {"keys": sorted(k for k in body if k in {"id", "object", "created", "model", "choices", "usage", "error"})}
    if "error" in body:
        err = body["error"]
        shape["error_keys"] = sorted(err) if isinstance(err, dict) else type(err).__name__
        return shape
    shape["object"] = body.get("object")
    choices = body.get("choices") or []
    shape["n_choices"] = len(choices)
    if choices and isinstance(choices[0], dict):
        msg = choices[0].get("message") or {}
        shape["message_keys"] = sorted(k for k in msg if msg.get(k) is not None)
        shape["finish_reason"] = choices[0].get("finish_reason") is not None
        shape["content_type"] = type(msg.get("content")).__name__
    return shape


def _call(url: str, key: str, model: str, prompt: str, stream: bool) -> dict:
    body = {"model": model, "messages": [{"role": "user", "content": prompt}], "max_tokens": 64}
    if stream:
        body["stream"] = True
        body["stream_options"] = {"include_usage": True}
    headers = {"Authorization": f"Bearer {key}"}
    t0 = time.monotonic()
    try:
        with httpx.Client(timeout=TIMEOUT) as client:
            if not stream:
                resp = client.post(f"{url}/chat/completions", json=body, headers=headers)
                try:
                    data = resp.json()
                except ValueError:
                    data = resp.text[:200]
                return {"status": resp.status_code, "shape": _shape_plain(data),
                        "usage": _usage(data.get("usage")) if isinstance(data, dict) else None,
                        "text": _text_plain(data), "secs": round(time.monotonic() - t0, 2)}
            with client.stream("POST", f"{url}/chat/completions", json=body, headers=headers) as resp:
                chunks, done, usage, text, objects = 0, False, None, [], set()
                reasoning_keys: set[str] = set()
                if resp.status_code != 200:
                    resp.read()
                    try:
                        data = resp.json()
                    except ValueError:
                        data = resp.text[:200]
                    return {"status": resp.status_code, "shape": _shape_plain(data), "usage": None,
                            "text": "", "secs": round(time.monotonic() - t0, 2)}
                for line in resp.iter_lines():
                    if not line.startswith("data:"):
                        continue
                    payload = line[5:].strip()
                    if payload == "[DONE]":
                        done = True
                        continue
                    try:
                        chunk = json.loads(payload)
                    except ValueError:
                        continue
                    chunks += 1
                    objects.add(chunk.get("object"))
                    if chunk.get("usage"):
                        usage = _usage(chunk["usage"])
                    for ch in chunk.get("choices") or []:
                        delta = ch.get("delta") or {}
                        piece = delta.get("content")
                        if isinstance(piece, str):
                            text.append(piece)
                        reasoning_keys.update(
                            k for k in ("reasoning", "reasoning_content") if delta.get(k)
                        )
                return {"status": resp.status_code,
                        "shape": {"objects": sorted(o for o in objects if o), "done": done,
                                  "has_chunks": chunks > 0, "usage_chunk": usage is not None},
                        "reasoning_keys": sorted(reasoning_keys),
                        "usage": usage, "text": "".join(text), "secs": round(time.monotonic() - t0, 2)}
    except httpx.HTTPError as exc:
        return {"status": None, "shape": {"transport_error": type(exc).__name__}, "usage": None,
                "text": "", "secs": round(time.monotonic() - t0, 2)}


def _embed(url: str, key: str, model: str) -> dict:
    t0 = time.monotonic()
    try:
        with httpx.Client(timeout=TIMEOUT) as client:
            resp = client.post(f"{url}/embeddings", json={"model": model, "input": "taOS parity check"},
                               headers={"Authorization": f"Bearer {key}"})
    except httpx.HTTPError as exc:
        return {"status": None, "error": type(exc).__name__, "secs": round(time.monotonic() - t0, 2)}
    try:
        body = resp.json()
    except ValueError:
        body = None
    out = {"status": resp.status_code, "secs": round(time.monotonic() - t0, 2)}
    if isinstance(body, dict) and isinstance(body.get("error"), dict):
        out["error"] = body["error"].get("code") or body["error"].get("message")
    data = body.get("data") if isinstance(body, dict) else None
    vec = data[0].get("embedding") if isinstance(data, list) and data and isinstance(data[0], dict) else None
    out["dims"] = len(vec) if isinstance(vec, list) else 0
    out["usage"] = _usage(body.get("usage")) if isinstance(body, dict) else None
    return out


def _gateway_only(args, key: str, model: str, url: str, prompts: list[str]) -> int:
    rows, ok = [], True
    for prompt in prompts:
        for stream in (False, True):
            before = _trace_calls(args.data_dir, args.agent)
            res = _call(url, key, model, prompt, stream)
            time.sleep(0.5)  # the stream's trace row is written as it closes
            after = _trace_calls(args.data_dir, args.agent)
            res["recorded"] = (after - before) if before is not None and after is not None else None
            if stream:
                rkeys = set(res.get("reasoning_keys") or [])
                shape_ok = bool(res["shape"].get("done") and res["shape"].get("has_chunks"))
            else:
                rkeys = set((res["shape"].get("message_keys") or []))
                shape_ok = res["shape"].get("object") == "chat.completion" and res["shape"].get("n_choices", 0) > 0
            reasoning_ok = "reasoning" not in rkeys or "reasoning_content" in rkeys
            row = {
                "prompt": prompt[:40], "stream": stream, "status": res["status"],
                "shape_ok": shape_ok, "reasoning_ok": reasoning_ok,
                "reasoning": sorted(rkeys & {"reasoning", "reasoning_content"}),
                "usage": res["usage"], "recorded": res["recorded"],
            }
            row["ok"] = (res["status"] == 200 and shape_ok and reasoning_ok
                         and res["usage"] is not None and (res["recorded"] or 0) >= 1)
            if not args.json:
                res.pop("text", None)
            row["raw"] = res
            ok = ok and row["ok"]
            rows.append(row)

    embed = None
    if not args.skip_embeddings:
        before = _trace_calls(args.data_dir, args.agent)
        embed = _embed(url, key, args.embedding_model)
        after = _trace_calls(args.data_dir, args.agent)
        embed["recorded"] = (after - before) if before is not None and after is not None else None
        embed["ok"] = (embed["status"] == 200 and embed["dims"] > 0 and embed["usage"] is not None
                       and (embed["recorded"] or 0) >= 1)
        ok = ok and embed["ok"]

    print(f"GATEWAY-ONLY model={model} agent={args.agent} gateway={url}")
    print(f"{'prompt':42} {'strm':5} {'status':6} {'shape':5} {'reasoning keys':32} {'tokens':>10} {'trace':>5} ok")
    for r in rows:
        u = r["usage"] or {}
        print(
            f"{r['prompt']:42} {str(r['stream']):5} {str(r['status']):6} "
            f"{'ok' if r['shape_ok'] else 'BAD':5} {','.join(r['reasoning']) or '-':32} "
            f"{str(u.get('prompt_tokens')) + '+' + str(u.get('completion_tokens')):>10} "
            f"{str(r['recorded']):>5} {'yes' if r['ok'] else 'NO'}"
        )
        if not r["reasoning_ok"]:
            print("    reasoning without reasoning_content")
    if embed is not None:
        u = embed["usage"] or {}
        print(f"embeddings model={args.embedding_model} status={embed['status']} dims={embed['dims']} "
              f"prompt_tokens={u.get('prompt_tokens')} trace={embed['recorded']} "
              f"{'yes' if embed['ok'] else 'NO'}")
        if embed["status"] == 403:
            print(f"    this agent's key does not allow {args.embedding_model!r}: add it to the "
                  "agent's permitted models, or pass --skip-embeddings")
        elif embed.get("error"):
            print(f"    error: {embed['error']}")
    if args.json:
        print(json.dumps({"chat": rows, "embeddings": embed}, indent=2))
    print("GATEWAY OK" if ok else "GATEWAY FAILED")
    return 0 if ok else 1


def _text_plain(data) -> str:
    try:
        return data["choices"][0]["message"]["content"] or ""
    except (KeyError, IndexError, TypeError):
        return ""


def main() -> int:
    if _IMPORT_ERROR is not None:
        print(f"needs the taOS venv (httpx, pyyaml): {_IMPORT_ERROR}", file=sys.stderr)
        return 2
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--data-dir", default="/opt/taos/data", type=Path)
    ap.add_argument("--agent", required=True, help="an agent whose key is in the local key store")
    ap.add_argument("--model", help="default: the agent's model from config.yaml")
    ap.add_argument("--gateway-url", help="default: http://127.0.0.1:<server.llm_gateway_port or 7838>/v1")
    ap.add_argument("--prompt", action="append", help="repeatable; default: three built-in prompts")
    ap.add_argument("--json", action="store_true", help="print the full result as JSON")
    ap.add_argument("--gateway-only", action="store_true",
                    help="accepted for old command lines; the gateway check is the only mode")
    ap.add_argument("--embedding-model", default="taos-embedding-default",
                    help="model for the embeddings check")
    ap.add_argument("--skip-embeddings", action="store_true",
                    help="leave out the embeddings check")
    args = ap.parse_args()

    cfg, agent = _load_agent(args.data_dir, args.agent)
    key = agent.get("llm_key")
    if not key:
        print(f"agent {args.agent!r} has no llm_key in config.yaml", file=sys.stderr)
        return 2
    model = args.model or agent.get("model")
    if not model:
        print("no --model given and the agent has none", file=sys.stderr)
        return 2
    server = cfg.get("server") or {}
    url = args.gateway_url or f"http://127.0.0.1:{server.get('llm_gateway_port') or 7838}/v1"
    return _gateway_only(args, key, model, url, args.prompt or DEFAULT_PROMPTS)


if __name__ == "__main__":
    sys.exit(main())
