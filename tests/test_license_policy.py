"""Engine tests for tinyagentos.license_policy (restrictive-licence gate)."""
from types import SimpleNamespace

import pytest

from tinyagentos import license_policy as lp


def _m(**kw):
    base = dict(license="", weights_license="", model_license="", license_class="",
                license_version="", license_url="", license_excluded_territories=[])
    base.update(kw)
    return SimpleNamespace(**base)


@pytest.mark.parametrize("field,value", [
    ("license", "Qwen Research License"),
    ("weights_license", "CC-BY-NC 4.0"),
    ("model_license", "Gemma Terms of Use"),
    ("license_class", "research-only"),
    ("license_class", "some-new-class"),
    ("license", "TotallyMadeUp License 9"),
])
def test_any_restrictive_field_gates(field, value):
    r = lp.evaluate(_m(**{field: value}), "x")
    assert r.restrictive, (field, value)


def test_permissive_everything_is_not_gated():
    r = lp.evaluate(_m(license="MIT", weights_license="Apache-2.0", license_class="permissive"))
    assert not r.restrictive


def test_restrictive_weights_override_permissive_code_licence():
    r = lp.evaluate(_m(license="MIT", weights_license="CC-BY-NC 4.0", license_class=""))
    assert r.restrictive and r.cls == "non-commercial" and r.name == "CC-BY-NC 4.0"


def test_classify_unknown_is_not_silently_permissive():
    assert lp.classify("TotallyMadeUp License 9")[0] == "unknown"
    assert lp.classify("")[0] == "unknown"


def test_or_expression_permissive_side_wins():
    assert lp.classify("MIT OR Apache-2.0")[0] == "permissive"
    assert lp.classify("Qwen Research License OR MIT")[0] == "permissive"


@pytest.mark.parametrize("via,ok", [
    ("session", True), ("registry_jwt", False), ("registry_jwt_candidate", False),
    ("registry_jwt_deployed_agent", False), ("registry_jwt_native_agent", False),
    ("local_token", False), ("device_bearer", False), ("device_bearer_candidate", False),
    ("loopback", False), ("exempt", False), ("service_token", False), (None, False), ("", False),
])
def test_only_a_person_session_may_accept(via, ok):
    assert lp.via_may_accept(via) is ok


def test_territory_limited_needs_country_only_when_limited():
    limited = lp.evaluate(_m(license="Tencent HunyuanVideo Community License"))
    assert limited.territory_limited
    assert lp.territory_check(limited, None) == "needs_country"
    assert lp.territory_check(limited, "") == "needs_country"
    assert lp.territory_check(limited, "US") == "ok"
    assert lp.territory_check(limited, "de") == "excluded"
    assert lp.territory_check(limited, "GB") == "excluded"
    not_limited = lp.evaluate(_m(weights_license="CC-BY-NC 4.0"))
    assert lp.territory_check(not_limited, None) == "ok"


def test_manifest_can_declare_excluded_territories():
    r = lp.evaluate(_m(license="Gemma Terms of Use", license_excluded_territories=["xx"]))
    assert r.territory_limited and lp.territory_check(r, "XX") == "excluded"


@pytest.mark.parametrize("code,out", [("us", "US"), (" GB ", "GB"), ("USA", None), ("", None), (5, None), ("1A", None)])
def test_valid_country(code, out):
    assert lp.valid_country(code) == out


@pytest.mark.parametrize("cls", sorted(lp.RESTRICTIVE_CLASSES))
def test_acceptance_text_has_no_placeholders(cls):
    r = lp.Restriction(restrictive=True, cls=cls, name="Example Licence", version="2.0",
                       url="https://example.org/licence")
    text = lp.acceptance_text("Example App", r)
    for bad in ("{", "}", "TODO", "...", "<", ">", "XXX", "None", "PLACEHOLDER"):
        assert bad not in text, (cls, bad, text)
    assert "Example Licence" in text and "version 2.0" in text and "https://example.org/licence" in text
    assert chr(0x2014) not in text


def test_acceptance_text_without_url_or_version_is_still_complete():
    text = lp.acceptance_text("App", lp.Restriction(restrictive=True, cls="non-commercial", name="CC-BY-NC 4.0"))
    assert "None" not in text and "{" not in text and "  " not in text
