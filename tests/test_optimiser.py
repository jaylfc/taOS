"""Unit tests for tinyagentos/cluster/optimiser.py.

Covers ClusterOptimiser.analyse() and the module-level _hw_summary() helper
with real inputs and exact return-value assertions.
"""
from unittest.mock import MagicMock

from tinyagentos.cluster.optimiser import ClusterOptimiser, PlacementSuggestion, _hw_summary


def _make_worker(
    name="worker-1",
    status="online",
    platform="linux",
    load=0.5,
    capabilities=None,
    hardware=None,
):
    w = MagicMock()
    w.name = name
    w.status = status
    w.platform = platform
    w.load = load
    w.capabilities = capabilities or []
    w.hardware = hardware or {}
    return w


def _make_cm(workers):
    cm = MagicMock()
    cm.get_workers.return_value = workers
    return cm


class TestHwSummary:
    def test_none_hardware_returns_unknown(self):
        assert _hw_summary(None) == "Unknown"

    def test_string_hardware_returns_unknown(self):
        assert _hw_summary("some-string") == "Unknown"

    def test_empty_dict_returns_cpu_only(self):
        assert _hw_summary({}) == "CPU only"

    def test_ram_only(self):
        assert _hw_summary({"ram_mb": 16384}) == "16GB RAM"

    def test_gpu_with_vram(self):
        hw = {"ram_mb": 32768, "gpu": {"type": "nvidia", "model": "RTX 4090", "vram_mb": 24576}}
        assert _hw_summary(hw) == "32GB RAM · RTX 4090 24GB"

    def test_gpu_type_only_no_model(self):
        hw = {"gpu": {"type": "amd", "vram_mb": 8192}}
        assert _hw_summary(hw) == "amd 8GB"

    def test_gpu_no_vram(self):
        hw = {"gpu": {"type": "intel"}}
        assert _hw_summary(hw) == "intel"

    def test_gpu_type_none_treated_as_no_gpu(self):
        hw = {"gpu": {"type": None, "vram_mb": 4096}}
        assert _hw_summary(hw) == "CPU only"

    def test_gpu_type_empty_string_treated_as_no_gpu(self):
        hw = {"gpu": {"type": "", "vram_mb": 4096}}
        assert _hw_summary(hw) == "CPU only"

    def test_npu_present(self):
        hw = {"npu": {"type": "rk3588", "tops": 6}}
        assert _hw_summary(hw) == "rk3588 6 TOPS"

    def test_npu_type_none_ignored(self):
        hw = {"npu": {"type": "none", "tops": 0}}
        assert _hw_summary(hw) == "CPU only"

    def test_full_hardware(self):
        hw = {
            "ram_mb": 65536,
            "gpu": {"type": "nvidia", "model": "A100", "vram_mb": 40960},
            "npu": {"type": "rk3588", "tops": 6},
        }
        assert _hw_summary(hw) == "64GB RAM · A100 40GB · rk3588 6 TOPS"

    def test_ram_zero_not_included(self):
        hw = {"ram_mb": 0}
        assert _hw_summary(hw) == "CPU only"

    def test_gpu_vram_zero_not_included_in_gpu_part(self):
        hw = {"gpu": {"type": "nvidia", "model": "GTX 1060", "vram_mb": 0}}
        assert _hw_summary(hw) == "GTX 1060"


