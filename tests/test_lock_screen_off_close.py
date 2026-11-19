"""The lock page's close-to-black on screen-off must be quick.

The kiosk script waits until the glass is black before cutting panel power, so
every millisecond of the close animation is a millisecond the panel stays lit
after the user pressed the power key. These tests read the durations out of
what the page actually ships (the stylesheet and the served lock-screen.js), so
they track the real values rather than a constant added for the test.
"""
from __future__ import annotations

import asyncio
import re

import tinyagentos.routes.auth as auth

MAX_CLOSE_MS = 200


def _ms(value: str, unit: str) -> float:
    return float(value) * (1000 if unit == "s" else 1)


def _served_js() -> str:
    resp = asyncio.run(auth.lock_screen_script(None))
    return resp.body.decode()


def _rule_after(css: str, selector: str, start: int = 0) -> str:
    at = css.index(selector, start)
    return css[at:css.index("}", at) + 1]


def _handler(js: str) -> str:
    """The screen-off handler that drives the close (the one that sets
    data-closing), sliced from its listener to the next listener."""
    mark = js.index('setAttribute("data-closing", "1")')
    start = js.rindex('addEventListener("screen-off"', 0, mark)
    end = js.index('addEventListener("screen-on"', mark)
    return js[start:end]


def test_close_animation_is_at_most_200ms():
    css = auth._LOCK_SCREEN_STYLE
    rule = _rule_after(css, '.lockscreen[data-closing="1"] { animation:')
    m = re.search(r"animation:\s*ls-tv-off\s+([\d.]+)(ms|s)\b", rule)
    assert m, rule
    assert _ms(m.group(1), m.group(2)) <= MAX_CLOSE_MS, rule


def test_close_timer_that_blacks_the_page_is_at_most_200ms():
    js = _served_js()
    h = _handler(js)
    after = h[h.index('setAttribute("data-closing", "1")'):]
    m = re.search(r"\},\s*([A-Za-z_]\w*|\d+)\s*\);", after)
    assert m, after[:400]
    token = m.group(1)
    if token.isdigit():
        ms = float(token)
    else:
        d = re.search(r"var\s+" + token + r"\s*=\s*(\d+)\s*;", js)
        assert d, token
        ms = float(d.group(1))
    assert ms <= MAX_CLOSE_MS, (token, ms)


def test_reduced_motion_goes_black_immediately():
    js = _served_js()
    h = _handler(js)
    # The close is skipped (page blanked at once) when motion is reduced.
    assert "prefers-reduced-motion: reduce" in h, h[:600]
    css = auth._LOCK_SCREEN_STYLE
    at = css.index("@media (prefers-reduced-motion: reduce)", css.index("@keyframes ls-tv-fade-in"))
    block = css[at:css.index("\n}", at)]
    m = re.search(r'data-closing="1"\][^}]*?([\d.]+)(ms|s)\b', block)
    assert m, block
    assert _ms(m.group(1), m.group(2)) <= MAX_CLOSE_MS, block


def test_login_page_renders_blanked_while_the_screen_is_off(monkeypatch):
    """The shell navigates here as the phone sleeps; the lock face must not be
    painted in the gap before the stream's screen-off event arrives."""
    monkeypatch.setattr(auth, "_LOCK_SCREEN_STATE", "screen-off")
    page = auth._login_page(unlock_method="swipe")
    assert 'id="lockscreen" data-blanked="1"' in page
    assert 'class="lockscreen-on ls-black"' in page
    monkeypatch.setattr(auth, "_LOCK_SCREEN_STATE", "screen-on")
    lit = auth._login_page(unlock_method="swipe")
    assert "data-blanked" not in lit.split('id="lockscreen"', 1)[1][:60]
    assert 'class="lockscreen-on"' in lit


def test_wake_animation_unchanged():
    css = auth._LOCK_SCREEN_STYLE
    assert '.lockscreen[data-opening="1"] { animation: ls-tv-on 0.5s' in css
    assert "var TV_MS = 500;" in _served_js()
