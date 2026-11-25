"""Tests for taOSnet redistribution-eligibility classification."""
from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from tinyagentos.taosnet.license_eligibility import (
    classify_manifest,
    license_allows_redistribution,
    normalize_license,
)

# Real licence strings from app-catalog/models that DO permit redistribution.
REDISTRIBUTABLE = [
    "Apache-2.0",
    "MIT",
    "BSD-3-Clause",
    "CC-BY-4.0",
    "OpenRAIL",
    "OpenRAIL++",
    "OpenRAIL-M",
    "openrail++",
    "OpenRAIL++ (commercial use allowed)",  # trailing note stripped
    "CreativeML OpenRAIL-M",
    "Gemma Terms of Use",
    '"Gemma Terms of Use"',  # quoted in YAML source
    "Gemma",
    "Llama 3.1 Community License",
    "Llama 3.3 Community License",
]

# Real licence strings that must NOT be redistributed: non-commercial, research,
# gated, dual-with-restricted-part, or simply unknown-so-conservative.
NOT_REDISTRIBUTABLE = [
    "Qwen Research License",
    "Qwen License (commercial requires agreement)",
    "Tongyi Qianwen License",
    "stable-cascade-nc-community (non-commercial)",
    "sai-nc-community (non-commercial)",
    "Stability AI Community License",
    "Playground v2.5 Community License",
    "NVIDIA Open Model License",
    "DeepSeek License",
    "S-Lab License 1.0",
    "CC-BY-NC-4.0",
    "CC-BY-NC 4.0 (non-commercial)",
    "CC BY-NC-SA 4.0",
    "bria-rmbg-1.4 (non-commercial)",
    "flux-1-dev-non-commercial-license",
    "Apache-2.0 / MiniCPM Model License",  # dual: weights are MiniCPM-licensed
]


@pytest.mark.parametrize("lic", REDISTRIBUTABLE)
def test_redistributable_licenses(lic):
    assert license_allows_redistribution(lic) is True


@pytest.mark.parametrize("lic", NOT_REDISTRIBUTABLE)
def test_restricted_licenses(lic):
    assert license_allows_redistribution(lic) is False


@pytest.mark.parametrize("lic", [None, "", "   ", "Totally Made Up License 2.0"])
def test_unknown_or_empty_defaults_to_false(lic):
    assert license_allows_redistribution(lic) is False


def test_case_and_whitespace_insensitive():
    assert license_allows_redistribution("apache-2.0") is True
    assert license_allows_redistribution("  APACHE-2.0  ") is True
    assert license_allows_redistribution("MIT") is True


def test_cc_by_is_allowed_but_cc_by_nc_is_not():
    # The NC marker must not also trip on the permissive CC-BY-4.0.
    assert license_allows_redistribution("CC-BY-4.0") is True
    assert license_allows_redistribution("CC-BY-NC-4.0") is False


def test_normalize_strips_quotes_and_trailing_note():
    assert normalize_license('"Gemma Terms of Use"') == "gemma terms of use"
    assert normalize_license("OpenRAIL++ (commercial use allowed)") == "openrail++"


def test_classify_manifest_reads_license_field():
    assert classify_manifest({"license": "MIT"}) is True
    assert classify_manifest({"license": "CC-BY-NC-4.0"}) is False
    assert classify_manifest({}) is False


def test_every_catalog_manifest_classifies_without_error():
    """Every model manifest must classify to a bool. If this fails on a NEW
    licence string, add it to the allow-list or confirm it should stay False."""
    catalog = Path(__file__).resolve().parents[2] / "app-catalog" / "models"
    manifests = list(catalog.glob("*/manifest.yaml")) + list(catalog.glob("*/manifest.yml"))
    assert manifests, "no model manifests found; wrong path?"
    for path in manifests:
        data = yaml.safe_load(path.read_text())
        result = classify_manifest(data)
        assert isinstance(result, bool), f"{path.name}: {data.get('license')!r} -> {result!r}"


# SPDX / common licence classification table (fix-forward #4516).
# GPL-2.0-only and GPL-2.0 without "or later" -> BLOCK (False).
# GPL-2.0-or-later, GPL-3.0, AGPL-3.0, LGPL, MPL, permissive -> ALLOW (True).
# SSPL, ELv2, BSL, non-commercial, unlicensed -> BLOCK (False).


@pytest.mark.parametrize(
    "lic,expected",
    [
        # GPL-2.0 family: "only" and bare GPL-2.0 block; "or-later" allows.
        ("GPL-2.0-only", False),
        ("GPL-2.0", False),  # bare GPL-2.0 treated as GPL-2.0-only
        ("GPLv2", False),  # common alias for GPL-2.0-only
        ("GPL-2.0-or-later", True),
        ("GPL-3.0-only", True),  # GPL-3.0 family allows
        ("GPL-3.0", True),
        ("GPL-3.0-or-later", True),
    ],
)
def test_spdx_gpl_classification(lic, expected):
    assert license_allows_redistribution(lic) is expected


@pytest.mark.parametrize(
    "lic,expected",
    [
        # LGPL, MPL, AGPL families allow.
        ("LGPL-2.0", True),
        ("LGPL-2.1", True),
        ("LGPL-3.0", True),
        ("MPL-2.0", True),
        ("MPL-1.1", True),
        ("AGPL-3.0", True),
        ("AGPL-3.0-only", True),
        ("AGPL-3.0-or-later", True),
    ],
)
def test_spdx_lgpl_mpl_agpl_classification(lic, expected):
    assert license_allows_redistribution(lic) is expected


@pytest.mark.parametrize(
    "lic,expected",
    [
        # Elastic v2, BSL, SSPL block.
        ("Elastic-2.0", False),
        ("BSL-1.0", False),
        ("BSL-1.1", False),
        ("SSPL-1.0", False),
        ("Server Side Public License", False),
        ("Server Side Public License v1", False),
    ],
)
def test_spdx_elastic_bsl_sspl_classification(lic, expected):
    assert license_allows_redistribution(lic) is expected


@pytest.mark.parametrize(
    "lic,expected",
    [
        # Non-commercial and unlicensed block.
        ("CC-BY-NC-4.0", False),
        ("CC-BY-NC-SA-4.0", False),
        ("Unlicensed", False),
        ("unlicense", False),
    ],
)
def test_spdx_noncommercial_unlicensed_classification(lic, expected):
    assert license_allows_redistribution(lic) is expected
