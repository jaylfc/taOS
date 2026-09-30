"""taos-kiosk-shellmode starts the nav bar only once a surface needs it.

Jay (2026-09-30): the boot logo showed the nav bar. sway paints the boot
wordmark while the server starts, and a bar started with the session sat over
it. The bar must stay down through boot, a loading kiosk window and the lock
screen, and come up for the signed-in desktop or a taOS app window.

Run: python3 -m pytest -q pmos/kiosk/test_taos_kiosk_shellmode.py
"""
from __future__ import annotations

import importlib.machinery
import importlib.util
from pathlib import Path

import pytest

KIOSK = "chrome-127.0.0.1__-Default"
APP = "chrome-127.0.0.1__app.html-Default"


def _load():
    path = Path(__file__).with_name("bin") / "taos-kiosk-shellmode"
    loader = importlib.machinery.SourceFileLoader("taos_kiosk_shellmode", str(path))
    spec = importlib.util.spec_from_loader(loader.name, loader)
    mod = importlib.util.module_from_spec(spec)
    loader.exec_module(mod)
    return mod


@pytest.fixture()
def sm(monkeypatch, tmp_path):
    mod = _load()
    calls = {"bar": 0, "sway": []}
    monkeypatch.setenv("XDG_RUNTIME_DIR", str(tmp_path))

    def fake_run(argv, **kw):
        if argv == [mod.BAR]:
            calls["bar"] += 1
        return None

    monkeypatch.setattr(mod.subprocess, "run", fake_run)
    monkeypatch.setattr(mod, "swaymsg", lambda *a: calls["sway"].append(a))
    mod.calls = calls
    return mod


def con(app_id, title, fs=0, cid=7):
    return {"id": cid, "app_id": app_id, "name": title, "fullscreen_mode": fs}


def event(sm, c):
    """What main() does with one window event."""
    if sm.apply(c):
        sm.start_bar()


# Titles a kiosk window can carry before the page sets its own. The control
# ("taOS") is the signed-in desktop and MUST start the bar, so a loading()
# that swallowed every title would fail below rather than pass here.
@pytest.mark.parametrize("title", ["", "  ", "127.0.0.1:6969/", "http://127.0.0.1:6969/",
                                   "about:blank", "localhost:6969/auth/login"])
def test_a_loading_kiosk_window_starts_no_bar_and_is_not_touched(sm, title):
    event(sm, con(KIOSK, title, fs=1))
    assert sm.calls["bar"] == 0
    assert sm.calls["sway"] == []


def test_the_lock_screen_starts_no_bar(sm):
    event(sm, con(KIOSK, "Sign in — taOS", fs=1))
    assert sm.calls["bar"] == 0


def test_the_signed_in_desktop_starts_the_bar(sm):
    event(sm, con(KIOSK, "taOS", fs=1))
    assert sm.calls["bar"] == 1
    assert sm.calls["sway"] == [("[con_id=7] fullscreen disable",)]


def test_a_taos_app_window_starts_the_bar(sm):
    event(sm, con(APP, "Chat", fs=1))
    assert sm.calls["bar"] == 1


def test_boot_sequence_bar_appears_only_after_sign_in(sm):
    seq = [con(KIOSK, ""), con(KIOSK, "127.0.0.1:6969/"),
           con(KIOSK, "Sign in — taOS", fs=1)]
    for c in seq:
        event(sm, c)
    assert sm.calls["bar"] == 0
    event(sm, con(KIOSK, "taOS", fs=1))
    assert sm.calls["bar"] == 1


def test_a_running_bar_is_not_restarted_on_every_event(sm, monkeypatch):
    monkeypatch.setattr(sm, "bar_running", lambda: True)
    event(sm, con(KIOSK, "taOS"))
    event(sm, con(APP, "Chat"))
    assert sm.calls["bar"] == 0


def test_the_session_config_no_longer_starts_the_bar():
    conf = (Path(__file__).parent / "etc" / "sway-kiosk.conf").read_text()
    live = [ln for ln in conf.splitlines() if not ln.lstrip().startswith("#")]
    assert not any("taos-kiosk-bar" in ln for ln in live)
    assert any("exec_always /usr/local/bin/taos-kiosk-shellmode" in ln for ln in live)
