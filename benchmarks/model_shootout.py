#!/usr/bin/env python3
"""taOSmd Model Shootout — Test every available model on fact extraction.

Benchmarks speed and quality across all RKLLM models loaded in rkllama.
"""

import asyncio
import json
import time
import httpx

LLM_URL = "http://localhost:8080"

TEST_TEXT = "Jay created taOS, a personal AI operating system. taOS runs on the Orange Pi 5 Plus. The research agent monitors Reddit and YouTube daily. Jay prefers local models over cloud APIs."

EXPECTED_FACTS = [
    ("Jay", "created", "taOS"),
    ("taOS", "runs_on", "Orange Pi 5 Plus"),
    ("research agent", "monitors", "Reddit"),
    ("research agent", "monitors", "YouTube"),
    ("Jay", "prefers", "local models"),
]

PROMPT = """Extract facts as a JSON array. Each fact: {{"subject":"name","predicate":"verb","object":"name"}}.
Keep subject and object short (1-5 words). Return ONLY valid JSON array.

Text: {text}

JSON:"""


def score(response_text: str, expected: list[tuple]) -> tuple[int, list[str], list[str]]:
    """Score extraction against expected facts."""
    # Try to parse JSON from response
    text = response_text.strip()
    if text.startswith("```"):
        lines = text.split("\n")
        text = "\n".join(lines[1:])
        if "```" in text:
            text = text[:text.rindex("```")]

    try:
        facts = json.loads(text)
        if not isinstance(facts, list):
            facts = []
    except json.JSONDecodeError:
        facts = []

    hits = []
    missed = []
    for subj, pred, obj in expected:
        found = False
        for f in facts:
            s = str(f.get("subject", "")).lower()
            p = str(f.get("predicate", "")).lower()
            o = str(f.get("object", "")).lower()
            if (subj.lower() in s or s in subj.lower()) and \
               (pred.lower() in p or p in pred.lower()) and \
               (obj.lower() in o or o in obj.lower()):
                found = True
                break
        if found:
            hits.append(f"{subj} {pred} {obj}")
        else:
            missed.append(f"{subj} {pred} {obj}")

    return len(hits), hits, missed


async def test_model(client: httpx.AsyncClient, model: str) -> dict:
    """Test a single model on fact extraction."""
    try:
        t0 = time.time()
        resp = await client.post(f"{LLM_URL}/v1/chat/completions", json={
            "model": model,
            "messages": [{"role": "user", "content": PROMPT.format(text=TEST_TEXT)}],
            "temperature": 0,
            "max_tokens": 300,
        }, timeout=120)
        total_time = time.time() - t0

        if resp.status_code != 200:
            return {"model": model, "error": f"HTTP {resp.status_code}", "time": total_time}

        data = resp.json()
        content = data["choices"][0]["message"]["content"]
        usage = data.get("usage", {})
        gen_time = usage.get("total_duration", 0)
        comp_tokens = usage.get("completion_tokens", 0)

        hits, hit_list, missed = score(content, EXPECTED_FACTS)

        return {
            "model": model,
            "hits": hits,
            "total": len(EXPECTED_FACTS),
            "recall": hits / len(EXPECTED_FACTS),
            "gen_time": gen_time,
            "total_time": round(total_time, 1),
            "tokens": comp_tokens,
            "tok_per_sec": round(comp_tokens / gen_time, 1) if gen_time > 0 else 0,
            "output": content[:200],
            "hits_detail": hit_list,
            "missed": missed,
        }
    except Exception as e:
        return {"model": model, "error": str(e), "time": 0}


async def main():
    print("=" * 70)
    print("taOSmd Model Shootout — Fact Extraction Benchmark")
    print("=" * 70)
    print(f"\nTest text: {TEST_TEXT[:80]}...")
    print(f"Expected facts: {len(EXPECTED_FACTS)}")

    async with httpx.AsyncClient() as client:
        # Get available models
        resp = await client.get(f"{LLM_URL}/v1/models", timeout=10)
        models = [m["id"] for m in resp.json().get("data", [])]

        # Filter out embedding/reranker models
        skip = ["embedding", "reranker"]
        test_models = [m for m in models if not any(s in m.lower() for s in skip)]

        print(f"Models to test: {len(test_models)}")
        for m in test_models:
            print(f"  - {m}")

        # Test each model
        print(f"\n{'='*70}")
        results = []

        for i, model in enumerate(test_models):
            print(f"\n[{i+1}/{len(test_models)}] Testing {model}...", end="", flush=True)
            result = await test_model(client, model)
            results.append(result)
            # Brief pause to let rkllama unload the model before loading the next
            if i < len(test_models) - 1:
                await asyncio.sleep(2)

            if "error" in result:
                print(f" ERROR: {result['error']}")
            else:
                print(f" {result['hits']}/{result['total']} ({result['recall']:.0%}) in {result['total_time']}s ({result['tokens']}tok, {result['tok_per_sec']}t/s)")

        # Sort by recall then speed
        valid = [r for r in results if "error" not in r]
        valid.sort(key=lambda r: (-r["recall"], r["total_time"]))

        # Results table
        print(f"\n{'='*70}")
        print("RESULTS (sorted by recall, then speed)")
        print(f"{'='*70}")
        print(f"\n  {'Model':<25s} {'Recall':>7s} {'Facts':>6s} {'Time':>7s} {'Tok/s':>7s} {'Tokens':>7s}")
        print(f"  {'-'*25} {'-'*7} {'-'*6} {'-'*7} {'-'*7} {'-'*7}")

        for r in valid:
            print(f"  {r['model']:<25s} {r['recall']:>6.0%} {r['hits']:>2d}/{r['total']:<2d} {r['total_time']:>6.1f}s {r['tok_per_sec']:>6.1f} {r['tokens']:>6d}")

        # Errors
        errors = [r for r in results if "error" in r]
        if errors:
            print(f"\n  ERRORS:")
            for r in errors:
                print(f"  {r['model']:<25s} {r['error']}")

        # Best picks
        if valid:
            best_quality = max(valid, key=lambda r: r["recall"])
            best_speed = min(valid, key=lambda r: r["total_time"])
            best_balanced = max(valid, key=lambda r: r["recall"] * (1 / (r["total_time"] + 1)))

            print(f"\n  RECOMMENDATIONS:")
            print(f"  Best quality:  {best_quality['model']} ({best_quality['recall']:.0%} recall)")
            print(f"  Best speed:    {best_speed['model']} ({best_speed['total_time']}s)")
            print(f"  Best balanced: {best_balanced['model']} ({best_balanced['recall']:.0%} in {best_balanced['total_time']}s)")

        # Also show regex baseline
        from tinyagentos.memory_extractor import extract_facts_from_text
        t0 = time.time()
        regex_facts = extract_facts_from_text(TEST_TEXT)
        regex_time = (time.time() - t0) * 1000
        regex_hits, _, _ = score(json.dumps(regex_facts), EXPECTED_FACTS)
        print(f"\n  Regex baseline: {regex_hits}/{len(EXPECTED_FACTS)} ({regex_hits/len(EXPECTED_FACTS):.0%}) in {regex_time:.0f}ms")

        print(f"\n{'='*70}")


if __name__ == "__main__":
    asyncio.run(main())
