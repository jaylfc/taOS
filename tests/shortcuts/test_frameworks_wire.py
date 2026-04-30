"""
Verify that frameworks.py accepts a `shortcuts` field and validate_framework_manifest
checks shortcut entries.
"""
import copy
import pytest

from tinyagentos.frameworks import FRAMEWORKS, validate_framework_manifest


def _first_framework_with_shortcuts():
    for name, fw in FRAMEWORKS.items():
        if fw.get("shortcuts"):
            return name, fw
    return None, None


def test_validate_accepts_shortcuts_field():
    """validate_framework_manifest must not raise when shortcuts are well-formed."""
    name, fw = _first_framework_with_shortcuts()
    if fw is None:
        pytest.skip("No framework has shortcuts yet — will pass after Task 11 adds them")
    validate_framework_manifest(name, fw)  # must not raise


def test_validate_rejects_malformed_shortcut():
    """validate_framework_manifest raises ValueError for a bad shortcut entry."""
    name, fw = _first_framework_with_shortcuts()
    if fw is None:
        pytest.skip("No framework has shortcuts yet")
    bad = copy.deepcopy(fw)
    bad["shortcuts"][0].pop("kind")
    with pytest.raises(ValueError, match="missing 'kind'"):
        validate_framework_manifest(name, bad)


def test_shortcuts_field_is_optional():
    """A framework without shortcuts is still valid."""
    validate_framework_manifest(
        "test-no-shortcuts",
        {
            "display_name": "Test Framework",
            "shortcuts": [],
        },
    )


def test_shortcuts_field_absent_ok():
    """A framework dict without the shortcuts key is still valid."""
    validate_framework_manifest(
        "test-absent",
        {"display_name": "Test Framework"},
    )
