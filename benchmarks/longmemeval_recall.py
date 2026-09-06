#!/usr/bin/env python3
"""LongMemEval Recall@k Benchmark — Same metric as MemPalace.

Measures: Does at least one correct answer session appear in top-k
retrieved results? This is a RETRIEVAL metric, not QA accuracy.

MemPalace scores:
  Raw mode:  96.6% Recall@5
  AAAK:      84.2% Recall@5
  Room-based: 89.4% Recall@5
"""

import asyncio
import json
import os
import sys
import tempfile
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import httpx
from tinyagentos.vector_memory import VectorMemory

DATA_PATH = os.path.join(os.path.dirname(__file__), "data", "longmemeval_oracle.json")
QMD_URL = "http://localhost:7832"


async def run_recall_benchmark(limit: int = 50, top_k: int = 5, question_type: str | None = None):
    print("=" * 70)
    print(f"LongMemEval Recall@{top_k} Benchmark (same metric as MemPalace)")
    print("=" * 70)

    with open(DATA_PATH) as f:
        dataset = json.load(f)

    if question_type:
        dataset = [q for q in dataset if q["question_type"] == question_type]
        print(f"Filtered: {question_type} ({len(dataset)} questions)")

    dataset = dataset[:limit]
    print(f"Running {len(dataset)} questions with Recall@{top_k}")

    results_by_type = {}
    total_recall = 0
    total_questions = 0
    total_time = 0

    async with httpx.AsyncClient(timeout=15) as embed_client:
        for i, item in enumerate(dataset):
            qtype = item["question_type"]
            question = item["question"]
            answer_session_ids = item.get("answer_session_ids", [])
            sessions = item.get("haystack_sessions", [])
            session_ids = item.get("haystack_session_ids", [])

            # Create fresh vector memory per question
            tmp = tempfile.mkdtemp()
            vmem = VectorMemory(db_path=os.path.join(tmp, "v.db"), qmd_url=QMD_URL)
            await vmem.init(http_client=embed_client)

            # Ingest: embed each session as a single document
            t0 = time.time()
            session_map = {}  # vmem_id -> session_id
            for si, session in enumerate(sessions):
                # Join all turns into one document per session
                session_text = "\n".join(
                    f"[{t.get('role','user')}]: {t.get('content','')}"
                    for t in session if t.get("content")
                )
                if session_text:
                    # Truncate to embedding model limit
                    vmem_id = await vmem.add(session_text[:512], metadata={"session_id": session_ids[si] if si < len(session_ids) else f"s{si}"})
                    if vmem_id > 0:
                        sid = session_ids[si] if si < len(session_ids) else f"s{si}"
                        session_map[vmem_id] = sid

            ingest_time = time.time() - t0

            # Retrieve: semantic search for the question
            t1 = time.time()
            results = await vmem.search(question, limit=top_k)
            retrieve_time = time.time() - t1

            # Score: Recall@k — does ANY correct session appear in top-k?
            retrieved_session_ids = set()
            for r in results:
                meta = r.get("metadata", {})
                sid = meta.get("session_id", "")
                if sid:
                    retrieved_session_ids.add(sid)
                # Also try matching by vmem id
                vmem_id = r.get("id")
                if vmem_id in session_map:
                    retrieved_session_ids.add(session_map[vmem_id])

            recall_hit = any(aid in retrieved_session_ids for aid in answer_session_ids)

            total_questions += 1
            if recall_hit:
                total_recall += 1

            if qtype not in results_by_type:
                results_by_type[qtype] = {"hits": 0, "total": 0}
            results_by_type[qtype]["total"] += 1
            if recall_hit:
                results_by_type[qtype]["hits"] += 1

            total_time += ingest_time + retrieve_time
            status = "✓" if recall_hit else "✗"
            print(f"  [{i+1:3d}/{len(dataset)}] {status} {qtype:25s} | embed:{ingest_time:.1f}s search:{retrieve_time:.3f}s | {question[:50]}")

            await vmem.close()

    overall = total_recall / total_questions * 100 if total_questions > 0 else 0

    print(f"\n{'='*70}")
    print(f"RESULTS — Recall@{top_k}")
    print(f"{'='*70}")
    print(f"\n  Overall: {total_recall}/{total_questions} ({overall:.1f}%)")
    print(f"  Time: {total_time:.1f}s ({total_time/total_questions:.1f}s/question)")

    print(f"\n  By category:")
    for qtype, data in sorted(results_by_type.items()):
        pct = data["hits"] / data["total"] * 100 if data["total"] > 0 else 0
        print(f"    {qtype:30s} {data['hits']:3d}/{data['total']:<3d} ({pct:.1f}%)")

    print(f"\n  Comparison (Recall@{top_k}):")
    print(f"    MemPalace (raw, all-MiniLM-L6):    96.6%")
    print(f"    MemPalace (AAAK compressed):        84.2%")
    print(f"    MemPalace (room-based):             89.4%")
    print(f"    taOSmd (NPU, Qwen3-Embed-0.6B):    {overall:.1f}%")
    print(f"{'='*70}")

    return overall


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--limit", type=int, default=50)
    parser.add_argument("--top-k", type=int, default=5)
    parser.add_argument("--type", type=str, default=None)
    args = parser.parse_args()
    asyncio.run(run_recall_benchmark(limit=args.limit, top_k=args.top_k, question_type=args.type))
