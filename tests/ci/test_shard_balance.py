"""Unit tests for the shard manifest guard (scripts/check_shard_manifest.py).

RED-FIRST: these tests pin the public API of the guard script, which does not
exist on origin/dev yet, so the collection run fails with ModuleNotFoundError
before the script is implemented. The failing run is recorded under the `Red`
fence; the passing run follows the `Green` implementation.
"""
import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT / "scripts"))

import check_shard_manifest as csm


def _write_manifest(tmp_path, manifest):
    path = tmp_path / "durations.json"
    path.write_text(json.dumps(manifest))
    return path


def _write_collected(tmp_path, ids):
    path = tmp_path / "collected.txt"
    path.write_text("\n".join(ids) + ("\n" if ids else ""))
    return path


def test_empty_manifest_fails():
    problems = csm.check_keys({}, {"a::b"})
    assert any("empty" in p for p in problems)


def test_synthetic_ids_fail_subset():
    manifest = {
        f"tests/test_synthetic_{i}.py::test_{i}": 1.0 for i in range(20)
    }
    collected = {
        f"tests/test_other_{i}.py::test_{i}" for i in range(100)
    }
    problems = csm.check_keys(manifest, collected)
    joined = " | ".join(problems)
    assert "match no collected test" in joined
    assert "coverage" in joined


def test_coverage_below_95_fails():
    collected = {
        f"tests/test_covered_{i}.py::test_{i}" for i in range(100)
    }
    manifest = {k: 1.0 for k in list(collected)[:90]}
    problems = csm.check_keys(manifest, collected)
    assert any("coverage" in p for p in problems)


def test_real_shape_passes():
    manifest = {
        f"tests/test_real_{i}.py::test_{i}": float(i + 1) for i in range(100)
    }
    collected = set(manifest)
    assert csm.check_keys(manifest, collected) == []


def test_least_duration_split_balances(tmp_path):
    manifest = {
        f"tests/test_balanced_{i}.py::test_{i}": float(i + 1) for i in range(40)
    }
    collected = list(manifest)
    totals = csm.split_shards(manifest, collected, splits=4, algorithm_name="least_duration")
    assert csm.balance_ratio(totals) <= 1.2

    unbalanced = {f"tests/test_unb_{i}.py::test_{i}": 1.0 for i in range(10)}
    unbalanced["tests/test_unb_big.py::test_big"] = 100.0
    collected_unb = list(unbalanced)
    totals = csm.split_shards(unbalanced, collected_unb, splits=4, algorithm_name="least_duration")
    assert csm.balance_ratio(totals) > 2.0

    manifest_path = _write_manifest(tmp_path, unbalanced)
    collected_path = _write_collected(tmp_path, collected_unb)
    rc = csm.main(
        [
            "--manifest",
            str(manifest_path),
            "--collected-file",
            str(collected_path),
            "--splitting-algorithm",
            "least_duration",
        ]
    )
    assert rc == 1


def test_cli_exit_codes(tmp_path, capsys):
    empty = _write_manifest(tmp_path, {})
    collected = _write_collected(tmp_path, [])
    rc = csm.main(
        ["--manifest", str(empty), "--collected-file", str(collected)]
    )
    out = capsys.readouterr().out
    assert rc == 1
    assert "FAIL:" in out

    good_manifest = {
        f"tests/test_good_{i}.py::test_{i}": float(i + 1) for i in range(40)
    }
    good = _write_manifest(tmp_path, good_manifest)
    collected = _write_collected(tmp_path, list(good_manifest))
    rc = csm.main(
        ["--manifest", str(good), "--collected-file", str(collected)]
    )
    out = capsys.readouterr().out
    assert rc == 0
    assert out.startswith("OK:")


def test_duration_based_chunks_empty_shard_fails(tmp_path):
    """duration_based_chunks can leave a shard empty; validator must catch it."""
    # Collection order: a..g with durations 1,1,1,1,1,1,3
    manifest = {
        "tests/test_a.py::test_a": 1.0,
        "tests/test_b.py::test_b": 1.0,
        "tests/test_c.py::test_c": 1.0,
        "tests/test_d.py::test_d": 1.0,
        "tests/test_e.py::test_e": 1.0,
        "tests/test_f.py::test_f": 1.0,
        "tests/test_g.py::test_g": 3.0,
    }
    collected = list(manifest)
    manifest_path = _write_manifest(tmp_path, manifest)
    collected_path = _write_collected(tmp_path, collected)
    # Default algorithm is duration_based_chunks, max-ratio 2.0
    rc = csm.main(
        [
            "--manifest",
            str(manifest_path),
            "--collected-file",
            str(collected_path),
            "--splits",
            "4",
            "--max-ratio",
            "2.0",
        ]
    )
    assert rc == 1
