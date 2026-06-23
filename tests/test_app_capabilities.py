"""The closed userspace capability vocabulary + manifest validation (#56 slice 1)."""
import pytest

from tinyagentos.userspace.capabilities import (
    FREE_CAPS,
    GATED_CAPS,
    KNOWN_CAPS,
    is_known_capability,
)
from tinyagentos.userspace.package import PackageError, parse_manifest


def test_known_caps_is_free_plus_gated_and_disjoint():
    assert KNOWN_CAPS == FREE_CAPS | GATED_CAPS
    assert FREE_CAPS.isdisjoint(GATED_CAPS)


def test_broker_reexports_the_same_sets():
    # The broker must enforce the exact vocabulary the parser validates against.
    from tinyagentos.userspace import broker

    assert broker.FREE_CAPS is FREE_CAPS
    assert broker.GATED_CAPS is GATED_CAPS


@pytest.mark.parametrize("cap", sorted(KNOWN_CAPS))
def test_every_known_cap_is_recognised(cap):
    assert is_known_capability(cap) is True


def test_network_grant_is_recognised_regardless_of_origin_format():
    # is_known_capability classifies the capability kind; origin format is the
    # parser's job, so even a malformed origin is "known" at this layer.
    assert is_known_capability("network:https://example.com") is True
    assert is_known_capability("network:garbage") is True


def test_unknown_and_non_string_caps_are_rejected():
    assert is_known_capability("app.bogus") is False
    assert is_known_capability("files.read") is False  # not the shipped scheme
    assert is_known_capability(123) is False


def _manifest(perms):
    return f"id: a\nname: A\nversion: 1.0.0\napp_type: web\npermissions:\n" + "".join(
        f"  - {p}\n" for p in perms
    )


def test_manifest_accepts_known_caps_and_network_origin():
    data = parse_manifest(_manifest(["app.kv", "app.net", "network:https://api.example.com"]))
    assert data["permissions"] == ["app.kv", "app.net", "network:https://api.example.com"]


def test_manifest_rejects_unknown_capability():
    with pytest.raises(PackageError, match="unknown capability"):
        parse_manifest(_manifest(["app.kv", "app.bogus"]))


def test_manifest_rejects_wrong_scheme_capability():
    # A plausible-but-wrong name (the non-shipped scheme) must be rejected at
    # parse time, not silently pass to fail later in the broker.
    with pytest.raises(PackageError, match="unknown capability"):
        parse_manifest(_manifest(["files.read"]))


def test_manifest_still_rejects_malformed_network_origin():
    with pytest.raises(PackageError, match="invalid network permission origin"):
        parse_manifest(_manifest(["network:http://insecure.example.com"]))


def test_manifest_rejects_non_string_permission():
    with pytest.raises(PackageError, match="permission must be a string"):
        parse_manifest("id: a\nname: A\nversion: 1.0.0\napp_type: web\npermissions:\n  - 42\n")
