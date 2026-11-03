"""PIN sign-in: the console-origin rule, the escalating throttle, and the store.

The load-bearing tests here are the ones that measure the REFUSING direction.
"PIN is console-only" is a claim about what gets turned away, so a test showing
a correct PIN working on loopback proves nothing about scope — only a correct
PIN being REFUSED off-console does.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess

import pytest

from tinyagentos.auth import (
    AuthManager,
    PIN_MAX_LEN,
    PIN_MIN_LEN,
    _PinAttemptLimiter,
    is_console_origin,
    validate_pin,
)
from tinyagentos.routes.auth import _PIN_PANEL_SCRIPT


@pytest.fixture()
def mgr(tmp_path):
    m = AuthManager(tmp_path)
    m.setup_user("tester", "Bring-up Test", "", "correct horse battery staple")
    return m


# --------------------------------------------------------------------------- #
#  R1 — a correct PIN presented from off-console is REFUSED                    #
# --------------------------------------------------------------------------- #

class TestConsoleOriginRefusesRemote:
    """R1. Red against any build where the origin check is absent or loopback-blind."""

    @pytest.mark.parametrize(
        "host",
        [
            "192.168.55.52",   # the pi-top itself, over the LAN
            "192.168.1.10",    # another machine on the home network
            "10.0.0.4",
            "100.64.0.10",    # tailnet
            "8.8.8.8",
            "::1:junk",        # unparseable — must not be read as loopback
            "",
            None,
        ],
    )
    def test_non_loopback_client_is_not_console(self, host):
        assert is_console_origin(host) is False

    @pytest.mark.parametrize(
        "host", ["127.0.0.1", "127.0.0.53", "::1", "localhost", "[::1]", "::ffff:127.0.0.1"]
    )
    def test_loopback_client_is_console(self, host):
        assert is_console_origin(host) is True

    def test_correct_pin_is_useless_without_console_origin(self, mgr):
        """The end-to-end shape of the rule: the PIN is right, the origin is not.

        check_pin deliberately does not consult the origin, so this asserts the
        composition the route layer must implement — the credential verifying is
        NOT sufficient, and the gate is what makes it insufficient.
        """
        mgr.set_pin("tester", "4913")
        ok, record = mgr.check_pin("4913", username="tester")
        assert ok is True and record is not None  # the PIN itself is correct
        assert is_console_origin("192.168.1.10") is False
        # A route that ANDs the two therefore refuses this attempt.
        assert (ok and is_console_origin("192.168.1.10")) is False


# --------------------------------------------------------------------------- #
#  R2 — forwarding headers disqualify, even from loopback                      #
# --------------------------------------------------------------------------- #

class TestForwardedRequestsAreNotConsole:
    """R2. Red against a naive loopback-only implementation.

    Behind a reverse proxy every LAN request reaches the app from 127.0.0.1. A
    loopback-only test would start accepting PINs from the whole network with no
    code change, so the presence of a forwarding header must disqualify.
    """

    @pytest.mark.parametrize(
        "header",
        [
            "x-forwarded-for",
            "x-forwarded-host",
            "x-forwarded-proto",
            "x-real-ip",
            "forwarded",
            "via",
        ],
    )
    def test_loopback_plus_forwarding_header_is_refused(self, header):
        assert is_console_origin("127.0.0.1", {header: "192.168.1.10"}) is False

    def test_spoofed_header_cannot_manufacture_console_access(self):
        """An attacker-supplied header must never UPGRADE a remote request."""
        assert is_console_origin("192.168.1.10", {"x-forwarded-for": "127.0.0.1"}) is False

    def test_empty_forwarding_header_does_not_disqualify(self):
        """Absent-but-present-as-empty is not evidence of a proxy."""
        assert is_console_origin("127.0.0.1", {"x-forwarded-for": ""}) is True

    def test_ordinary_console_request_still_passes(self):
        assert is_console_origin(
            "127.0.0.1", {"user-agent": "Mozilla/5.0", "accept": "text/html"}
        ) is True


# --------------------------------------------------------------------------- #
#  R3 — the throttle escalates AND recovers; it is never permanent             #
# --------------------------------------------------------------------------- #

class TestEscalatingThrottleRecovers:
    """R3. Assert the RECOVERY, not just the refusal.

    A permanent lockout also passes a refusal-only test, and permanent lockout
    is the design Jay rejected — on a keyboard-less screen it re-creates the
    original brick. So every tier here is also shown to expire.
    """

    def test_below_threshold_never_delays(self):
        lim = _PinAttemptLimiter()
        for _ in range(4):
            lim.record_failure("u1", now=1000.0)
        assert lim.retry_after("u1", now=1000.0) == 0

    @pytest.mark.parametrize("failures,expected", [(5, 30), (10, 300), (15, 900), (40, 900)])
    def test_delay_escalates_by_tier_and_caps(self, failures, expected):
        lim = _PinAttemptLimiter()
        for _ in range(failures):
            lim.record_failure("u1", now=1000.0)
        assert lim.retry_after("u1", now=1000.0) == pytest.approx(expected, abs=1)

    @pytest.mark.parametrize("failures,delay", [(5, 30), (10, 300), (15, 900), (40, 900)])
    def test_every_tier_expires_on_its_own(self, failures, delay):
        """The device must always become usable again without a second machine."""
        lim = _PinAttemptLimiter()
        for _ in range(failures):
            lim.record_failure("u1", now=1000.0)
        assert lim.retry_after("u1", now=1000.0) > 0
        assert lim.retry_after("u1", now=1000.0 + delay + 1) == 0

    def test_success_clears_the_penalty(self):
        lim = _PinAttemptLimiter()
        for _ in range(15):
            lim.record_failure("u1", now=1000.0)
        lim.reset("u1")
        assert lim.retry_after("u1", now=1000.0) == 0


# --------------------------------------------------------------------------- #
#  R4 — PIN failures must not touch the password path                          #
# --------------------------------------------------------------------------- #

class TestPinFailuresDoNotLockOutPassword:
    def test_password_still_works_after_exhausting_pin_attempts(self, mgr):
        mgr.set_pin("tester", "4913")
        for _ in range(40):
            assert mgr.check_pin("0000", username="tester")[0] is False
        ok, record = mgr.check_password("correct horse battery staple", username="tester")
        assert ok is True and record is not None

    def test_pin_limiter_is_keyed_per_user(self):
        """Loopback is a single address, so an address key would be one shared
        bucket. Keying by user keeps one account's failures off another's."""
        lim = _PinAttemptLimiter()
        for _ in range(15):
            lim.record_failure("user-a", now=1000.0)
        assert lim.retry_after("user-a", now=1000.0) > 0
        assert lim.retry_after("user-b", now=1000.0) == 0


# --------------------------------------------------------------------------- #
#  Store behaviour                                                             #
# --------------------------------------------------------------------------- #

class TestPinLength:
    def test_set_pin_stores_length(self, mgr):
        mgr.set_pin("tester", "4913")
        data = json.loads((mgr.data_dir / ".auth_user.json").read_text())
        assert data["users"][0].get("pin_length") == 4
        assert mgr.pin_length("tester") == 4

    def test_clear_pin_removes_length(self, mgr):
        mgr.set_pin("tester", "4913")
        mgr.clear_pin("tester")
        data = json.loads((mgr.data_dir / ".auth_user.json").read_text())
        assert "pin_hash" not in data["users"][0]
        assert "pin_length" not in data["users"][0]
        assert mgr.pin_length("tester") is None

    def test_check_pin_learns_length_on_success_if_missing(self, tmp_path):
        m = AuthManager(tmp_path)
        m.setup_user("tester", "Bring-up Test", "", "correct horse battery staple")
        m.set_pin("tester", "123456")
        data = json.loads((tmp_path / ".auth_user.json").read_text())
        data["users"][0].pop("pin_length", None)
        (tmp_path / ".auth_user.json").write_text(json.dumps(data))
        ok, rec = m.check_pin("123456", username="tester")
        assert ok is True
        data2 = json.loads((tmp_path / ".auth_user.json").read_text())
        assert data2["users"][0].get("pin_length") == 6

    def test_failed_check_pin_stores_nothing(self, mgr, tmp_path):
        mgr.set_pin("tester", "123456")
        data = json.loads((tmp_path / ".auth_user.json").read_text())
        data["users"][0].pop("pin_length", None)
        (tmp_path / ".auth_user.json").write_text(json.dumps(data))
        ok, rec = mgr.check_pin("000000", username="tester")
        assert ok is False
        data2 = json.loads((tmp_path / ".auth_user.json").read_text())
        assert "pin_length" not in data2["users"][0]

    def test_pin_length_none_on_multi_user_store(self, mgr, tmp_path):
        # add another user
        code = mgr.add_user_invite("other", "tester")
        mgr.complete_invite("other", code, "Other", "o@o.com", "anotherpass123")
        assert mgr.pin_length(None) is None

    def test_user_listing_has_no_pin_length_key(self, mgr):
        mgr.set_pin("tester", "4913")
        users = mgr.list_users()
        assert users
        assert "pin_length" not in users[0]
        pub = mgr.get_primary_user()
        assert pub and "pin_length" not in pub
        assert pub["has_pin"] is True

    def test_no_pin_configured_refuses_everything(self, mgr):
        assert mgr.has_pin("tester") is False
        assert mgr.check_pin("4913", username="tester")[0] is False
        assert mgr.check_pin("", username="tester")[0] is False

    def test_clear_pin_removes_it(self, mgr):
        mgr.set_pin("tester", "4913")
        assert mgr.clear_pin("tester") is True
        assert mgr.has_pin("tester") is False
        assert mgr.check_pin("4913", username="tester")[0] is False

    def test_pin_does_not_authenticate_as_a_password(self, mgr):
        """The two factors must not be interchangeable."""
        mgr.set_pin("tester", "4913")
        assert mgr.check_password("4913", username="tester")[0] is False

    def test_password_does_not_authenticate_as_a_pin(self, mgr):
        mgr.set_pin("tester", "4913")
        assert mgr.check_pin("correct horse battery staple", username="tester")[0] is False

    def test_multi_user_without_username_refuses_to_guess(self, mgr):
        """A PIN set on one account must not unlock whichever record is first."""
        mgr.add_user_invite("second", invited_by_username="tester")
        mgr.set_pin("tester", "4913")
        assert mgr.check_pin("4913", username=None)[0] is False


class TestPinValidation:
    @pytest.mark.parametrize("bad", ["123", "1" * (PIN_MAX_LEN + 1), "12a4", "12 4", "", "-123"])
    def test_malformed_pins_rejected(self, bad):
        with pytest.raises(ValueError):
            validate_pin(bad)

    @pytest.mark.parametrize("good", ["1" * PIN_MIN_LEN, "4913", "1" * PIN_MAX_LEN])
    def test_well_formed_pins_accepted(self, good):
        assert validate_pin(good) == good

    def test_set_pin_rejects_malformed(self, mgr):
        with pytest.raises(ValueError):
            mgr.set_pin("tester", "12a4")
        assert mgr.has_pin("tester") is False


class TestPinLimiterIsBounded:
    """The limiter map must not grow without limit.

    routes.auth falls back to the key f"unknown:{username}" when no user
    resolves, so a caller cycling usernames would otherwise add a permanent
    entry per attempt for the life of the process -- and a kiosk process runs
    for weeks.
    """

    def test_state_is_capped(self):
        from tinyagentos.auth import _PinAttemptLimiter

        lim = _PinAttemptLimiter()
        for i in range(lim._MAX_KEYS + 500):
            lim.record_failure(f"unknown:user{i}")
        assert len(lim._state) <= lim._MAX_KEYS

    def test_escalation_is_not_reset_by_the_bound(self):
        """The ACTIVE key must survive eviction and keep its failure count.

        Evicting least-recently-failed is what makes this safe; dropping
        entries whose delay has merely elapsed would hand an attacker a free
        reset (wait out the top tier, get five fast guesses again).
        """
        from tinyagentos.auth import _PinAttemptLimiter

        lim = _PinAttemptLimiter()
        for _ in range(5):
            lim.record_failure("victim")
        for i in range(lim._MAX_KEYS + 10):
            lim.record_failure(f"noise{i}")
        lim.record_failure("victim")
        # 6 consecutive failures still sits at or above the first tier.
        assert lim.retry_after("victim") > 0


# --------------------------------------------------------------------------- #
#  Lock page script (node) — PIN auto-unlock behaviour                       #
# --------------------------------------------------------------------------- #

_PIN_HARNESS = r"""
function makeEl(opts) {
  opts = opts || {};
  var el = {
    id: opts.id || null,
    _attrs: opts.attrs || {},
    value: opts.value || "",
    children: opts.children || [],
    _h: {},
    hidden: opts.hidden !== undefined ? opts.hidden : false,
    parentNode: opts.parentNode || null,
    textContent: "",
    addEventListener: function (t, fn) { (this._h[t] = this._h[t] || []).push(fn); },
    setAttribute: function (k, v) { this._attrs[k] = v; },
    getAttribute: function (k) {
      return Object.prototype.hasOwnProperty.call(this._attrs, k) ? this._attrs[k] : null;
    },
    querySelector: function (sel) { return opts.querySelector ? opts.querySelector(sel) : null; },
    closest: function(){ return null; },
    fire: function (t, ev) { ev = ev || {}; var handlers = (this._h[t] || []).slice(); for (var i=0;i<handlers.length;i++){ handlers[i].call(this, ev); } }
  };
  return el;
}

