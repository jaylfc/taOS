#!/usr/bin/env python3
"""Side-by-side parity check: LiteLLM proxy vs the in-process LLM gateway.

NOT a test. Run it on a live controller while LiteLLM still runs beside the
gateway (cutover stage 1). It sends the same prompts, as one real agent (its
own key, read from config.yaml, never printed), to both:

  LiteLLM   http://127.0.0.1:<server.litellm_port>/v1         (default 7834)
  gateway   http://127.0.0.1:<server.llm_gateway_port>/v1     (agent listener, default 7838)

and compares, per prompt, non-streamed and streamed:
  status        HTTP status code
  shape         the OpenAI response skeleton (keys, message keys, finish_reason,
                content type; for streams: chunk object, [DONE], usage chunk)
  usage         prompt/completion/total tokens as the client saw them
  recorded      the agent's spend delta in <data-dir>/.agent_budgets.db
                (both proxies record into the same store)

Exit 0 when status and shape match everywhere and the gateway reported usage
wherever LiteLLM did; 1 otherwise; 2 when it could not run at all. Token
counts and cost are REPORTED, not gated: two cost tables legitimately differ.

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

try:
    import httpx
    import yaml
except ImportError as exc:  # pragma: no cover - run from the taOS venv
    print(f"needs the taOS venv (httpx, pyyaml): {exc}", file=sys.stderr)
    sys.exit(2)

DEFAULT_PROMPTS = [
    "Reply with the single word: pong",
    "List three primary colours as a JSON array of strings, nothing else.",
    "In one sentence, what is an incus proxy device?",
]
TIMEOUT = httpx.Timeout(connect=10.0, read=180.0, write=30.0, pool=10.0)


def _load_agent(data_dir: Path, name: str) -> tuple[dict, dict]:
    cfg = yaml.safe_load((data_dir / "config.yaml").read_text()) or {}
    for agent in cfg.get("agents") or []:
        if isinstance(agent, dict) and agent.get("name") == name:
            return cfg, agent
    raise SystemExit(f"agent {name!r} not found in {data_dir / 'config.yaml'}")


def _spend(data_dir: Path, agent: str) -> float | None:
    path = data_dir / ".agent_budgets.db"
    if not path.exists():
        return None
    try:
        with sqlite3.connect(f"file:{path}?mode=ro", uri=True, timeout=5) as conn:
            row = conn.execute(
                "SELECT spend_usd FROM agent_budgets WHERE agent = ?", (agent,)
            ).fetchone()
    except sqlite3.Error:
        return None
    return float(row[0]) if row else 0.0


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
                        piece = (ch.get("delta") or {}).get("content")
                        if isinstance(piece, str):
                            text.append(piece)
                return {"status": resp.status_code,
                        "shape": {"objects": sorted(o for o in objects if o), "done": done,
                                  "has_chunks": chunks > 0, "usage_chunk": usage is not None},
                        "usage": usage, "text": "".join(text), "secs": round(time.monotonic() - t0, 2)}
    except httpx.HTTPError as exc:
        return {"status": None, "shape": {"transport_error": type(exc).__name__}, "usage": None,
                "text": "", "secs": round(time.monotonic() - t0, 2)}


def _text_plain(data) -> str:
    try:
        return data["choices"][0]["message"]["content"] or ""
    except (KeyError, IndexError, TypeError):
        return ""


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--data-dir", default="/opt/taos/data", type=Path)
    ap.add_argument("--agent", required=True, help="an agent whose LiteLLM key is in the local key store")
    ap.add_argument("--model", help="default: the agent's model from config.yaml")
    ap.add_argument("--litellm-url", help="default: http://127.0.0.1:<server.litellm_port>/v1")
    ap.add_argument("--gateway-url", help="default: http://127.0.0.1:<server.llm_gateway_port or 7838>/v1")
    ap.add_argument("--prompt", action="append", help="repeatable; default: three built-in prompts")
    ap.add_argument("--json", action="store_true", help="print the full result as JSON")
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
    targets = {
        "litellm": args.litellm_url or f"http://127.0.0.1:{server.get('litellm_port', 7834)}/v1",
        "gateway": args.gateway_url or f"http://127.0.0.1:{server.get('llm_gateway_port') or 7838}/v1",
    }
    prompts = args.prompt or DEFAULT_PROMPTS

    rows, ok = [], True
    for prompt in prompts:
        for stream in (False, True):
            row = {"prompt": prompt[:40], "stream": stream}
            for side, url in targets.items():
                before = _spend(args.data_dir, args.agent)
                res = _call(url, key, model, prompt, stream)
                time.sleep(1.0)  # LiteLLM records spend from an async callback
                after = _spend(args.data_dir, args.agent)
                res["recorded_usd"] = (
                    round(after - before, 8) if before is not None and after is not None else None
                )
                if not args.json:
                    res.pop("text", None)
                row[side] = res
            lite, gw = row["litellm"], row["gateway"]
            row["status_match"] = lite["status"] == gw["status"]
            row["shape_match"] = lite["shape"] == gw["shape"]
            row["usage_ok"] = not (lite["usage"] and not gw["usage"])
            ok = ok and row["status_match"] and row["shape_match"] and row["usage_ok"]
            rows.append(row)

    print(f"model={model} agent={args.agent} litellm={targets['litellm']} gateway={targets['gateway']}")
    print(f"{'prompt':42} {'strm':5} {'status L/G':11} {'shape':6} {'tokens L':>16} {'tokens G':>16} {'usd L':>10} {'usd G':>10}")
    for r in rows:
        lu, gu = r["litellm"]["usage"] or {}, r["gateway"]["usage"] or {}
        print(
            f"{r['prompt']:42} {str(r['stream']):5} "
            f"{str(r['litellm']['status']) + '/' + str(r['gateway']['status']):11} "
            f"{'same' if r['shape_match'] else 'DIFF':6} "
            f"{str(lu.get('prompt_tokens')) + '+' + str(lu.get('completion_tokens')):>16} "
            f"{str(gu.get('prompt_tokens')) + '+' + str(gu.get('completion_tokens')):>16} "
            f"{str(r['litellm']['recorded_usd']):>10} {str(r['gateway']['recorded_usd']):>10}"
        )
        if not r["shape_match"]:
            print(f"    litellm shape: {r['litellm']['shape']}")
            print(f"    gateway shape: {r['gateway']['shape']}")
    if args.json:
        print(json.dumps(rows, indent=2))
    print("PARITY OK" if ok else "PARITY FAILED")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
