"""Test that the license class allowlist in tests matches the code."""
import pytest
from tinyagentos.routes.store_install import RESTRICTIVE_LICENSE_CLASSES


def test_license_class_test_allowlist_matches_code():
    """The test parametrization must exactly match the restrictive_classes set in code.
    
    This is an allowlist test - if a new license class is added to the code,
    it must also be added to the test parametrization, and vice versa.
    """
    # These are the license classes tested in TestRestrictiveLicenseGate
    tested_classes = {
        "non-commercial",
        "revenue-capped",
        "research-only",
        "territory-limited",
        "proprietary",
        "gated",
        "unlicensed",
    }
    
    # The code's restrictive_classes should match the tested classes exactly
    assert RESTRICTIVE_LICENSE_CLASSES == tested_classes, (
        f"License class allowlist mismatch!\n"
        f"Code has: {sorted(RESTRICTIVE_LICENSE_CLASSES)}\n"
        f"Tests cover: {sorted(tested_classes)}\n"
        f"Missing from tests: {sorted(RESTRICTIVE_LICENSE_CLASSES - tested_classes)}\n"
        f"Extra in tests: {sorted(tested_classes - RESTRICTIVE_LICENSE_CLASSES)}"
    )
