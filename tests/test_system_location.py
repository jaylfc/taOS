"""GET /api/system/location: the handset's latest GPS fix, read from the
runtime file taos-locationd writes (taOSmobile pmos/kiosk/bin/taos-locationd).

The fix is personal data, so beyond the shape cases these pin that a bad file
degrades to fix=None (never a 500), that the route needs a signed-in user, and
that the coordinates never reach a log record. Fixtures use a made-up point.
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from httpx import ASGITransport, AsyncClient

from tinyagentos.routes import system as system_routes

NOW = datetime(2026, 10, 9, 16, 0, 0, tzinfo=timezone.utc)


def _iso(dt: datetime) -> str:
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


def _good(age_s: int = 120) -> dict:
    return {
        "lat": 51.5,
        "lon": -0.12,
        "accuracy_m": 6,
        "gps_utc": "154530.00",
        "fix_utc": _iso(NOW - timedelta(seconds=age_s)),
        "source": "modem-gnss",
    }


@pytest.fixture
def loc(tmp_path, monkeypatch):
    d = tmp_path / "taos-location"
    f = d / "location.json"
    monkeypatch.setattr(system_routes, "_LOCATION_DIR", d)
    monkeypatch.setattr(system_routes, "_LOCATION_FILE", f)
    return d, f


def _write(f: Path, payload) -> None:
    f.parent.mkdir(parents=True, exist_ok=True)
    f.write_text(payload if isinstance(payload, str) else json.dumps(payload))


def test_dir_absent_is_unavailable(loc):
    assert system_routes._location_state(now=NOW) == {
        "available": False, "fix": None, "age_s": None,
    }


def test_dir_present_file_absent_is_available_without_fix(loc):
    d, _ = loc
    d.mkdir()
    assert system_routes._location_state(now=NOW) == {
        "available": True, "fix": None, "age_s": None,
    }


def test_fresh_fix(loc):
    _, f = loc
    good = _good(120)
    _write(f, good)
    state = system_routes._location_state(now=NOW)
    assert state["available"] is True
    assert state["age_s"] == 120
    assert state["fix"] == {
        "lat": 51.5, "lon": -0.12, "accuracy_m": 6,
        "fix_utc": good["fix_utc"], "source": "modem-gnss",
    }
    assert "gps_utc" not in state["fix"]


def test_fractional_seconds_and_null_accuracy(loc):
    _, f = loc
    good = _good(30)
    good["fix_utc"] = (NOW - timedelta(seconds=30)).strftime("%Y-%m-%dT%H:%M:%S.250000Z")
    good["accuracy_m"] = None
    _write(f, good)
    state = system_routes._location_state(now=NOW)
    assert state["fix"]["accuracy_m"] is None
    assert state["age_s"] == 29


def test_stale_fix_is_dropped_but_age_reported(loc):
    _, f = loc
    _write(f, _good(1801))
    state = system_routes._location_state(now=NOW)
    assert state["available"] is True
    assert state["fix"] is None
    assert state["age_s"] == 1801


def test_fix_at_the_staleness_boundary_is_kept(loc):
    _, f = loc
    _write(f, _good(1800))
    state = system_routes._location_state(now=NOW)
    assert state["fix"] is not None
    assert state["age_s"] == 1800


def test_small_future_skew_reads_as_age_zero(loc):
    _, f = loc
    _write(f, _good(-30))
    state = system_routes._location_state(now=NOW)
    assert state["fix"] is not None
    assert state["age_s"] == 0


def _mut(**kw):
    g = _good()
    for k, v in kw.items():
        if v is _DROP:
            g.pop(k)
        else:
            g[k] = v
    return g


_DROP = object()

MALFORMED = {
    "not-json": "{nope",
    "json-list": "[51.5, -0.12]",
    "lat-missing": _mut(lat=_DROP),
    "lat-string": _mut(lat="51.5"),
    "lat-bool": _mut(lat=True),
    "lat-out-of-range": _mut(lat=95.0),
    "lon-out-of-range": _mut(lon=-181),
    "accuracy-string": _mut(accuracy_m="6"),
    "fix-utc-missing": _mut(fix_utc=_DROP),
    "fix-utc-garbage": _mut(fix_utc="yesterday"),
    "fix-utc-far-future": _mut(fix_utc=_iso(NOW + timedelta(hours=2))),
    "oversize": json.dumps({**_good(), "pad": "x" * 5000}),
}


@pytest.mark.parametrize("name", sorted(MALFORMED))
def test_malformed_file_is_no_fix_never_an_error(loc, name):
    _, f = loc
    _write(f, MALFORMED[name])
    state = system_routes._location_state(now=NOW)
    assert state == {"available": True, "fix": None, "age_s": None}, name


def test_unreadable_file_is_no_fix(loc, monkeypatch):
    _, f = loc
    _write(f, _good())

    def boom(*a, **kw):
        raise PermissionError("denied")

    monkeypatch.setattr(Path, "open", boom)
    state = system_routes._location_state(now=NOW)
    assert state == {"available": True, "fix": None, "age_s": None}


def test_fix_never_reaches_a_log_record(loc, caplog):
    _, f = loc
    _write(f, _good())
    with caplog.at_level(logging.DEBUG):
        state = system_routes._location_state(now=NOW)
        _write(f, "{nope")
        system_routes._location_state(now=NOW)
    assert state["fix"] is not None  # control: the good read happened
    text = "\n".join(r.getMessage() for r in caplog.records)
    assert "51.5" not in text and "-0.12" not in text


@pytest.mark.asyncio
async def test_route_needs_a_signed_in_user(loc, app, client):
    signed_in = await client.get("/api/system/location")
    assert signed_in.status_code == 200, signed_in.text
    assert set(signed_in.json()) == {"available", "fix", "age_s"}
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as bare:
        anon = await bare.get("/api/system/location")
    assert anon.status_code == 401, anon.text
