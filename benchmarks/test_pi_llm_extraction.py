#!/usr/bin/env python3
"""Test LLM fact extraction on the Pi's NPU (Qwen3-4B via rkllama)."""

import asyncio
import json
import time
import httpx

LLM_URL = "http://localhost:8080"
MODEL = "qwen3-4b-chat"

SEED = [
    "Jay created taOS, a personal AI operating system. taOS runs on the Orange Pi 5 Plus which has an RK3588 NPU with 6 TOPS of compute.",
    "The Knowledge Pipeline uses Python for the backend and React for the frontend. It supports Reddit, YouTube, GitHub, and X for content ingestion.",
    "Jay prefers running local models on the NPU rather than using cloud APIs. The system uses SQLite for storage and QMD for vector search.",
    "The research agent monitors Reddit and YouTube daily for new content about AI and Rockchip development.",
    "taOS has 32 bundled apps including a Library, Reddit Client, YouTube Library, GitHub Browser, and X Monitor.",
    "The dev agent works on the knowledge pipeline and manages GitHub integrations. It depends on the Agent Browsers system for cookie authentication.",
]

PROMPT = """Extract facts as a JSON array. Each fact: {{"subject":"name","predicate":"verb","object":"name"}}.
Rules: subject and object must be short entity names (1-5 words), not full sentences.
Predicates: created, uses, prefers, runs_on, has, monitors, works_on, manages, supports, depends_on, is_a.

Text: {text}

JSON array:"""

EXPECTED = [
    ("Jay", "created", "taOS"),
    ("taOS", "is_a", "personal AI operating system"),
    ("taOS", "runs_on", "Orange Pi 5 Plus"),
    ("Orange Pi 5 Plus", "has", "RK3588 NPU"),
    ("Knowledge Pipeline", "uses", "Python"),
    ("Knowledge Pipeline", "uses", "React"),
    ("Knowledge Pipeline", "supports", "Reddit"),
    ("Knowledge Pipeline", "supports", "YouTube"),
    ("Knowledge Pipeline", "supports", "GitHub"),
    ("Jay", "prefers", "local models"),
    ("system", "uses", "SQLite"),
    ("system", "uses", "QMD"),
    ("research agent", "monitors", "Reddit"),
    ("research agent", "monitors", "YouTube"),
    ("taOS", "has", "32 bundled apps"),
    ("dev agent", "works_on", "knowledge pipeline"),
    ("dev agent", "manages", "GitHub integrations"),
    ("dev agent", "depends_on", "Agent Browsers"),
]


def score(extracted, expected):
    hits = 0
    for subj, pred, obj in expected:
        for f in extracted:
            s = f.get("subject", "").lower()
            p = f.get("predicate", "").lower()
            o = f.get("object", "").lower()
            if (subj.lower() in s or s in subj.lower()) and \
               (pred.lower() in p or p in pred.lower()) and \
               (obj.lower() in o or o in obj.lower()):
                hits += 1
                break
    return hits, len(expected)


async def main():
    print("=" * 60)
    print(f"LLM Fact Extraction on Pi NPU ({MODEL})")
    print("=" * 60)

    async with httpx.AsyncClient() as client:
        # Warm up model
        print("\nWarming up model...")
        await client.post(f"{LLM_URL}/v1/chat/completions", json={
            "model": MODEL,
            "messages": [{"role": "user", "content": "Hi"}],
            "max_tokens": 5,
        }, timeout=60)

        all_facts = []
        total_time = 0
        total_tokens = 0

        print(f"\nExtracting from {len(SEED)} passages...\n")

        for i, text in enumerate(SEED):
            t0 = time.time()
            resp = await client.post(f"{LLM_URL}/v1/chat/completions", json={
                "model": MODEL,
                "messages": [{"role": "user", "content": PROMPT.format(text=text)}],
                "temperature": 0,
                "max_tokens": 500,
            }, timeout=60)
            dt = time.time() - t0
            total_time += dt

            data = resp.json()
            content = data["choices"][0]["message"]["content"]
            tokens = data["usage"]["completion_tokens"]
            total_tokens += tokens

            # Parse JSON from response
            content = content.strip()
            if content.startswith("```"):
                lines = content.split("\n")
                content = "\n".join(lines[1:])
                if "```" in content:
                    content = content[:content.rindex("```")]

            try:
                facts = json.loads(content)
                if not isinstance(facts, list):
                    facts = []
            except json.JSONDecodeError:
                facts = []

            all_facts.extend(facts)
            print(f"  Turn {i+1} ({dt:.1f}s, {tokens}tok): {len(facts)} facts")
            for f in facts:
                s = f.get("subject", "?")
                p = f.get("predicate", "?")
                o = f.get("object", "?")
                print(f"    {s} --{p}--> {o}")

        # Score against ground truth
        hits, total = score(all_facts, EXPECTED)

        # Also run regex for comparison
        from tinyagentos.memory_extractor import extract_facts_from_text
        regex_facts = []
        regex_t0 = time.time()
        for text in SEED:
            regex_facts.extend(extract_facts_from_text(text))
        regex_time = (time.time() - regex_t0) * 1000
        regex_hits, _ = score(regex_facts, EXPECTED)

        print("\n" + "=" * 60)
        print("RESULTS")
        print("=" * 60)
        print(f"\n  {'Method':<20s} {'Facts':>6s} {'Hits':>6s} {'Recall':>8s} {'Time':>10s} {'Tok/turn':>10s}")
        print(f"  {'-'*20} {'-'*6} {'-'*6} {'-'*8} {'-'*10} {'-'*10}")
        print(f"  {'Regex':<20s} {len(regex_facts):>6d} {regex_hits:>6d} {regex_hits/total:>7.0%} {regex_time:>9.0f}ms {'N/A':>10s}")
        print(f"  {'LLM (Qwen3-4B NPU)':<20s} {len(all_facts):>6d} {hits:>6d} {hits/total:>7.0%} {total_time*1000:>9.0f}ms {total_tokens/len(SEED):>9.0f}t")

        if hits > regex_hits:
            improvement = ((hits - regex_hits) / regex_hits * 100) if regex_hits > 0 else float("inf")
            print(f"\n  LLM improves over regex by {improvement:.0f}% ({hits - regex_hits} more facts)")
        elif hits == regex_hits:
            print(f"\n  Same recall — LLM offers no improvement over regex for this corpus")
        else:
            print(f"\n  Regex wins! LLM is worse by {regex_hits - hits} facts")

        print(f"\n  Ground truth: {total} facts")
        print(f"  LLM speed: {total_time/len(SEED):.1f}s per passage on RK3588 NPU")
        print("=" * 60)


if __name__ == "__main__":
    asyncio.run(main())