var SCN = JSON.parse(process.env.PIN_SCENARIO || '{}');

var pinPanel = makeEl({ id: "pin-panel", attrs: { "data-next": "/desktop", "data-username": "" } });
if (SCN.pinLength !== undefined && SCN.pinLength !== null) {
  pinPanel.setAttribute("data-pin-length", String(SCN.pinLength));
}

var input = makeEl({ id: "pin-input", value: "" }); input.focus = function(){}; input.replace = function(a,b){ this.value = String(this.value).split(a).join(b); return this.value; };
var dots = makeEl({ id: "pin-dots", children: [] });
var dotCount = SCN.dotCount || (SCN.pinLength || 4);
for (var di=0; di<dotCount; di++) {
  dots.children.push(makeEl({ attrs: { "data-filled": "0" } }));
}
var err = makeEl({ id: "pin-error", textContent: "" });
var submit = null;
if (SCN.hasSubmit) {
  submit = makeEl({ id: "pin-submit" });
}

var els = {
  "pin-panel": pinPanel,
  "pin-input": input,
  "pin-dots": dots,
  "pin-error": err,
  "pin-submit": submit,
  "use-password": makeEl({ id: "use-password" }),
  "use-pin": makeEl({ id: "use-pin" }),
  "pw-panel": makeEl({ id: "pw-panel", hidden: true })
};

