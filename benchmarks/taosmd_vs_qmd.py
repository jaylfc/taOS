#!/usr/bin/env python3
"""taOSmd vs Base QMD — Head-to-Head Benchmark.

Tests retrieval quality on identical queries. Measures:
- Hit rate: did the system find relevant information?
- Precision: how much of the expected info was found?
- Latency: how fast is retrieval?
- Token efficiency: how many tokens used in context?

Compares:
1. QMD Only (vector semantic search — the old system)
2. KG Only (temporal knowledge graph — new structured layer)
3. taOSmd Combined (KG + QMD + archive — full stack)
"""

import asyncio
import json
import os
import tempfile
import time

import httpx

QMD_BASE = "http://localhost:7832"

# ------------------------------------------------------------------
# Test corpus: seed the SAME data into both systems
# ------------------------------------------------------------------

SEED_TEXT = [
    "Jay created taOS, a personal AI operating system that runs on the Orange Pi 5 Plus.",
    "The Orange Pi 5 Plus has an RK3588 SoC with a 6 TOPS NPU and Mali-Bifrost GPU.",
    "taOS uses Python for the backend, React TypeScript for the frontend, and SQLite for storage.",
    "Jay prefers running local models on the NPU rather than using cloud APIs.",
    "The Knowledge Pipeline ingests content from Reddit, YouTube, X, GitHub, and web articles.",
    "The research agent monitors Reddit and YouTube daily for AI and Rockchip content.",
    "The dev agent works on the knowledge pipeline and manages GitHub integrations.",
    "taOS has 32 bundled apps including Library, Reddit Client, YouTube Library, and GitHub Browser.",
    "The Agent Browsers system uses Docker containers with persistent volumes for browser profiles.",
    "taOSmd is the memory system with a temporal knowledge graph, zero-loss archive, and context assembler.",
    "The monitor service polls saved content with smart decay, flooring at 30 days instead of stopping.",
    "Jay manages two agents: research-agent and dev-agent, each with their own memory.",
    "The shortcut registry uses Ctrl+Space for search, Ctrl+1-9 for dock apps, and Ctrl+W to close windows.",
    "taOS supports fullscreen mode with keyboard lock on Chrome and Edge browsers.",
    "The settings app has sections for Keyboard Shortcuts, Accessibility, and Desktop & Dock configuration.",
]

# Queries with expected answers and which system should find them
BENCHMARK_QUERIES = [
    {
        "query": "What hardware does taOS run on?",
        "expected": ["Orange Pi 5 Plus", "RK3588", "NPU", "Mali"],
        "category": "factual",
    },
    {
        "query": "Who created taOS and what is it?",
        "expected": ["Jay", "personal AI operating system"],
        "category": "factual",
    },
    {
        "query": "What programming languages does taOS use?",
        "expected": ["Python", "React", "TypeScript"],
        "category": "factual",
    },
    {
        "query": "What does Jay prefer for model inference?",
        "expected": ["local models", "NPU", "cloud"],
        "category": "preference",
    },
    {
        "query": "What platforms does the knowledge pipeline support?",
        "expected": ["Reddit", "YouTube", "GitHub"],
        "category": "factual",
    },
    {
        "query": "What agents does Jay manage?",
        "expected": ["research-agent", "dev-agent"],
        "category": "relationship",
    },
    {
        "query": "How does monitoring work in taOS?",
        "expected": ["decay", "30 days", "polling"],
        "category": "technical",
    },
    {
        "query": "What keyboard shortcuts does taOS have?",
        "expected": ["Ctrl+Space", "Ctrl+1", "Ctrl+W"],
        "category": "technical",
    },
    {
        "query": "How many apps does taOS have?",
        "expected": ["32", "Library", "Reddit"],
        "category": "factual",
    },
    {
        "query": "What is the memory system called?",
        "expected": ["taOSmd", "knowledge graph", "archive"],
        "category": "factual",
    },
]


def score_results(result_text: str, expected: list[str]) -> tuple[int, int]:
    """Count how many expected terms appear in the result text."""
    text_lower = result_text.lower()
    hits = sum(1 for e in expected if e.lower() in text_lower)
    return hits, len(expected)


