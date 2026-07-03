"""The provenance -> capability ceiling model (provenance-keyed sandbox tiers).

Every userspace app is classified into one of four provenance tiers
(first-party/ai-generated/user-uploaded/unknown); the tier is a CEILING --
the capabilities the app holds automatically, before any user consent.
"""
import pytest

from tinyagentos.userspace.capabilities import (
    DEFAULT_PROVENANCE,
    FREE_CAPS,
    GATED_CAPS,
    KNOWN_CAPS,
    PROVENANCE_CEILINGS,
    PROVENANCE_DESCRIPTIONS,
    PROVENANCE_TIERS,
    capability_allowed,
    capability_ceiling,
    default_provenance_for_trust,
    is_known_provenance,
)


def test_provenance_tiers_are_exactly_four():
    assert PROVENANCE_TIERS == ("first-party", "ai-generated", "user-uploaded", "unknown")


def test_every_tier_has_a_ceiling_and_description():
    for tier in PROVENANCE_TIERS:
        assert tier in PROVENANCE_CEILINGS
        assert PROVENANCE_DESCRIPTIONS.get(tier), f"no description for {tier}"


def test_default_provenance_is_unknown_the_most_restricted():
    assert DEFAULT_PROVENANCE == "unknown"
    assert capability_ceiling(DEFAULT_PROVENANCE) == frozenset()


def test_is_known_provenance():
    for tier in PROVENANCE_TIERS:
        assert is_known_provenance(tier) is True
    assert is_known_provenance("community") is False
    assert is_known_provenance("") is False
    assert is_known_provenance(None) is False


def test_first_party_ceiling_is_every_known_capability():
    assert capability_ceiling("first-party") == KNOWN_CAPS


def test_ai_generated_and_user_uploaded_have_no_network_or_storage_by_default():
    for tier in ("ai-generated", "user-uploaded"):
        ceiling = capability_ceiling(tier)
        # No storage, no network, no cross-app data.
        assert "app.kv" not in ceiling
        assert "app.table" not in ceiling
        assert "app.files" not in ceiling
        assert "app.net" not in ceiling
        assert "app.agent" not in ceiling
        assert "app.llm" not in ceiling
        assert "app.memory" not in ceiling
        # Only the inert, app-local UI capabilities are free.
        assert ceiling == frozenset({"app.notify", "app.window"})


def test_unknown_is_more_restricted_than_ai_generated_or_user_uploaded():
    unknown_ceiling = capability_ceiling("unknown")
    assert unknown_ceiling == frozenset()
    for tier in ("ai-generated", "user-uploaded"):
        assert unknown_ceiling < capability_ceiling(tier)


def test_unrecognised_provenance_falls_back_to_default():
    assert capability_ceiling("bogus-tier") == capability_ceiling(DEFAULT_PROVENANCE)
    assert capability_ceiling(None) == capability_ceiling(DEFAULT_PROVENANCE)


@pytest.mark.parametrize("cap", sorted(GATED_CAPS))
def test_gated_caps_never_free_below_first_party(cap):
    for tier in ("ai-generated", "user-uploaded", "unknown"):
        assert not capability_allowed(tier, cap, granted=[])


@pytest.mark.parametrize("cap", sorted(FREE_CAPS))
def test_first_party_ceiling_covers_every_free_cap(cap):
    assert capability_allowed("first-party", cap, granted=[])


def test_capability_allowed_within_ceiling_needs_no_grant():
    assert capability_allowed("ai-generated", "app.notify", granted=[]) is True
    assert capability_allowed("ai-generated", "app.window.open", granted=[]) is True


def test_capability_allowed_outside_ceiling_denied_without_grant():
    assert capability_allowed("ai-generated", "app.kv", granted=[]) is False
    assert capability_allowed("user-uploaded", "app.net", granted=[]) is False
    assert capability_allowed("unknown", "app.notify", granted=[]) is False


def test_an_explicit_grant_lets_an_app_exceed_its_ceiling():
    # This is the whole point of the ceiling: it is a default, not a cap. An
    # app can always exceed it via the existing consent/grant flow.
    assert capability_allowed("ai-generated", "app.kv", granted=["app.kv"]) is True
    assert capability_allowed("user-uploaded", "app.net", granted=["app.net"]) is True
    assert capability_allowed("unknown", "app.memory.search", granted=["app.memory"]) is True
    # A grant recorded as the exact sub-capability string also counts.
    assert capability_allowed("unknown", "app.memory.search", granted=["app.memory.search"]) is True


def test_default_provenance_for_trust_back_compat_mapping():
    # Legacy first-party built-ins classify as first-party; everything else
    # (community, missing, or any other value) defaults to user-uploaded --
    # the only other way an app reaches the userspace store today.
    assert default_provenance_for_trust("first-party") == "first-party"
    assert default_provenance_for_trust("community") == "user-uploaded"
    assert default_provenance_for_trust(None) == "user-uploaded"
    assert default_provenance_for_trust("") == "user-uploaded"