var document = {
  getElementById: function(id){ return els[id] || null; },
  readyState: "complete",
  addEventListener: function(){},
  querySelector: function(sel){ if (sel === 'input[type=password]') return input; return null; }
};
var window = { taosOSK: null, location: { assign: function(){} } };

var posts = [];
global.fetch = function(url, opts){
  posts.push({url: url, opts: opts});
  if (SCN.fetchFail) {
    return Promise.reject(new Error("fail"));
  }
  if (SCN.fetchStatus && SCN.fetchStatus !== 200) {
    return Promise.resolve({
      status: SCN.fetchStatus,
      json: function(){ return Promise.resolve(SCN.fetchBody || {}); }
    });
  }
  return Promise.resolve({
    status: 200,
    json: function(){ return Promise.resolve({ok: true}); }
  });
};
global.setTimeout = function(fn){ fn(); return 1; };
global.clearTimeout = function(){};

__PIN_SCRIPT__

if (SCN.typeDigits) {
  for (var i=0; i<SCN.typeDigits.length; i++) {
    input.value += String(SCN.typeDigits[i]);
    var h = input._h["input"] || [];
    for (var j=0; j<h.length; j++) { h[j].call(input, {}); }
  }
}

if (SCN.doEnter && input._h["keydown"]) {
  var kh = input._h["keydown"] || [];
  for (var k=0; k<kh.length; k++) { kh[k].call(input, {key: "Enter", preventDefault: function(){}}); }
}

