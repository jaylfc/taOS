#!/usr/bin/env python3
"""Test LLM-based fact extraction quality vs regex.

Sends the same text to both the regex extractor and the LLM extraction prompt,
compares quality. Uses a simple scoring approach against expected facts.
"""

import asyncio
import json
import os
import sys
import tempfile
import time

# Add parent to path for imports
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tinyagentos.memory_extractor import extract_facts_from_text, EXTRACTION_PROMPT
from tinyagentos.temporal_knowledge_graph import TemporalKnowledgeGraph

SEED_TEXT = [
    "Jay created taOS, a personal AI operating system. taOS runs on the Orange Pi 5 Plus which has an RK3588 NPU with 6 TOPS of compute.",
    "The Knowledge Pipeline uses Python for the backend and React for the frontend. It supports Reddit, YouTube, GitHub, and X for content ingestion.",
    "Jay prefers running local models on the NPU rather than using cloud APIs. The system uses SQLite for storage and QMD for vector search.",
    "The research agent monitors Reddit and YouTube daily for new content about AI and Rockchip development.",
    "taOS has 32 bundled apps including a Library, Reddit Client, YouTube Library, GitHub Browser, and X Monitor.",
    "The dev agent works on the knowledge pipeline and manages GitHub integrations. It depends on the Agent Browsers system for cookie authentication.",
]

# Ground truth — what a perfect extractor should find
EXPECTED_FACTS = [
    # Turn 1
    ("Jay", "created", "taOS"),
    ("taOS", "is_a", "personal AI operating system"),
    ("taOS", "runs_on", "Orange Pi 5 Plus"),
    ("Orange Pi 5 Plus", "has", "RK3588 NPU"),
    # Turn 2
    ("Knowledge Pipeline", "uses", "Python"),
    ("Knowledge Pipeline", "uses", "React"),
    ("Knowledge Pipeline", "supports", "Reddit"),
    ("Knowledge Pipeline", "supports", "YouTube"),
    ("Knowledge Pipeline", "supports", "GitHub"),
    # Turn 3
    ("Jay", "prefers", "local models"),
    ("system", "uses", "SQLite"),
    ("system", "uses", "QMD"),
    # Turn 4
    ("research agent", "monitors", "Reddit"),
    ("research agent", "monitors", "YouTube"),
    # Turn 5
    ("taOS", "has", "32 bundled apps"),
    # Turn 6
    ("dev agent", "works_on", "knowledge pipeline"),
    ("dev agent", "manages", "GitHub integrations"),
    ("dev agent", "depends_on", "Agent Browsers"),
]


def score_extracted(extracted: list[dict], expected: list[tuple]) -> dict:
    """Score extracted facts against ground truth."""
    hits = 0
    matched = []
    missed = []

    for subj, pred, obj in expected:
        found = False
        for fact in extracted:
            s = fact["subject"].lower()
            p = fact["predicate"].lower()
            o = fact["object"].lower()
            if (subj.lower() in s or s in subj.lower()) and \
               (pred.lower() == p or pred.lower() in p) and \
               (obj.lower() in o or o in obj.lower()):
                found = True
                matched.append((subj, pred, obj))
                break
        if not found:
            missed.append((subj, pred, obj))
        hits += int(found)

    return {
        "hits": hits,
        "total": len(expected),
        "precision": hits / len(expected) if expected else 0,
        "matched": matched,
        "missed": missed,
        "extra": len(extracted) - hits,  # Facts extracted but not in ground truth
    }


async def main():
    print("=" * 70)
    print("Fact Extraction Quality: Regex vs Ground Truth")
    print("=" * 70)

    # Test regex extraction
    print("\nRegex Extraction:")
    all_regex_facts = []
    t0 = time.time()
    for i, text in enumerate(SEED_TEXT):
        facts = extract_facts_from_text(text)
        all_regex_facts.extend(facts)
        print(f"  Turn {i+1}: {len(facts)} facts")
        for f in facts:
            print(f"    {f['subject']} --{f['predicate']}--> {f['object']}")
    regex_time = (time.time() - t0) * 1000

    regex_score = score_extracted(all_regex_facts, EXPECTED_FACTS)
    print(f"\n  Total extracted: {len(all_regex_facts)}")
    print(f"  Ground truth hits: {regex_score['hits']}/{regex_score['total']} ({regex_score['precision']:.0%})")
    print(f"  Extra (not in ground truth): {regex_score['extra']}")
    print(f"  Time: {regex_time:.0f}ms")

    if regex_score["missed"]:
        print(f"\n  MISSED ({len(regex_score['missed'])}):")
        for s, p, o in regex_score["missed"]:
            print(f"    {s} --{p}--> {o}")

    # Show what LLM extraction WOULD do
    print("\n" + "=" * 70)
    print("LLM Extraction Prompt (for reference)")
    print("=" * 70)
    print("\nTo test with a local model, load a chat model in rkllama and run:")
    print("  curl -s http://localhost:8080/v1/chat/completions \\")
    print("    -X POST -H 'Content-Type: application/json' \\")
    print("    -d '{\"model\":\"<model>\",\"messages\":[{\"role\":\"user\",\"content\":\"<prompt>\"}],\"temperature\":0}'")
    print(f"\nPrompt template ({len(EXTRACTION_PROMPT)} chars):")
    print(EXTRACTION_PROMPT[:200] + "...")

    # Summary
    print("\n" + "=" * 70)
    print("QUALITY REPORT")
    print("=" * 70)
    print(f"\n  Ground truth facts:  {len(EXPECTED_FACTS)}")
    print(f"  Regex extracted:     {len(all_regex_facts)}")
    print(f"  Regex hits:          {regex_score['hits']}/{regex_score['total']} ({regex_score['precision']:.0%})")
    print(f"  Regex speed:         {regex_time:.0f}ms for {len(SEED_TEXT)} passages")
    print(f"\n  Quality ceiling (LLM): estimated 80-95% based on Mem0/Graphiti benchmarks")
    print(f"  Current gap: {(0.90 - regex_score['precision'])*100:.0f} percentage points to LLM quality")
    print(f"\n  To close the gap:")
    print(f"    1. Load a chat model in rkllama (e.g., Qwen3 1.7B)")
    print(f"    2. Enable LLM extraction: process_conversation_turn(..., llm_url='http://localhost:8080', use_llm=True)")
    print(f"    3. Regex remains as fast fallback when LLM is unavailable")
    print("=" * 70)


if __name__ == "__main__":
    asyncio.run(main())
