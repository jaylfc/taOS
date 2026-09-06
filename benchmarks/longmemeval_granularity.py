#!/usr/bin/env python3
"""LongMemEval-S Granularity Comparison.

Tests all embedding strategies for publishable scores:
1. Full session (all turns) — raw semantic
2. Full session (all turns) — hybrid
3. User-turns only — raw semantic
4. User-turns only — hybrid
5. Turn-level (one doc per turn) — raw semantic
6. Turn-level (one doc per turn) — hybrid
"""

import asyncio
import json
import os
import sys
import tempfile
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tinyagentos.vector_memory import VectorMemory

DATA_PATH = os.path.join(os.path.dirname(__file__), "data", "longmemeval_s_cleaned.json")


async def run_variant(data, name, build_docs_fn, hybrid, limit=100):
    """Run one benchmark variant."""
    hits = 0
    t0 = time.time()

    for qi in range(limit):
        item = data[qi]
        sessions = item.get("haystack_sessions", [])
        session_ids = item.get("haystack_session_ids", [])
        answer_ids = item.get("answer_session_ids", [])

        tmp = tempfile.mkdtemp()
        vmem = VectorMemory(db_path=os.path.join(tmp, "v.db"), embed_mode="onnx", onnx_path="models/minilm-onnx")
        await vmem.init()

        sid_map = {}
        docs = build_docs_fn(sessions, session_ids)
        for text, sid in docs:
            if text.strip():
                vid = await vmem.add(text, metadata={"sid": sid})
                if vid > 0:
                    sid_map[vid] = sid

        results = await vmem.search(item["question"], limit=5, hybrid=hybrid)
        retrieved = set()
        for r in results:
            sid = r.get("metadata", {}).get("sid", "")
            if sid:
                retrieved.add(sid)
            vid = r.get("id")
            if vid in sid_map:
                retrieved.add(sid_map[vid])

        if any(a in retrieved for a in answer_ids):
            hits += 1

        await vmem.close()

    elapsed = time.time() - t0
    pct = hits / limit * 100
    return {"name": name, "hits": hits, "total": limit, "pct": pct, "time": elapsed, "per_q": elapsed / limit}


def build_full_session(sessions, session_ids):
    """One doc per session — all turns concatenated."""
    docs = []
    for si, session in enumerate(sessions):
        text = " ".join(t.get("content", "") for t in session if t.get("content"))
        sid = session_ids[si] if si < len(session_ids) else f"s{si}"
        docs.append((text, sid))
    return docs


def build_user_only(sessions, session_ids):
    """One doc per session — user turns only."""
    docs = []
    for si, session in enumerate(sessions):
        text = " ".join(t.get("content", "") for t in session if t.get("role") == "user" and t.get("content"))
        sid = session_ids[si] if si < len(session_ids) else f"s{si}"
        docs.append((text, sid))
    return docs


def build_turn_level(sessions, session_ids):
    """One doc per turn — each turn is a separate document."""
    docs = []
    for si, session in enumerate(sessions):
        sid = session_ids[si] if si < len(session_ids) else f"s{si}"
        for turn in session:
            content = turn.get("content", "")
            if content:
                docs.append((content, sid))
    return docs


async def main():
    print("=" * 70)
    print("LongMemEval-S Granularity Comparison")
    print("=" * 70)

    data = json.load(open(DATA_PATH))
    limit = 100

    print(f"Running {limit} questions per variant\n")

    variants = [
        ("Full session, raw semantic", build_full_session, False),
        ("Full session, hybrid", build_full_session, True),
        ("User-turns only, raw semantic", build_user_only, False),
        ("User-turns only, hybrid", build_user_only, True),
        ("Turn-level, raw semantic", build_turn_level, False),
        ("Turn-level, hybrid", build_turn_level, True),
    ]

    results = []
    for name, builder, hybrid in variants:
        print(f"  Testing: {name}...", end="", flush=True)
        r = await run_variant(data, name, builder, hybrid, limit=limit)
        results.append(r)
        print(f" {r['pct']:.1f}% ({r['per_q']:.1f}s/q)")

    print(f"\n{'=' * 70}")
    print(f"{'Variant':<40s} {'Recall@5':>10s} {'Speed':>10s}")
    print(f"{'-' * 40} {'-' * 10} {'-' * 10}")
    for r in sorted(results, key=lambda x: -x["pct"]):
        print(f"  {r['name']:<38s} {r['pct']:>8.1f}% {r['per_q']:>8.1f}s/q")

    best = max(results, key=lambda x: x["pct"])
    print(f"\n  Best: {best['name']} ({best['pct']:.1f}%)")
    print(f"  MemPalace (raw): 96.6%")
    print(f"{'=' * 70}")


if __name__ == "__main__":
    asyncio.run(main())
