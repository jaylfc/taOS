"""PIN sign-in: the console-origin rule, the escalating throttle, and the store.

The load-bearing tests here are the ones that measure the REFUSING direction.
"PIN is console-only" is a claim about what gets turned away, so a test showing
a correct PIN working on loopback proves nothing about scope — only a correct
PIN being REFUSED off-console does.
"""
from __future__ import annotations

import pytest

from tinyagentos.auth import (
    AuthManager,
    PIN_MAX_LEN,
    PIN_MIN_LEN,
    _PinAttemptLimiter,
    is_console_origin,
    validate_pin,
)


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
            "100.78.225.80",   # tailnet
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

class TestPinStore:
    def test_pin_round_trips(self, mgr):
        mgr.set_pin("tester", "4913")
        assert mgr.has_pin("tester") is True
        assert mgr.check_pin("4913", username="tester")[0] is True

    def test_wrong_pin_refused(self, mgr):
        mgr.set_pin("tester", "4913")
        assert mgr.check_pin("4914", username="tester")[0] is False

    def test_pin_is_hashed_not_stored_in_clear(self, mgr, tmp_path):
        mgr.set_pin("tester", "4913")
        raw = (tmp_path / ".auth_user.json").read_text()
        assert "4913" not in raw
        assert "$argon2" in raw

    def test_pin_hash_never_reaches_the_public_profile(self, mgr):
        mgr.set_pin("tester", "4913")
        pub = mgr.get_primary_user()
        assert "pin_hash" not in pub
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
