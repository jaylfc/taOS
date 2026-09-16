"""The on-screen keyboard on the server-rendered auth pages.

taOS targets touchscreens with no keyboard, under `chromium --kiosk` on a bare
X session where no IME can be summoned. These assert the two things that make
the keyboard trustworthy: it is really there on both pages, and adding it did
not cost the no-JavaScript guarantee those pages are written for.
"""
from __future__ import annotations

import json
import re

import pytest

from tinyagentos.routes.auth import (
    _LOCK_SCREEN_SCRIPT as LOCK_SCRIPT,
    _login_page,
    _pin_panel_html,
    _setup_page,
)
from tinyagentos.routes.onscreen_keyboard import (
    OSK_SCRIPT,
    OSK_SCRIPT_PATH,
    OSK_STYLE,
    osk_assets,
)


@pytest.fixture()
def login_console():
    """Login page as the device's own screen sees it (PIN offered)."""
    return _login_page("", multi_user=False, next_url="", pin_available=True)


@pytest.fixture()
def login_remote():
    """Login page as a LAN browser sees it (no PIN)."""
    return _login_page("", multi_user=False, next_url="", pin_available=False)


#: One forecast response, trimmed to the fields the route reads.
_FORECAST_PAYLOAD = {
    "current": {
        "temperature_2m": 13.4,
        "apparent_temperature": 11.2,
        "is_day": 1,
        "weather_code": 3,
        "wind_speed_10m": 9.3,
    },
    "daily": {"temperature_2m_max": [15.1], "temperature_2m_min": [8.7]},
}


def _stub_forecast(monkeypatch, payload):
    """Replace the HTTP client the weather route uses and record what it asked.

    Returns the record, so a test can assert on the OUTGOING request rather than
    on our own constants -- the units are a property of the call, and a route
    that quietly asked for fahrenheit would still match any string we own.
    """
    from tinyagentos.routes import auth as auth_mod

    captured = {"calls": 0, "url": None, "params": None}

    class _Response:
        def raise_for_status(self):
            return None

        def json(self):
            return payload

    class _Client:
        def __init__(self, *_args, **_kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_exc):
            return False

        async def get(self, url, params=None):
            captured["calls"] += 1
            captured["url"] = url
            captured["params"] = params
            return _Response()

    monkeypatch.setattr(auth_mod.httpx, "AsyncClient", _Client)
    return captured


@pytest.fixture()
def weather_cache_reset():
    """The weather cache is module state, so a test that leaves a reading behind
    makes the next one pass without calling anything."""
    from tinyagentos.routes import auth as auth_mod

    auth_mod._weather_cached = None
    auth_mod._weather_cached_at = 0.0
    yield
    auth_mod._weather_cached = None
    auth_mod._weather_cached_at = 0.0


class TestKeyboardIsPresentWhereItIsNeeded:
    def test_login_page_ships_the_keyboard(self, login_remote):
        assert f'src="{OSK_SCRIPT_PATH}"' in login_remote

    def test_setup_page_ships_the_keyboard(self):
        """Onboarding is the FIRST thing a new touchscreen device does; without
        a keyboard here the device cannot be set up at all."""
        page = _setup_page("")
        assert f'src="{OSK_SCRIPT_PATH}"' in page

    def test_keyboard_is_offered_even_without_a_pin(self, login_remote):
        """The keyboard is an accessibility affordance, not part of PIN sign-in;
        a password user on a touchscreen needs it just as much."""
        assert 'id="pin-panel"' not in login_remote
        assert f'src="{OSK_SCRIPT_PATH}"' in login_remote

    def test_assets_ship_style_and_script_together(self):
        out = osk_assets()
        assert "<style>" in out
        assert OSK_STYLE.strip()[:20] in out
        assert f'src="{OSK_SCRIPT_PATH}"' in out

    def test_script_is_never_inlined(self):
        """taOS sends `script-src 'self'`, which silently refuses inline
        <script> blocks. An inlined keyboard produces correct-looking HTML that
        the browser drops at execution time -- the page renders, the keyboard
        never appears, and any test asserting on the markup still passes. This
        is the assertion that would have caught it."""
        out = osk_assets()
        assert OSK_SCRIPT.strip()[:40] not in out, "keyboard script must not be inlined"
        assert "defer" in out