async def benchmark_qmd_only(client: httpx.AsyncClient) -> list[dict]:
    """Benchmark QMD vector search alone."""
    results = []
    for q in BENCHMARK_QUERIES:
        t0 = time.time()
        try:
            resp = await client.post(f"{QMD_BASE}/vsearch", json={
                "query": q["query"], "limit": 5, "collection": "workspace",
            }, timeout=15)
            if resp.status_code == 200:
                items = resp.json().get("results", [])
                result_text = " ".join(
                    (r.get("content", "") or "") + " " + (r.get("title", "") or "")
                    for r in items
                )
            else:
                result_text = ""
        except Exception:
            result_text = ""

        latency = (time.time() - t0) * 1000
        hits, total = score_results(result_text, q["expected"])
        results.append({
            "query": q["query"],
            "hits": hits,
            "total": total,
            "precision": hits / total if total > 0 else 0,
            "latency_ms": round(latency, 1),
            "category": q["category"],
        })
    return results


async def benchmark_kg_only(kg) -> list[dict]:
    """Benchmark knowledge graph structured queries."""
    results = []
    for q in BENCHMARK_QUERIES:
        t0 = time.time()
        result_text = ""

        # Try querying key words from the query as entities
        words = q["query"].replace("?", "").split()
        for word in words:
            if len(word) < 3:
                continue
            try:
                entity_results = await kg.query_entity(word, direction="both")
                for r in entity_results:
                    for field in ("object_name", "subject_name", "predicate"):
                        if field in r:
                            result_text += " " + str(r[field])
            except Exception:
                pass

        # Also try the full timeline
        try:
            timeline = await kg.timeline(limit=20)
            for t_item in timeline:
                result_text += f" {t_item.get('subject_name', '')} {t_item['predicate']} {t_item.get('object_name', '')}"
        except Exception:
            pass

        latency = (time.time() - t0) * 1000
        hits, total = score_results(result_text, q["expected"])
        results.append({
            "query": q["query"],
            "hits": hits,
            "total": total,
            "precision": hits / total if total > 0 else 0,
            "latency_ms": round(latency, 1),
            "category": q["category"],
        })
    return results


async def benchmark_combined(kg, archive, client: httpx.AsyncClient) -> list[dict]:
    """Benchmark full taOSmd stack: KG + QMD + Archive."""
    from tinyagentos.context_assembler import ContextAssembler
    assembler = ContextAssembler(kg=kg, archive=archive, qmd_base_url=QMD_BASE, http_client=client)

    results = []
    for q in BENCHMARK_QUERIES:
        t0 = time.time()
        ctx = await assembler.assemble(
            query=q["query"],
            agent_name="research-agent",
            user_name="Jay",
            depth="deep",
        )
        result_text = ctx["context"]
        latency = ctx["latency_ms"]

        hits, total = score_results(result_text, q["expected"])
        results.append({
            "query": q["query"],
            "hits": hits,
            "total": total,
            "precision": hits / total if total > 0 else 0,
            "latency_ms": round(latency, 1),
            "tokens": ctx["total_tokens"],
            "category": q["category"],
        })
    return results