class TestClusterOptimiserAnalyse:
    def test_no_workers(self):
        cm = _make_cm([])
        result = ClusterOptimiser(cm).analyse()
        assert result == {"suggestions": [], "summary": "No workers in cluster"}

    def test_single_online_worker(self):
        cm = _make_cm([_make_worker("w1")])
        result = ClusterOptimiser(cm).analyse()
        assert result["suggestions"] == []
        assert result["summary"] == "Need at least 2 online workers for optimisation"

    def test_one_online_one_offline(self):
        cm = _make_cm([
            _make_worker("w1", status="online"),
            _make_worker("w2", status="offline"),
        ])
        result = ClusterOptimiser(cm).analyse()
        assert result["suggestions"] == []
        assert result["summary"] == "Need at least 2 online workers for optimisation"

    def test_two_cpu_workers_no_suggestions(self):
        cm = _make_cm([
            _make_worker("w1", hardware={"ram_mb": 8192}),
            _make_worker("w2", hardware={"ram_mb": 4096}),
        ])
        result = ClusterOptimiser(cm).analyse()
        assert result["suggestions"] == []
        assert "2 workers online" in result["summary"]

    def test_gpu_worker_gets_large_model_suggestion(self):
        cm = _make_cm([
            _make_worker("gpu-box", hardware={
                "ram_mb": 32768,
                "gpu": {"type": "nvidia", "cuda": True, "vram_mb": 12288},
            }),
            _make_worker("cpu-box", hardware={"ram_mb": 8192}),
        ])
        result = ClusterOptimiser(cm).analyse()
        models = [s["model"] for s in result["suggestions"]]
        assert "qwen3-8b" in models

    def test_gpu_20gb_gets_14b_model(self):
        cm = _make_cm([
            _make_worker("gpu-box", hardware={
                "ram_mb": 65536,
                "gpu": {"type": "nvidia", "cuda": True, "vram_mb": 20480},
            }),
            _make_worker("cpu-box", hardware={"ram_mb": 8192}),
        ])
        result = ClusterOptimiser(cm).analyse()
        models = [s["model"] for s in result["suggestions"]]
        assert "qwen3-14b" in models

    def test_gpu_32gb_plus_gets_32b_model(self):
        cm = _make_cm([
            _make_worker("gpu-box", hardware={
                "ram_mb": 131072,
                "gpu": {"type": "nvidia", "cuda": True, "vram_mb": 49152},
            }),
            _make_worker("cpu-box", hardware={"ram_mb": 8192}),
        ])
        result = ClusterOptimiser(cm).analyse()
        models = [s["model"] for s in result["suggestions"]]
        assert "qwen3-32b" in models

    def test_gpu_below_8gb_no_large_model_suggestion(self):
        cm = _make_cm([
            _make_worker("gpu-box", hardware={
                "ram_mb": 16384,
                "gpu": {"type": "nvidia", "cuda": True, "vram_mb": 4096},
            }),
            _make_worker("cpu-box", hardware={"ram_mb": 8192}),
        ])
        result = ClusterOptimiser(cm).analyse()
        models = [s["model"] for s in result["suggestions"]]
        assert "qwen3-8b" not in models
        assert "qwen3-14b" not in models
        assert "qwen3-32b" not in models

    def test_embedding_suggested_on_weakest_cpu(self):
        cm = _make_cm([
            _make_worker("gpu-box", hardware={
                "ram_mb": 32768,
                "gpu": {"type": "nvidia", "cuda": True, "vram_mb": 16384},
            }),
            _make_worker("weak-cpu", hardware={"ram_mb": 2048}),
            _make_worker("strong-cpu", hardware={"ram_mb": 16384}),
        ])
        result = ClusterOptimiser(cm).analyse()
        embedding = [s for s in result["suggestions"] if s["model"] == "embedding-model"]
        assert len(embedding) == 1
        assert embedding[0]["suggested"] == "weak-cpu"

    def test_image_gen_suggested_on_gpu_with_6gb_plus(self):
        cm = _make_cm([
            _make_worker("gpu-box", hardware={
                "ram_mb": 32768,
                "gpu": {"type": "nvidia", "cuda": True, "vram_mb": 8192},
            }),
            _make_worker("cpu-box", hardware={"ram_mb": 8192}),
        ])
        result = ClusterOptimiser(cm).analyse()
        models = [s["model"] for s in result["suggestions"]]
        assert "image-generation" in models

    def test_no_image_gen_on_gpu_below_6gb(self):
        cm = _make_cm([
            _make_worker("gpu-box", hardware={
                "ram_mb": 16384,
                "gpu": {"type": "nvidia", "cuda": True, "vram_mb": 4096},
            }),
            _make_worker("cpu-box", hardware={"ram_mb": 8192}),
        ])
        result = ClusterOptimiser(cm).analyse()
        models = [s["model"] for s in result["suggestions"]]
        assert "image-generation" not in models

    def test_npu_reranking_suggestion(self):
        cm = _make_cm([
            _make_worker("npu-box", hardware={
                "ram_mb": 8192,
                "npu": {"type": "rk3588", "tops": 6},
            }),
            _make_worker("cpu-box", hardware={"ram_mb": 8192}),
        ])
        result = ClusterOptimiser(cm).analyse()
        rerank = [s for s in result["suggestions"] if s["model"] == "reranking-model"]
        assert len(rerank) == 1
        assert rerank[0]["suggested"] == "npu-box"

    def test_apple_silicon_classified_as_gpu(self):
        # Apple silicon can be a GPU worker, but without vram_mb it won't
        # trigger image-generation (needs >= 6144). It does however still
        # give an embedding suggestion since cpu-box is present.
        cm = _make_cm([
            _make_worker("mac-studio", hardware={
                "ram_mb": 65536,
                "gpu": {"type": "apple"},
            }),
            _make_worker("cpu-box", hardware={"ram_mb": 8192}),
        ])
        result = ClusterOptimiser(cm).analyse()
        models = [s["model"] for s in result["suggestions"]]
        assert "embedding-model" in models

    def test_rocm_gpu_classified_as_gpu(self):
        cm = _make_cm([
            _make_worker("amd-box", hardware={
                "ram_mb": 32768,
                "gpu": {"type": "amd", "rocm": True, "vram_mb": 16384},
            }),
            _make_worker("cpu-box", hardware={"ram_mb": 8192}),
        ])
        result = ClusterOptimiser(cm).analyse()
        models = [s["model"] for s in result["suggestions"]]
        assert "image-generation" in models

    def test_workers_list_includes_all_workers(self):
        workers = [
            _make_worker("w1", status="online"),
            _make_worker("w2", status="online"),
            _make_worker("w3", status="offline"),
        ]
        cm = _make_cm(workers)
        result = ClusterOptimiser(cm).analyse()
        assert len(result["workers"]) == 3
        names = [w["name"] for w in result["workers"]]
        assert names == ["w1", "w2", "w3"]

    def test_worker_hardware_summary_in_output(self):
        workers = [
            _make_worker("w1", status="online", hardware={"ram_mb": 16384}),
            _make_worker("w2", status="online", hardware={"ram_mb": 8192}),
        ]
        cm = _make_cm(workers)
        result = ClusterOptimiser(cm).analyse()
        w1_data = result["workers"][0]
        assert w1_data["hardware_summary"] == "16GB RAM"

    def test_worker_status_preserved(self):
        workers = [
            _make_worker("w1", status="online"),
            _make_worker("w2", status="online", hardware={"ram_mb": 8192}),
        ]
        cm = _make_cm(workers)
        result = ClusterOptimiser(cm).analyse()
        assert result["workers"][0]["status"] == "online"
        assert result["workers"][1]["status"] == "online"

    def test_suggestion_fields_present(self):
        cm = _make_cm([
            _make_worker("gpu-box", hardware={
                "ram_mb": 32768,
                "gpu": {"type": "nvidia", "cuda": True, "vram_mb": 16384},
            }),
            _make_worker("cpu-box", hardware={"ram_mb": 8192}),
        ])
        result = ClusterOptimiser(cm).analyse()
        for s in result["suggestions"]:
            assert "model" in s
            assert "current" in s
            assert "suggested" in s
            assert "reason" in s
            assert "improvement" in s

    def test_suggestion_current_worker_is_none(self):
        cm = _make_cm([
            _make_worker("gpu-box", hardware={
                "ram_mb": 32768,
                "gpu": {"type": "nvidia", "cuda": True, "vram_mb": 16384},
            }),
            _make_worker("cpu-box", hardware={"ram_mb": 8192}),
        ])
        result = ClusterOptimiser(cm).analyse()
        for s in result["suggestions"]:
            assert s["current"] is None

    def test_summary_count_matches_suggestions(self):
        cm = _make_cm([
            _make_worker("gpu-box", hardware={
                "ram_mb": 32768,
                "gpu": {"type": "nvidia", "cuda": True, "vram_mb": 16384},
            }),
            _make_worker("cpu-box", hardware={"ram_mb": 8192}),
        ])
        result = ClusterOptimiser(cm).analyse()
        n = len(result["suggestions"])
        assert result["summary"] == f"2 workers online, {n} optimisation suggestions"

    def test_multiple_gpu_workers_image_gen_only_once(self):
        cm = _make_cm([
            _make_worker("gpu1", hardware={
                "ram_mb": 32768,
                "gpu": {"type": "nvidia", "cuda": True, "vram_mb": 16384},
            }),
            _make_worker("gpu2", hardware={
                "ram_mb": 32768,
                "gpu": {"type": "nvidia", "cuda": True, "vram_mb": 24576},
            }),
            _make_worker("cpu-box", hardware={"ram_mb": 8192}),
        ])
        result = ClusterOptimiser(cm).analyse()
        img_gen = [s for s in result["suggestions"] if s["model"] == "image-generation"]
        assert len(img_gen) == 1

    def test_multiple_gpu_workers_best_gpu_gets_large_model(self):
        cm = _make_cm([
            _make_worker("gpu-small", hardware={
                "ram_mb": 16384,
                "gpu": {"type": "nvidia", "cuda": True, "vram_mb": 8192},
            }),
            _make_worker("gpu-big", hardware={
                "ram_mb": 65536,
                "gpu": {"type": "nvidia", "cuda": True, "vram_mb": 24576},
            }),
            _make_worker("cpu-box", hardware={"ram_mb": 8192}),
        ])
        result = ClusterOptimiser(cm).analyse()
        chat_models = [s for s in result["suggestions"] if s["model"].startswith("qwen3")]
        assert len(chat_models) == 1
        assert chat_models[0]["suggested"] == "gpu-big"

    def test_worker_with_non_dict_hardware(self):
        w = _make_worker("bad-hw", status="online")
        w.hardware = "not-a-dict"
        cm = _make_cm([w, _make_worker("w2")])
        result = ClusterOptimiser(cm).analyse()
        assert "2 workers online" in result["summary"]

    def test_worker_with_none_hardware(self):
        w = _make_worker("none-hw", status="online")
        w.hardware = None
        cm = _make_cm([w, _make_worker("w2")])
        result = ClusterOptimiser(cm).analyse()
        assert "2 workers online" in result["summary"]

    def test_no_embedding_suggestion_without_cpu_workers(self):
        cm = _make_cm([
            _make_worker("gpu1", hardware={
                "ram_mb": 32768,
                "gpu": {"type": "nvidia", "cuda": True, "vram_mb": 16384},
            }),
            _make_worker("gpu2", hardware={
                "ram_mb": 32768,
                "gpu": {"type": "amd", "rocm": True, "vram_mb": 16384},
            }),
        ])
        result = ClusterOptimiser(cm).analyse()
        models = [s["model"] for s in result["suggestions"]]
        assert "embedding-model" not in models

    def test_no_embedding_suggestion_without_accelerator(self):
        cm = _make_cm([
            _make_worker("cpu1", hardware={"ram_mb": 8192}),
            _make_worker("cpu2", hardware={"ram_mb": 4096}),
        ])
        result = ClusterOptimiser(cm).analyse()
        models = [s["model"] for s in result["suggestions"]]
        assert "embedding-model" not in models

    def test_npu_and_gpu_both_present(self):
        cm = _make_cm([
            _make_worker("npu-box", hardware={
                "ram_mb": 8192,
                "npu": {"type": "rk3588", "tops": 6},
            }),
            _make_worker("gpu-box", hardware={
                "ram_mb": 32768,
                "gpu": {"type": "nvidia", "cuda": True, "vram_mb": 16384},
            }),
            _make_worker("cpu-box", hardware={"ram_mb": 8192}),
        ])
        result = ClusterOptimiser(cm).analyse()
        models = [s["model"] for s in result["suggestions"]]
        assert "reranking-model" in models
        assert "qwen3-14b" in models
        assert "image-generation" in models
        assert "embedding-model" in models

    def test_placement_suggestion_dataclass_defaults(self):
        ps = PlacementSuggestion(
            model_or_service="test",
            current_worker=None,
            suggested_worker="w1",
            reason="test reason",
            improvement="test improvement",
        )
        assert ps.model_or_service == "test"
        assert ps.current_worker is None
        assert ps.suggested_worker == "w1"
        assert ps.reason == "test reason"
        assert ps.improvement == "test improvement"