class TestNoJavaScriptGuaranteeIsIntact:
    """The auth pages are deliberately server-rendered and work with JS off.

    The keyboard is added at runtime by script, so with JS disabled the page
    must still be a plain, submittable HTML form.
    """

    def test_password_form_still_posts_without_script(self, login_console):
        assert 'method="POST"' in login_console
        assert 'action="/auth/login"' in login_console
        assert 'name="password"' in login_console

    def test_setup_form_still_posts_without_script(self):
        page = _setup_page("")
        assert 'method="POST"' in page
        assert 'action="/auth/setup"' in page
        for field in ("username", "full_name", "email", "password"):
            assert f'name="{field}"' in page

    def test_no_key_markup_is_server_rendered(self, login_console):
        """Keys are built by script. If they were in the HTML they would appear
        for no-JS users as dead buttons that type nothing."""
        assert 'class="osk-key"' not in login_console

    def test_password_panel_is_only_hidden_when_a_pin_is_offered(self, login_remote):
        """With no PIN there is nothing to switch to, so the form must not start
        hidden — that would leave a no-JS user with a blank page."""
        assert re.search(r'id="pw-panel"[^>]*\shidden', login_remote) is None


class TestKeysCannotSubmitTheForm:
    def test_every_key_is_type_button(self):
        """A bare <button> in a form defaults to submit. A keyboard whose keys
        each submit the login form would be worse than no keyboard at all."""
        assert 'b.type = "button"' in OSK_SCRIPT
        # The toggle lives inside the page too and must not submit either.
        assert 'toggle.type = "button"' in OSK_SCRIPT

    def test_pin_controls_are_type_button(self, login_console):
        for el_id in ("pin-submit", "use-password"):
            match = re.search(rf'<button[^>]*id="{el_id}"', login_console)
            assert match, f"{el_id} not found"
            assert 'type="button"' in match.group(0)


class TestFocusIsNotStolenFromTheField:
    def test_pointerdown_is_cancelled(self):
        """Without preventDefault on pointerdown the first tap blurs the input
        and the second types into nothing."""
        assert 'panel.addEventListener("pointerdown"' in OSK_SCRIPT
        assert "ev.preventDefault()" in OSK_SCRIPT

    def test_nothing_closes_the_panel_on_blur(self):
        """The keyboard must not close because focus moved.

        Measured in Chromium at 1024x600: tapping a button focuses it on
        pointerdown, which blurred the field; the old focusout handler then hid
        the panel, un-reserving ~276px, which reflowed the card out from under
        the finger — so mouseup landed elsewhere and no `click` was ever fired.
        "Sign in with PIN" produced pointerdown and no click: PIN sign-in was
        dead to touch. Visibility now follows the toggle only.
        """
        assert 'addEventListener("focusout"' not in OSK_SCRIPT
        # hide() may only be reached from the explicit on/off path.
        assert OSK_SCRIPT.count("hide();") == 1
        assert "function setEnabled" in OSK_SCRIPT

    def test_reserved_space_is_measured_not_guessed(self):
        """A fixed vh guess is wrong for at least one of {numeric, letters} x
        {600px panel, desktop window}, and being wrong low puts the card's
        buttons under the keys."""
        assert "panel.offsetHeight" in OSK_SCRIPT
        assert 'document.body.style.paddingBottom = h ? h + "px" : ""' in OSK_SCRIPT

    def test_a_card_too_tall_for_the_space_left_stays_reachable(self):
        """The auth pages centre their card in a flex body; centring a card
        taller than the remaining space puts its actions behind the keys with
        nothing to scroll."""
        rule = re.search(r"body\.osk-open\s*\{([^}]*)\}", OSK_STYLE)
        assert rule, "no body.osk-open rule"
        assert "flex-start" in rule.group(1)
        assert "overflow-y: auto" in rule.group(1)


