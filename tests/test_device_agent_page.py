"""The lock screen's half of a device agent, driven rather than grepped.

Two properties, both of which a source-text assertion would only pretend to
check: a plugged-in board gets its own island key, and its replies are
APPENDED as they arrive rather than repainted.

The second is the flicker lesson this screen has already learned once: a
poller that rebuilds its container every tick makes every row a new node, and
every row then replays its entrance animation. A device thread polls every 2s
while `apt update` runs, so it is the worst possible place to relearn it.
"""
from __future__ import annotations

import json
import subprocess
import tempfile
from pathlib import Path

import pytest

from tinyagentos.routes import auth
from test_lock_screen_gestures import _function
from test_lock_screen_views import _node
# The REPAINT harness, not the views one: this file is about which nodes
# survive an append, so it needs a stand-in with a real tree (appendChild,
# children) rather than one built from a kids list at construction.
from test_lock_screen_repaint import _DOM, _var


def _run(program: str) -> dict:
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "device.js"
        path.write_text(program, encoding="utf-8")
        proc = subprocess.run(
            [_node(), str(path)], capture_output=True, text=True, timeout=60
        )
    assert proc.returncode == 0, proc.stderr[-2000:]
    return json.loads(proc.stdout.strip().splitlines()[-1])


class TestADeviceIslandHasItsOwnKey:
    def test_the_payloads_key_wins_over_the_display_name(self):
        """A board called taOSusb and a TAOS_LOCK_DEMO_AGENTS entry of the
        same name must never land on one island: the list reconciles by
        data-agent, so a shared value hands each poll's payload to the other's
        node."""
        # The same source set the repaint suite feeds island(): it needs
        # RESTING, the avatar helpers and setAttrIfChanged, and assembling a
        # shorter list here would only test how far I got.
        src = "\n".join([
            _var("FRAMEWORKS"), _var("RESTING"),
            _function("hueFor"), _function("initials"),
            _function("islandIdentity"), _function("applyAgent"),
            _function("setAttrIfChanged"), _function("island"),
        ])
        out = _run(_DOM + src + r"""
var document = { createElement: makeNode };
var a = island({ name: "taOSusb", key: "device:taosusb", status: "Online · USB" });
var b = island({ name: "taOSusb", status: "running" });
console.log(JSON.stringify({
  device: a.getAttribute("data-agent"),
  demo: b.getAttribute("data-agent")
}));
""")
        assert out["device"] == "device:taosusb"
        assert out["demo"] == "taOSusb"
        assert out["device"] != out["demo"]


class TestRepliesAreAppendedNotRepainted:
    HARNESS = _DOM + r"""
var msgsEl = makeNode("div");
var screenEl = makeNode("div");
screenEl.setAttribute("data-sheet", "chat");
var chatSub = makeNode("div");
var composer = makeNode("input");
var sendBtn = makeNode("button");
var NODE_ID = 0;
function bubble(msg) {
  var el = makeNode("div");
  el.__id = ++NODE_ID;
  el.__role = msg.role;
  el.__text = msg.text;
  return el;
}
function slugFor(s) { return String(s).toLowerCase(); }
"""

    def test_a_message_already_on_screen_is_not_drawn_again(self):
        """The endpoint returns the WHOLE thread every poll. Without the seen
        map every tick would re-add every bubble."""
        out = _run(self.HARNESS + _function("threadSlug")
                   + _function("paintDeviceMessages") + r"""
var deviceSeen = {};
var thread = [
  { id: "a", seq: 0, role: "user",  text: "do a health check" },
  { id: "a", seq: 1, role: "agent", text: "Filesystem  Size  Used" }
];
paintDeviceMessages(thread);
var first = msgsEl.children.map(function (c) { return c.__id; });
// The same poll again, plus one new line -- exactly what the next tick sees.
thread.push({ id: "a", seq: 2, role: "agent", text: "/dev/root   29G   4G" });
paintDeviceMessages(thread);
console.log(JSON.stringify({
  first: first,
  after: msgsEl.children.map(function (c) { return c.__id; }),
  texts: msgsEl.children.map(function (c) { return c.__text; })
}));
""")
        # Every node that was there before is the SAME node, in order.
        assert out["after"][:len(out["first"])] == out["first"]
        assert len(out["after"]) == 3
        assert out["texts"][-1].startswith("/dev/root")

    def test_output_is_monospace_but_what_the_user_typed_is_not(self):
        """df, free and ip speak in columns; the user's own words do not."""
        out = _run(self.HARNESS + _function("threadSlug")
                   + _function("paintDeviceMessages") + r"""
var deviceSeen = {};
paintDeviceMessages([
  { id: "a", seq: 0, role: "user",  text: "health check" },
  { id: "a", seq: 1, role: "agent", text: "cols" }
]);
console.log(JSON.stringify(msgsEl.children.map(function (c) {
  return [c.__role, c.getAttribute("data-mono")];
})));
""")
        assert out[0] == ["user", None]
        assert out[1] == ["agent", "1"]

    def test_the_devices_opening_line_is_not_eaten_by_the_users_own_message(self):
        """/auth/lock-send writes the user's text under the id it minted with
        seq 0, and @taOS-dev's device sends its first message for that same id
        ALSO at seq 0 ("working on it…"). Keyed on id:seq alone the second is
        taken for a duplicate of the first and dropped, so the sheet sits on
        the question with no sign the board heard it.

        Found by reading their protocol note, not by running it -- which is
        why it is pinned here.
        """
        out = _run(self.HARNESS + _function("threadSlug")
                   + _function("paintDeviceMessages") + r"""
var deviceSeen = {};
paintDeviceMessages([
  { id: "abc", seq: 0, role: "user",  text: "do a health check" },
  { id: "abc", seq: 0, role: "agent", text: "working on it…" }
]);
console.log(JSON.stringify(msgsEl.children.map(function (c) {
  return [c.__role, c.__text];
})));
""")
        assert len(out) == 2, out
        assert out[0][0] == "user" and out[1][0] == "agent"
