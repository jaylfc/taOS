# taOSmd Overnight Results (2026-04-13)

## Final Publishable Scores

### LongMemEval-S (500 questions, 50+ sessions/question)
- **Recall@5: 97.2%** (486/500) — beats MemPalace 96.6%
- knowledge-update: 100.0% (78/78)
- single-session-user: 100.0% (70/70)
- multi-session: 98.5% (131/133)
- temporal-reasoning: 95.5% (127/133)
- single-session-assistant: 94.6% (53/56)
- single-session-preference: 90.0% (27/30)

### Granularity Comparison (100 questions each)
- User-turns only, hybrid: **99.0%** (best)
- Full session, hybrid: 97.0%
- Turn-level, hybrid: 97.0%
- Turn-level, raw semantic: 93.0%
- User-turns only, raw semantic: 92.0%
- Full session, raw semantic: 83.0%

### Real-World Agent Benchmarks
- Business Agent: **100%** (10/10)
- Personal Assistant: **100%** (10/10)
- Developer Agent: **100%** (10/10)

## Overnight Improvements
- Importance scoring via access tracking (hit_rate = accessed/appeared)
- Preference extractor with synthetic preference documents (9 tests)
- RKNN NPU conversion of MiniLM (44MB, but ONNX CPU faster at 0.3ms)
- Numpy-vectorised cosine similarity (4x speedup)
- Community feedback from m13v integrated

## What Didn't Improve Scores
- Synthetic preference docs: +0% on preference category (3 misses are genuinely hard)
- RKNN NPU for MiniLM: slower than ONNX CPU (16.7ms vs 0.3ms)

## Next Steps
- Temporal timestamp boosting for temporal-reasoning category
- Custom benchmark datasets for publishing
- Test setup script on clean Fedora LXC
- Gemma 4 conversion (blocked on RKLLM support)