class TestAccessibility:
    def test_toggle_exposes_pressed_state(self):
        assert 'toggle.setAttribute("aria-pressed"' in OSK_SCRIPT

    def test_panel_is_a_labelled_group(self):
        assert 'panel.setAttribute("role", "group")' in OSK_SCRIPT
        assert 'aria-label", "On-screen keyboard"' in OSK_SCRIPT

    def test_every_key_carries_an_accessible_name(self):
        assert 'b.setAttribute("aria-label"' in OSK_SCRIPT

    def test_state_changes_are_announced(self):
        assert 'live.setAttribute("aria-live", "polite")' in OSK_SCRIPT

    def test_keyboard_activation_path_exists(self):
        """Tab+Enter never fires pointerdown, so a click path must exist too or
        the keyboard is unusable by switch/AT users."""
        assert 'panel.addEventListener("click"' in OSK_SCRIPT

    def test_touch_targets_meet_the_minimum(self):
        """44px is the floor for a finger target; keys must not go below it."""
        sizes = [int(n) for n in re.findall(r"min-height:\s*(\d+)px", OSK_STYLE)]
        assert sizes, "no key height declared"
        assert min(sizes) >= 44

    def test_no_key_preview_popup(self):
        """Phone keyboards magnify the pressed glyph. On a wall-mounted panel
        that renders the password to the whole room."""
        assert "osk-preview" not in OSK_STYLE
        assert "preview" not in OSK_SCRIPT.lower()


class TestNumericLayoutForPin:
    def test_numeric_layout_selected_by_inputmode(self):
        assert 'mode === "numeric"' in OSK_SCRIPT

    def test_pin_field_suppresses_the_shared_keyboard_on_the_lock_screen(self, login_console):
        """The console page is the lock screen, which draws its OWN keypad.

        inputmode="none" is what keeps the shared on-screen keyboard (and the
        compositor's Wayland keyboard) from opening a SECOND keypad over the
        first -- and the OSK's open state re-anchors the page to the top of the
        viewport, which is what pushed the passcode off a tall phone screen.
        """
        match = re.search(r'<input[^>]*id="pin-input"[^>]*>', login_console)
        assert match
        assert 'inputmode="none"' in match.group(0)

    def test_pin_field_still_requests_the_numeric_pad_without_a_keypad(self):
        """Off the lock screen the shared numeric OSK is still the way in.

        _pin_panel_html is what the non-keypad caller renders, so this asserts
        the branch a future non-lock-screen page would get -- without it the
        keypad=False path is untested and could rot to inputmode="none" too,
        leaving that page with no keyboard at all.
        """
        panel = _pin_panel_html("/desktop", keypad=False)
        match = re.search(r'<input[^>]*id="pin-input"[^>]*>', panel)
        assert match
        assert 'inputmode="numeric"' in match.group(0)
        assert 'data-osk-submit="pin-submit"' in match.group(0)

    def test_pin_field_is_masked(self, login_console):
        match = re.search(r'<input[^>]*id="pin-input"[^>]*>', login_console)
        assert 'type="password"' in match.group(0)

    def test_pin_field_is_not_autofilled_or_saved(self, login_console):
        match = re.search(r'<input[^>]*id="pin-input"[^>]*>', login_console)
        assert 'autocomplete="off"' in match.group(0)

    def test_shared_keyboards_enter_routing_is_still_wired(self):
        """data-osk-submit routes Enter to the PIN handler; without it Enter
        would fall through to the password form and post an empty password.

        The lock screen does not use the shared keyboard, but the mechanism must
        stay intact for the keypad=False rendering that does.
        """
        panel = _pin_panel_html("/desktop", keypad=False)
        match = re.search(r'<input[^>]*id="pin-input"[^>]*>', panel)
        assert 'data-osk-submit="pin-submit"' in match.group(0)
        assert "data-osk-submit" in OSK_SCRIPT

    def test_lock_screen_keypad_can_actually_submit(self, login_console):
        """A keypad with no reachable submit is a phone nobody can unlock.

        There is no auto-submit -- the PIN's length is server-side, so the page
        cannot know when the user has finished typing. #pin-submit is therefore
        the ONLY way in, and a CSS rule that hides it strands the user on the
        lock screen with no way to authenticate.
        """
        assert 'id="pin-submit"' in login_console
        assert not re.search(
            r"\.lockscreen\s+#pin-submit\s*\{[^}]*display:\s*none", login_console
        )

    def test_keypad_is_reachable_without_a_gesture(self, login_console):
        """The keypad is no longer always on screen, so the way to it must be a
        real, focusable BUTTON.

        The swipe is a shortcut. If the only route to the passcode were a drag,
        anyone who cannot make that drag -- or any device where the touch layer
        is misbehaving -- would be locked out of their own phone with the
        keypad rendered but hidden.
        """
        match = re.search(r'<button[^>]*id="ls-unlock-btn"[^>]*>', login_console)
        assert match, "no unlock button"
        assert 'type="button"' in match.group(0)
        assert "ls-unlock-btn" in LOCK_SCRIPT
        assert 'unlockBtn.addEventListener("click", openPasscode)' in LOCK_SCRIPT

    def test_resting_screen_hides_the_passcode_but_not_by_hiding_submit(self, login_console):
        """The passcode is hidden as a SHEET -- the whole shell slides away --
        rather than by hiding its controls one by one.

        This is the rule the submit-visibility test protects, stated for the new
        resting state: whatever hides the passcode must be reversible by the
        unlock control, and #pin-submit must never be the thing being hidden.
        """
        assert 'setAttribute("data-sheet", "none")' in LOCK_SCRIPT
        assert re.search(
            r'\.lockscreen:not\(\[data-sheet="passcode"\]\)\s+\.ls-foot\s*\{[^}]*transform',
            login_console,
        ), "the passcode shell is not hidden by the sheet transform"
        assert not re.search(
            r"\.lockscreen[^{]*#pin-submit\s*\{[^}]*display:\s*none", login_console
        )

    def test_islands_are_buttons_not_a_list(self, login_console):
        """An island opens a sheet, so it must be operable by keyboard too."""
        assert 'el.setAttribute("role", "button")' in LOCK_SCRIPT
        assert 'el.setAttribute("tabindex", "0")' in LOCK_SCRIPT
        # A role="list" whose children are buttons is an invalid a11y tree.
        assert 'id="ls-activity" role="group"' in login_console

    def test_lock_chrome_does_not_select_text_like_a_browser(self, login_console):
        """Press-and-hold is bound to the islands. Without this, chromium starts
        a text selection on that exact gesture and raises the copy callout."""
        assert re.search(
            r"body\.lockscreen-on\s*\{[^}]*user-select:\s*none", login_console
        )
        assert re.search(
            r"\.ls-msg,\s*\.ls-compose-input\s*\{[^}]*user-select:\s*text", login_console
        ), "selection should still work inside the conversation"

    def test_every_url_the_lock_screen_fetches_is_reachable_pre_auth(self):
        """The lock screen renders BEFORE sign-in, so anything it fetches must be
        exempt from the auth middleware or it 401s.

        This is derived from the script rather than listed by hand: the defect it
        guards was a new per-agent route (/auth/lock-thread/) added to the page
        and to EXEMPT_PATHS' sibling list but NOT to EXEMPT_PREFIXES, so the
        conversation sheet opened empty with nothing logged. A hand-maintained
        list would have been updated in the same pass that forgot the prefix.
        """
        from tinyagentos.auth_middleware import EXEMPT_PATHS, EXEMPT_PREFIXES

        urls = set(re.findall(r'fetch\(\s*"([^"]+)"', LOCK_SCRIPT))
        assert urls, "no fetches found -- the regex stopped matching the script"
        for url in sorted(urls):
            exempt = url in EXEMPT_PATHS or any(
                url.startswith(prefix) for prefix in EXEMPT_PREFIXES
            )
            assert exempt, f"{url} is fetched pre-auth but is not exempt"

    def test_lock_screen_renders_its_own_keypad(self, login_console):
        assert 'id="ls-pad"' in login_console
        for digit in "0123456789":
            assert f'data-digit="{digit}"' in login_console
        assert 'data-action="back"' in login_console


