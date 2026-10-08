from __future__ import annotations

import ast
import math
import os
import subprocess
import sys

from tinyagentos.cluster.placement import eligible, rank, score


class TestScore:
    def test_positive_value(self):
        s = score("k", "node")
        assert s > 0

    def test_weight_scales(self):
        assert score("k", "node", 2.0) == 2.0 * score("k", "node", 1.0)

    def test_no_builtin_hash(self):
        assert "hash" not in score.__code__.co_names

    def test_score_finite_at_hash_extremes(self):
        import hashlib
        from unittest.mock import patch
        # Test with all 1s (hash near 2**64)
        with patch("tinyagentos.cluster.placement.hashlib.blake2b") as mock:
            mock.return_value.digest.return_value = b"\xff" * 8
            s = score("k", "n")
            assert math.isfinite(s)
            assert s > 0
        # Test with all 0s
        with patch("tinyagentos.cluster.placement.hashlib.blake2b") as mock:
            mock.return_value.digest.return_value = b"\x00" * 8
            s = score("k", "n")
            assert math.isfinite(s)
            assert s > 0


class TestRank:
    def test_golden_vector_list(self):
        assert rank("qwen3-8b", ["alpha", "bravo", "charlie"]) == [
            "bravo",
            "charlie",
            "alpha",
        ]

    def test_golden_vector_list_four(self):
        assert rank("agent-42", ["alpha", "bravo", "charlie", "delta"]) == [
            "delta",
            "charlie",
            "bravo",
            "alpha",
        ]

    def test_golden_vector_dict(self):
        assert rank("qwen3-8b", {"alpha": 4.0, "bravo": 1.0, "charlie": 1.0}) == [
            "alpha",
            "bravo",
            "charlie",
        ]

    def test_empty_list(self):
        assert rank("k", []) == []

    def test_empty_dict(self):
        assert rank("k", {}) == []

    def test_weight_zero_dropped(self):
        assert rank("k", {"a": 0.0, "b": 1.0}) == ["b"]

    def test_negative_weight_dropped(self):
        assert rank("k", {"a": -1.0, "b": 1.0}) == ["b"]

    def test_determinism_across_processes(self):
        in_process = rank("det-key", ["alpha", "bravo", "charlie"])
        for seed in ("1", "2"):
            script = (
                "from tinyagentos.cluster.placement import rank;"
                " print(repr(rank('det-key', ['alpha', 'bravo', 'charlie'])))"
            )
            env = {**os.environ, "PYTHONHASHSEED": seed}
            result = subprocess.run(
                [sys.executable, "-c", script],
                capture_output=True,
                text=True,
                env=env,
            )
            assert result.returncode == 0, result.stderr
            assert ast.literal_eval(result.stdout.strip()) == in_process

    def test_minimal_disruption_add_node(self):
        nodes = ["n1", "n2", "n3", "n4", "n5"]
        keys = [f"k{i}" for i in range(1000)]
        original = {k: rank(k, nodes)[0] for k in keys}

        extended = nodes + ["n6"]
        updated = {k: rank(k, extended)[0] for k in keys}

        moved = [k for k in keys if original[k] != updated[k]]
        assert 100 <= len(moved) <= 250
        for k in moved:
            assert updated[k] == "n6"

    def test_minimal_disruption_remove_node(self):
        nodes = ["n1", "n2", "n3", "n4", "n5"]
        keys = [f"k{i}" for i in range(1000)]
        original = {k: rank(k, nodes)[0] for k in keys}

        reduced = ["n1", "n2", "n4", "n5"]
        updated = {k: rank(k, reduced)[0] for k in keys}

        for k in keys:
            if original[k] != "n3":
                assert updated[k] == original[k]

    def test_weight_bias(self):
        nodes = {"a": 2.0, "b": 1.0}
        keys = [f"k{i}" for i in range(4000)]
        a_count = sum(1 for k in keys if rank(k, nodes)[0] == "a")
        assert 0.60 * 4000 <= a_count <= 0.73 * 4000


class TestEligible:
    def test_object_online(self):
        class W:
            status = "online"
            kind = "worker"

        assert eligible(W()) is True

    def test_object_update_available(self):
        class W:
            status = "update-available"
            kind = "worker"

        assert eligible(W()) is True

    def test_object_offline(self):
        class W:
            status = "offline"
            kind = "worker"

        assert eligible(W()) is False

    def test_object_draining(self):
        class W:
            status = "draining"
            kind = "worker"

        assert eligible(W()) is False

    def test_object_device_kind(self):
        class W:
            status = "online"
            kind = "device"

        assert eligible(W()) is False

    def test_dict_online(self):
        assert eligible({"status": "online", "kind": "worker"}) is True

    def test_dict_update_available(self):
        assert eligible({"status": "update-available", "kind": "worker"}) is True

    def test_dict_offline(self):
        assert eligible({"status": "offline", "kind": "worker"}) is False

    def test_dict_draining(self):
        assert eligible({"status": "draining", "kind": "worker"}) is False

    def test_dict_device_kind(self):
        assert eligible({"status": "online", "kind": "device"}) is False
