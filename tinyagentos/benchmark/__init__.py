"""TinyAgentOS worker benchmark system.

Produces per-capability scores for every worker so the cluster scheduler
can make informed cost-model decisions about where to dispatch work.

Policy:
- Runs exactly once automatically, when a worker first joins the cluster.
- After that, runs are manual only — trigger via POST /api/workers/{id}/benchmark
  with optional custom model/suite parameters.
- Results stored in data/benchmarks.db, keyed on
  (worker_id, capability, model, metric, measured_at).

See docs/design/resource-scheduler.md §Backend-driven discovery and the
worker benchmark system Phase 1 issue on the project board.
"""
from tinyagentos.benchmark.store import BenchmarkStore
from tinyagentos.benchmark.suite import BenchmarkSuite, SuiteResult, SuiteTask
from tinyagentos.benchmark.runner import BenchmarkRunner

__all__ = [
    "BenchmarkRunner",
    "BenchmarkStore",
    "BenchmarkSuite",
    "SuiteResult",
    "SuiteTask",
]