class TestPinPanelIsConsoleOnly:
    def test_panel_absent_off_console(self, login_remote):
        assert 'id="pin-panel"' not in login_remote
        assert 'id="use-pin"' not in login_remote

    def test_pin_endpoint_not_named_off_console(self, login_remote):
        """The page must not even mention PIN sign-in to a remote browser."""
        assert "/auth/pin-login" not in login_remote
        assert "/auth/pin-panel.js" not in login_remote

    def test_panel_present_on_console(self, login_console):
        assert 'id="pin-panel"' in login_console
        assert 'src="/auth/pin-panel.js"' in login_console

    def test_password_remains_reachable_from_the_pin_panel(self, login_console):
        """A PIN that fails must never strand the user with no way back."""
        assert 'id="use-password"' in login_console


class TestPinPanelChrome:
    """Rendered-state defects seen on a 1024x600 panel, not markup presence."""

    def test_empty_error_bar_is_not_rendered(self, login_console):
        """`.error` carries a red background and border, and the live region
        has to exist before it has anything to say — so without this the page
        shows a bare red slab having failed at nothing."""
        assert re.search(r"#pin-error:empty\s*\{[^}]*display:\s*none", login_console)

    def test_primary_pin_action_is_a_real_touch_target(self, login_console):
        """#pin-submit is type=button (a submit would post the password form),
        so it misses `button[type="submit"]` styling entirely and renders as a
        ~21px native button — half the 44px floor, on a touchscreen."""
        rule = re.search(r"#pin-submit\s*\{([^}]*)\}", login_console)
        assert rule, "#pin-submit carries no styling of its own"
        found = re.search(r"min-height:\s*(\d+)px", rule.group(1))
        assert found and int(found.group(1)) >= 44

    def test_card_sheds_height_while_the_keyboard_is_open(self, login_console):
        """The keypad takes 291px of a 600px panel. Without this the card's
        buttons are only reachable by scrolling a page that gives no sign it
        scrolls."""
        assert "body.osk-open .card" in login_console


