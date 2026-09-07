"""RED test for R2-31: importing each benchmarks module fails.

This test proves that every script in benchmarks/ imports modules
that do not exist in the tree (tinyagentos.* modules), as documented
in the library-replacement audit pass 2 (R2-31).
"""

from __future__ import annotations

import importlib
import sys

import pytest


BENCHMARK_MODULES = [
    "benchmarks.longmemeval_granularity",
    "benchmarks.longmemeval_recall",
    "benchmarks.longmemeval_runner",
    "benchmarks.model_shootout",
    "benchmarks.realworld_agent_benchmark",
    "benchmarks.taosmd_benchmark",
    "benchmarks.taosmd_iteration5",
    "benchmarks.taosmd_vs_qmd",
    "benchmarks.test_llm_extraction",
    "benchmarks.test_pi_llm_extraction",
]


class TestR231BenchmarksImportFailure:
    """RED test: importing any benchmarks module should fail."""

    @pytest.mark.parametrize("module_name", BENCHMARK_MODULES)
    def test_import_fails(self, module_name: str) -> None:
        with pytest.raises(ModuleNotFoundError):
            importlib.import_module(module_name)