if (SCN.clickSubmit && submit && submit._h["click"]) {
  var ch = submit._h["click"] || [];
  for (var m=0; m<ch.length; m++) { ch[m].call(submit, {}); }
}

process.stdout.write(JSON.stringify({ posts: posts, err: err.textContent || "", submitExists: !!els["pin-submit"], value: input.value }));
"""


def _run_pin_scenario(scenario: dict, script_override: str | None = None):
    node = shutil.which("node")
    if node is None:
        pytest.fail("node required")
    script = script_override if script_override is not None else _PIN_PANEL_SCRIPT
    harness = _PIN_HARNESS.replace("__PIN_SCRIPT__", script)
    env = os.environ.copy()
    env["PIN_SCENARIO"] = json.dumps(scenario)
    proc = subprocess.run([node, "-e", harness], stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=env, timeout=10)
    if proc.returncode != 0:
        raise AssertionError(proc.stderr.decode())
    return json.loads(proc.stdout.decode())


class TestPinPanelScriptNode:
    def test_with_data_pin_length_six_typing_five_sends_no_post_sixth_sends_exactly_one(self):
        res = _run_pin_scenario({"pinLength": 6, "hasSubmit": False, "dotCount": 6, "typeDigits": [1,2,3,4,5]})
        assert len(res["posts"]) == 0
        res = _run_pin_scenario({"pinLength": 6, "hasSubmit": False, "dotCount": 6, "typeDigits": [1,2,3,4,5,6]})
        assert len(res["posts"]) == 1

    def test_wrong_pin_clears_dots_and_second_full_entry_posts_second(self):
        res = _run_pin_scenario({"pinLength": 4, "hasSubmit": False, "dotCount": 4, "typeDigits": [1,2,3,4], "fetchStatus": 401, "fetchBody": {"error": "bad"}})
        assert len(res["posts"]) == 1
        res2 = _run_pin_scenario({"pinLength": 4, "hasSubmit": False, "dotCount": 4, "typeDigits": [5,6,7,8], "fetchStatus": 401, "fetchBody": {"error": "bad"}})
        assert len(res2["posts"]) == 1

    def test_no_data_pin_length_button_exists_and_typing_never_auto_posts(self):
        res = _run_pin_scenario({"pinLength": None, "hasSubmit": True, "dotCount": 4, "typeDigits": [1,2,3,4]})
        assert len(res["posts"]) == 0
        assert res["submitExists"] is True

    def test_mutation_try_pin_fires_at_length_four_always_five_no_post_goes_red(self):
        mutated = _PIN_PANEL_SCRIPT.replace('if (pinLength && input.value.length === pinLength) {\n      tryPin();\n    }',
                                             'if (input.value.length === 4) {\n      tryPin();\n    }')
        res = _run_pin_scenario({"pinLength": 6, "hasSubmit": False, "dotCount": 6, "typeDigits": [1,2,3,4,5]}, script_override=mutated)
        assert len(res["posts"]) >= 1