class TestFocusedFieldIsClearOfTheKeys:
    def test_reveal_measures_against_the_panel_not_the_viewport(self):
        """scrollIntoView treats the strip the keyboard sits on as visible —
        the panel is position:fixed OVER the viewport — so a field level with
        the top row is left tucked behind the keys."""
        assert "scrollIntoView(" not in OSK_SCRIPT  # the call, not the comment
        assert "panel.getBoundingClientRect().top" in OSK_SCRIPT
        assert "window.scrollBy" in OSK_SCRIPT


def test_insert_honours_maxlength():
    """Writing .value directly bypasses the browser's own maxlength.

    The setup PIN field is maxlength=12, so without a clamp the numeric pad
    could enter a 13th digit. This asserts the clamp is PRESENT in the served
    script; that it BEHAVES is proven on the device (a script that is served
    but refused by CSP passes any assertion about its text -- see
    test_script_is_never_inlined for why that distinction is load-bearing).
    """
    from tinyagentos.routes.onscreen_keyboard import OSK_SCRIPT

    assert 'getAttribute("maxlength")' in OSK_SCRIPT
    assert "if (room <= 0) return;" in OSK_SCRIPT


class TestLockScreenWeather:
    """The weather row between the clock and the agent islands.

    Two things make it safe rather than merely present: the page never talks to
    the forecast host itself, and the reading is cached so a pocketed phone does
    not call out once per wake.
    """

    def test_weather_sits_between_the_clock_and_the_islands(self, login_console):
        """Jay asked for it there, and the order is the one the eye reads this
        screen in: what time is it, what is it like out, what are my agents up
        to. A row rendered after the feed would be below the fold on a phone."""
        date_at = login_console.index('id="ls-date"')
        weather_at = login_console.index('id="ls-weather"')
        feed_at = login_console.index('id="ls-feed"')
        assert date_at < weather_at < feed_at

    def test_the_page_never_calls_the_forecast_host_itself(self, login_console):
        """The lock screen paints before sign-in and on every wake. A fetch from
        the page would announce the device to a third party each time, from a
        surface nobody has authenticated to -- so the call belongs on the server
        and the page may only know about its own route."""
        assert "open-meteo" not in LOCK_SCRIPT
        assert "open-meteo" not in login_console
        assert 'fetch("/auth/lock-weather"' in LOCK_SCRIPT

    def test_the_reading_is_fixed_to_liverpool_in_celsius_and_mph(self):
        from tinyagentos.routes import auth as auth_mod

        assert auth_mod._WEATHER_PLACE == "Liverpool"
        # Liverpool city centre, to the precision the forecast grid can use.
        assert round(auth_mod._WEATHER_LAT, 2) == 53.41
        assert round(auth_mod._WEATHER_LON, 2) == -2.99

    @pytest.mark.asyncio
    async def test_the_request_asks_for_celsius_and_mph(self, monkeypatch, weather_cache_reset):
        """Asserted on the OUTGOING request, not on a constant: the units are a
        property of what we ask the API for, and a route that quietly asked for
        fahrenheit would still satisfy any check of our own strings."""
        from tinyagentos.routes import auth as auth_mod

        captured = _stub_forecast(monkeypatch, _FORECAST_PAYLOAD)
        await auth_mod._fetch_weather()

        assert captured["params"]["temperature_unit"] == "celsius"
        assert captured["params"]["wind_speed_unit"] == "mph"
        assert captured["params"]["latitude"] == auth_mod._WEATHER_LAT

    @pytest.mark.asyncio
    async def test_a_wake_does_not_hammer_the_api(self, monkeypatch, weather_cache_reset):
        """A phone repaints its lock screen every time it is woken. Without the
        cache that is one forecast call per wake, for a number that moves a few
        times a day."""
        from tinyagentos.routes import auth as auth_mod

        captured = _stub_forecast(monkeypatch, _FORECAST_PAYLOAD)
        first = await auth_mod._weather_reading()
        second = await auth_mod._weather_reading()

        assert first == second
        assert captured["calls"] == 1

    @pytest.mark.asyncio
    async def test_a_failed_refresh_keeps_the_last_good_reading(
        self, monkeypatch, weather_cache_reset
    ):
        """A handset loses its link constantly. A row that vanishes every time
        the refresh fails reads as broken; a temperature fifteen minutes old is
        still roughly true."""
        from tinyagentos.routes import auth as auth_mod

        _stub_forecast(monkeypatch, _FORECAST_PAYLOAD)
        good = await auth_mod._weather_reading()
        assert good is not None

        # Expire the cache, then take the API away.
        auth_mod._weather_cached_at = 0.0

        async def _dead(*_args, **_kwargs):
            return None

        monkeypatch.setattr(auth_mod, "_fetch_weather", _dead)
        assert await auth_mod._weather_reading() == good

    def test_a_clear_night_is_not_drawn_as_a_sun(self):
        from tinyagentos.routes.auth import _weather_condition

        assert _weather_condition(0, True)[1] == "clear"
        assert _weather_condition(0, False)[1] == "night"
        # An unmapped code degrades to cloud rather than to a blank icon.
        assert _weather_condition(4242, True)[1] == "cloud"

    def test_every_icon_the_route_can_name_is_one_the_page_can_draw(self):
        """The wording and the picture are chosen together in the route, and the
        page owns the shapes. A code mapped to an icon the script has no path for
        would silently render as cloud, so the two tables must agree."""
        from tinyagentos.routes.auth import _WMO_CONDITIONS

        drawn = set(re.findall(r"^\s*(\w+):\s*'<", LOCK_SCRIPT, re.MULTILINE))
        named = {icon for _label, icon in _WMO_CONDITIONS.values()} | {"night"}
        assert named <= drawn, f"route names icons the page cannot draw: {named - drawn}"

    @pytest.mark.asyncio
    async def test_weather_is_console_only(self, monkeypatch):
        """Same rule as every other lock-screen route: a LAN browser gets 403."""
        from tinyagentos.routes import auth as auth_mod

        monkeypatch.setattr(auth_mod, "_request_is_console", lambda _request: False)
        assert (await auth_mod.lock_weather(None)).status_code == 403


