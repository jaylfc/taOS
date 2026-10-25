"""The kiosk must never show that taOS runs inside chromium.

Jay 2026-10-11: chromium asked whether taOS may send notifications, and the
swipe-back animation played. Two tells: a permission prompt for taOS's own
origin, and overscroll history navigation in a window launched without the
flag that turns it off.
"""
import json
import pathlib
import re

ROOT = pathlib.Path(__file__).resolve().parents[1]
POLICY = ROOT / "kiosk/etc/chromium-policy-taos-kiosk.json"
TAOS_ORIGIN = "http://127.0.0.1:6969"

# Every file that starts a chromium window on the handset.
LAUNCHERS = [
    ROOT / "kiosk/bin/taos-kiosk-launch",
    ROOT / "apps/taos-app-launch",
    ROOT / "shell/taos-shelld.py",
]


def _chromium_launches(text):
    """Each chromium command line in *text*, continuation lines joined.

    A launch runs from a line naming chromium with --app (or the kiosk's
    BROWSER_ARGS block) to the line holding --app=, so flags before and after
    the binary both count.
    """
    joined = re.sub(r"\\\n", " ", text)
    joined = re.sub(r'"\s*\n\s*"', " ", joined)  # python string concatenation
    return [line for line in joined.splitlines() if "--app=" in line]


def test_policy_grants_taos_its_own_permissions_without_a_prompt():
    policy = json.loads(POLICY.read_text())
    for key in ("NotificationsAllowedForUrls",
                "AudioCaptureAllowedUrls",
                "VideoCaptureAllowedUrls"):
        assert TAOS_ORIGIN in policy.get(key, []), key


def test_every_launch_turns_off_swipe_back():
    seen = 0
    for path in LAUNCHERS:
        launches = _chromium_launches(path.read_text())
        assert launches, "no chromium launch found in %s" % path
        for line in launches:
            seen += 1
            assert "--overscroll-history-navigation=0" in line, (path.name, line.strip())
    assert seen >= len(LAUNCHERS)
