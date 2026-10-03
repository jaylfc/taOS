import importlib.util
from pathlib import Path

_spec = importlib.util.spec_from_file_location(
    "newest_release_tag", Path(__file__).resolve().parents[1] / "scripts" / "newest_release_tag.py"
)
newest_release_tag = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(newest_release_tag)
newest = newest_release_tag.newest


def test_numeric_not_lexical_beta_order():
    assert newest(["v1.0.0-beta.9", "v1.0.0-beta.10"]) == "v1.0.0-beta.10"


def test_invalid_historic_tag_is_skipped_not_fatal():
    # v1.0.0-beta.4.1 exists on the real repo and is not PEP 440.
    assert newest(["v1.0.0-beta.4.1", "v1.0.0-beta.55", "v1.0.0-beta.54"]) == "v1.0.0-beta.55"


def test_final_outranks_its_betas():
    assert newest(["v1.0.0-beta.99", "v1.0.0"]) == "v1.0.0"


def test_older_tag_is_not_newest():
    assert newest(["v1.0.0-beta.56", "v1.0.0-beta.55\n"]) != "v1.0.0-beta.55"


def test_empty_input():
    assert newest(["", "\n"]) == ""