class TestLockScreenNotifications:
    """The collated notification stacks under the agent islands."""

    def test_the_stack_renders_under_the_agent_islands(self, login_console):
        """Jay asked for them underneath the islands, and that is also the only
        order that keeps the islands -- the point of this screen -- above a pile
        of mail."""
        activity_at = login_console.index('id="ls-activity"')
        notifs_at = login_console.index('id="ls-notifs"')
        assert activity_at < notifs_at

    @pytest.mark.asyncio
    async def test_notifications_are_demo_content_or_nothing(self, monkeypatch):
        """This screen renders BEFORE sign-in. Real mail or a real call log here
        would hand the phone's contents to whoever picked it up, so the route
        serves the scripted table or 404s -- there is no third branch."""
        from tinyagentos.routes import auth as auth_mod

        monkeypatch.setattr(auth_mod, "_request_is_console", lambda _request: True)
        monkeypatch.delenv("TAOS_LOCK_DEMO_AGENTS", raising=False)
        monkeypatch.setenv("TAOS_LOCK_DEMO_NOTIFICATIONS", "1")
        assert (await auth_mod.lock_notifications(None)).status_code == 404

        monkeypatch.setenv("TAOS_LOCK_DEMO_AGENTS", "Demo")
        resp = await auth_mod.lock_notifications(None)
        assert resp.status_code == 200
        assert json.loads(resp.body)["demo"] is True

    @pytest.mark.asyncio
    async def test_the_stacks_are_off_while_the_islands_stay_up(self, monkeypatch):
        """Jay is redesigning the stacks and asked for them hidden meanwhile,
        with the agent islands left alone.

        So the notifications need their own switch, OFF by default: with the
        master demo flag on and nothing else set, the islands still have their
        placeholder agents and this route 404s, which the page renders as no
        stack at all. Asserting the default rather than the opt-in is the point
        -- an unset variable is what a device actually boots with."""
        from tinyagentos.routes import auth as auth_mod

        monkeypatch.setattr(auth_mod, "_request_is_console", lambda _request: True)
        monkeypatch.setenv("TAOS_LOCK_DEMO_AGENTS", "Demo")
        monkeypatch.delenv("TAOS_LOCK_DEMO_NOTIFICATIONS", raising=False)

        assert (await auth_mod.lock_notifications(None)).status_code == 404
        # The islands are deliberately untouched by the same switch.
        assert auth_mod._demo_enabled() is True

    @pytest.mark.asyncio
    async def test_notifications_are_console_only(self, monkeypatch):
        from tinyagentos.routes import auth as auth_mod

        monkeypatch.setenv("TAOS_LOCK_DEMO_AGENTS", "Demo")
        monkeypatch.setenv("TAOS_LOCK_DEMO_NOTIFICATIONS", "1")
        monkeypatch.setattr(auth_mod, "_request_is_console", lambda _request: False)
        assert (await auth_mod.lock_notifications(None)).status_code == 403

    def test_every_source_jay_asked_for_has_a_stack(self):
        from tinyagentos.routes.auth import _demo_notifications

        sources = {group["source"] for group in _demo_notifications()}
        assert {"mail", "x", "reddit", "phone", "sms"} <= sources

    def test_items_are_collated_by_source_not_listed_flat(self):
        """The whole point of the stack: several mails are ONE pile, not three
        banners pushing the islands off the screen."""
        from tinyagentos.routes.auth import _demo_notifications

        groups = {g["source"]: g for g in _demo_notifications()}
        assert len(groups["mail"]["items"]) >= 3

    def test_stacks_and_their_items_are_newest_first(self):
        """A phone orders by arrival. A fixed table order would leave an
        hours-old pile sitting above one that landed a minute ago."""
        from tinyagentos.routes.auth import _demo_notifications

        groups = _demo_notifications()
        tops = [group["items"][0]["at"] for group in groups]
        assert tops == sorted(tops, reverse=True)
        for group in groups:
            ats = [item["at"] for item in group["items"]]
            assert ats == sorted(ats, reverse=True)

    def test_a_stack_is_a_button_not_a_list(self, login_console):
        """Pressing a stack fans it out, so it must be reachable by tab and
        operable by Enter -- the same rule the islands already follow."""
        assert 'el.setAttribute("role", "button")' in LOCK_SCRIPT
        assert 'el.setAttribute("aria-expanded"' in LOCK_SCRIPT
        assert 'id="ls-notifs" role="group"' in login_console

    def test_notification_text_is_never_written_as_markup(self):
        """Titles and bodies are content. The only innerHTML on this path is an
        icon path from the script's own table, keyed by name."""
        assert "title.textContent = item.title" in LOCK_SCRIPT
        assert "text.textContent = item.text" in LOCK_SCRIPT
        assert "innerHTML = item" not in LOCK_SCRIPT

    def test_a_closed_stack_says_how_deep_it_is(self, login_console):
        """Only two cards peek out from behind the top one however many there
        are, so the count is the only thing that can say 'three'."""
        assert ".ls-notif-count" in login_console
        assert "count.textContent = group.items.length" in LOCK_SCRIPT

    def test_a_repaint_does_not_fold_a_stack_the_user_opened(self):
        """The poll rebuilds the stacks. Open state kept inside the rendered
        node would be destroyed by that -- the pile would shut under the user's
        finger every refresh."""
        assert "var notifOpen = {}" in LOCK_SCRIPT
        assert "notifOpen[group.source]" in LOCK_SCRIPT