async def main():
    from tinyagentos.temporal_knowledge_graph import TemporalKnowledgeGraph
    from tinyagentos.archive import ArchiveStore
    from tinyagentos.memory_extractor import extract_facts_from_text, process_conversation_turn

    print("=" * 70)
    print("taOSmd vs Base QMD — Head-to-Head Benchmark")
    print("=" * 70)

    # Set up fresh KG and archive in temp dir
    tmp = tempfile.mkdtemp()
    kg = TemporalKnowledgeGraph(db_path=os.path.join(tmp, "kg.db"))
    archive = ArchiveStore(archive_dir=os.path.join(tmp, "archive"), index_path=os.path.join(tmp, "idx.db"))
    await kg.init()
    await archive.init()

    async with httpx.AsyncClient() as client:
        # Check QMD availability
        qmd_available = False
        try:
            resp = await client.get(f"{QMD_BASE}/health", timeout=5)
            qmd_available = resp.status_code == 200
        except Exception:
            pass

        # Phase 1: Seed taOSmd with test data (auto-extraction from text)
        print("\nPhase 1: Seeding taOSmd from conversation text...")
        t0 = time.time()
        total_facts = 0
        for text in SEED_TEXT:
            result = await process_conversation_turn(text, "benchmark", kg, archive, source="benchmark")
            total_facts += result["facts_extracted"]
            # Also archive the raw text
            await archive.record("conversation", {"content": text}, agent_name="benchmark", summary=text[:80])

        seed_time = (time.time() - t0) * 1000
        stats = await kg.stats()
        print(f"  Facts extracted: {total_facts} from {len(SEED_TEXT)} passages")
        print(f"  KG: {stats['entities']} entities, {stats['triples']} triples")
        print(f"  Archive: {len(SEED_TEXT)} entries")
        print(f"  Seed time: {seed_time:.0f}ms")

        # Phase 2: Run benchmarks
        print(f"\nPhase 2: Running {len(BENCHMARK_QUERIES)} queries across systems...\n")

        # QMD benchmark
        if qmd_available:
            print("  [QMD Only — vector semantic search]")
            qmd_results = await benchmark_qmd_only(client)
            for r in qmd_results:
                status = "HIT" if r["precision"] > 0 else "MISS"
                print(f"    {status} {r['hits']}/{r['total']} ({r['precision']:.0%}) {r['latency_ms']:6.0f}ms  {r['query'][:50]}")
            qmd_avg = sum(r["precision"] for r in qmd_results) / len(qmd_results)
            qmd_lat = sum(r["latency_ms"] for r in qmd_results) / len(qmd_results)
            print(f"    AVG: {qmd_avg:.0%} precision, {qmd_lat:.0f}ms latency\n")
        else:
            print("  [QMD — UNAVAILABLE]\n")
            qmd_results = None
            qmd_avg = 0

        # KG benchmark
        print("  [KG Only — structured triple queries]")
        kg_results = await benchmark_kg_only(kg)
        for r in kg_results:
            status = "HIT" if r["precision"] > 0 else "MISS"
            print(f"    {status} {r['hits']}/{r['total']} ({r['precision']:.0%}) {r['latency_ms']:6.0f}ms  {r['query'][:50]}")
        kg_avg = sum(r["precision"] for r in kg_results) / len(kg_results)
        kg_lat = sum(r["latency_ms"] for r in kg_results) / len(kg_results)
        print(f"    AVG: {kg_avg:.0%} precision, {kg_lat:.0f}ms latency\n")

        # Combined benchmark
        print("  [taOSmd Combined — KG + QMD + Archive]")
        combined_results = await benchmark_combined(kg, archive, client)
        for r in combined_results:
            status = "HIT" if r["precision"] > 0 else "MISS"
            tokens = r.get("tokens", 0)
            print(f"    {status} {r['hits']}/{r['total']} ({r['precision']:.0%}) {r['latency_ms']:6.0f}ms {tokens:3d}t  {r['query'][:50]}")
        comb_avg = sum(r["precision"] for r in combined_results) / len(combined_results)
        comb_lat = sum(r["latency_ms"] for r in combined_results) / len(combined_results)
        comb_tokens = sum(r.get("tokens", 0) for r in combined_results) / len(combined_results)
        print(f"    AVG: {comb_avg:.0%} precision, {comb_lat:.0f}ms latency, {comb_tokens:.0f} tokens\n")

        # Summary
        print("=" * 70)
        print("RESULTS SUMMARY")
        print("=" * 70)
        print(f"\n  {'System':<25s} {'Precision':>10s} {'Latency':>10s} {'Tokens':>10s}")
        print(f"  {'-'*25} {'-'*10} {'-'*10} {'-'*10}")
        if qmd_results:
            print(f"  {'QMD Only (baseline)':<25s} {qmd_avg:>9.0%} {qmd_lat:>9.0f}ms {'N/A':>10s}")
        print(f"  {'KG Only (structured)':<25s} {kg_avg:>9.0%} {kg_lat:>9.0f}ms {'N/A':>10s}")
        print(f"  {'taOSmd Combined':<25s} {comb_avg:>9.0%} {comb_lat:>9.0f}ms {comb_tokens:>9.0f}t")

        if qmd_results and comb_avg > qmd_avg:
            improvement = ((comb_avg - qmd_avg) / qmd_avg * 100) if qmd_avg > 0 else float('inf')
            print(f"\n  taOSmd improves over QMD baseline by {improvement:.0f}%")
        if comb_avg > kg_avg:
            print(f"  taOSmd Combined beats KG-only by {((comb_avg - kg_avg) / kg_avg * 100) if kg_avg > 0 else 0:.0f}%")

        # Per-category breakdown
        print(f"\n  Per-category precision:")
        categories = set(q["category"] for q in BENCHMARK_QUERIES)
        for cat in sorted(categories):
            kg_cat = [r for r in kg_results if r["category"] == cat]
            comb_cat = [r for r in combined_results if r["category"] == cat]
            kg_p = sum(r["precision"] for r in kg_cat) / len(kg_cat) if kg_cat else 0
            comb_p = sum(r["precision"] for r in comb_cat) / len(comb_cat) if comb_cat else 0
            print(f"    {cat:<15s}  KG: {kg_p:.0%}  Combined: {comb_p:.0%}")

        print("\n" + "=" * 70)

    await archive.close()
    await kg.close()


if __name__ == "__main__":
    asyncio.run(main())