class TestLockScreenFeedScrollsAsOne:
    def test_one_scroll_region_holds_both_stacks(self, login_console):
        """The agent stack used to be the scrolling element. With a second stack
        under it that is wrong twice: the two would scroll independently, and the
        notifications -- outside the only scrollable box -- would push the
        passcode off the bottom of a full screen instead of scrolling."""
        feed = re.search(r"\.ls-feed\s*\{([^}]*)\}", login_console)
        assert feed and "overflow-y: auto" in feed.group(1)
        agents = re.search(r"\.ls-agents\s*\{([^}]*)\}", login_console)
        assert agents and "overflow" not in agents.group(1), (
            "the agent stack still scrolls on its own -- the two stacks will "
            "slide past each other"
        )

    def test_the_scrolling_box_is_allowed_to_shrink(self, login_console):
        """Without min-height:0 a flex item refuses to shrink below its content,
        so a full feed grows the column and pushes the keypad off-screen instead
        of scrolling."""
        feed = re.search(r"\.ls-feed\s*\{([^}]*)\}", login_console)
        assert feed and "min-height: 0" in feed.group(1)

    def test_a_scrolling_feed_fades_rather_than_slicing_a_card_in_half(self, login_console):
        """With the scrollbar hidden, a feed that simply ends mid-card reads as a
        rendering fault instead of as more content."""
        assert '.ls-feed[data-fade="bottom"]' in login_console
        assert 'feedEl.setAttribute("data-fade", fade)' in LOCK_SCRIPT

    def test_the_fade_is_measured_not_assumed(self, login_console):
        """An unconditional mask eats the bottom of the last card on a device
        with one agent and no notifications, where nothing scrolls."""
        assert re.search(
            r"var over = feedEl\.scrollHeight - feedEl\.clientHeight", LOCK_SCRIPT
        )
        # The plain .ls-feed rule must not carry a mask of its own.
        feed = re.search(r"\.ls-feed\s*\{([^}]*)\}", login_console)
        assert feed and "mask-image" not in feed.group(1)


class TestTheIslandRepaintKeepsKeyboardFocus:
    """The islands poll every 15 seconds and paintActivity rebuilds the list.

    Measured on the handset over CDP before this was fixed: focus an island,
    wait 17s, and document.activeElement had fallen back to the lock screen
    body. Because the six islands and their six mic buttons come BEFORE the
    notification stacks in tab order, a keyboard or switch-access user was
    thrown back to the top every 15 seconds and could never tab far enough to
    reach a stack at all -- while `island()` carries a comment promising it is
    "reachable by tab". The notification stacks polled at 15 MINUTES and kept
    their focus, which is why only half the surface looked broken.
    """

    def _paint_activity(self):
        """The body of paintActivity, which is the function that wipes the list."""
        start = LOCK_SCRIPT.index("function paintActivity(")
        end = LOCK_SCRIPT.index("function pollActivity(", start)
        return LOCK_SCRIPT[start:end]

    def test_focus_is_captured_before_the_wipe_and_restored_after_the_rebuild(self):
        """Ordering is the whole assertion.

        Reading activeElement AFTER `agentsEl.textContent = ""` reads the body,
        because the wipe is what moved focus there -- so a capture in the wrong
        place records nothing and restores nothing while looking correct.
        """
        body = self._paint_activity()
        capture = body.index("document.activeElement")
        wipe = body.index('agentsEl.textContent = ""')
        restore = body.index(".focus()")
        assert capture < wipe, "focus must be read BEFORE the list is wiped"
        assert wipe < restore, "focus must be restored AFTER the list is rebuilt"

    def test_the_island_is_found_again_by_a_stable_key_not_by_position(self):
        """Restoring by index moves focus to a DIFFERENT agent whenever the list
        reorders between polls, which is worse than losing focus: the user's next
        Enter opens an agent they never selected."""
        assert 'el.setAttribute("data-agent", name)' in LOCK_SCRIPT
        body = self._paint_activity()
        assert '.ls-island[data-agent="' in body

    def test_the_mic_button_keeps_focus_rather_than_the_island(self):
        """An island and its mic button are separate tab stops that do different
        things. Restoring the island when the user was on the mic silently
        re-aims the next Enter from 'dictate' to 'open conversation'."""
        body = self._paint_activity()
        assert "focusWasMic" in body
        assert '.ls-mic' in body

    def test_focus_is_not_moved_when_the_agent_is_gone(self):
        """If that agent disappeared, the body is the correct place for focus."""
        body = self._paint_activity()
        assert re.search(r"if \(again\)", body), (
            "restore must be conditional on the element still existing"
        )
