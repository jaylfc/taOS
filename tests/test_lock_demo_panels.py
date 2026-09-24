"""The five scripted lock-screen panels: phone, mailbox, apps, projects, decisions.

Jay, from the glass: "we need demo data for the other lock screen categories
too" -- the view row shipped seven tabs and only three of them had anything
behind them. He then specified the content of every one: missed calls from the
dialer, WhatsApp Business and an agent's Twilio line plus a voicemail; a unified
BlackBerry-Hub-style mailbox mixing email, SMS, X DMs and LinkedIn; Instagram,
Reddit, a bank and YouTube; and pending approvals. He then, from the glass,
swapped the settings tab for PROJECTS ("makes sense as its a projects focused
os"), moved decisions out of a tab of their own and into the top of ALERTS
("thats were decisions will go for quick answering"), and fixed the tab order.

Two properties matter here and they pull in opposite directions.

**The screen renders BEFORE sign-in.** Anyone who picks the phone up sees it.
So every line of this content is scripted and server-side, gated behind a demo
flag, and there is no code path from any of it to a real account. A mailbox
panel wired to the user's actual inbox would be a pre-auth leak, not a feature,
and Jay's "email, SMS, X DMs, LinkedIn" list is exactly the shape that invites
that mistake. The tests below assert the absence of that path, not just the
presence of the content.

**Every panel is a poller, and the last bug on this screen was every poller
that wipes its container and rebuilds.** That is BUG 2b: `paintActivity()`
emptied `#ls-agents` on each 15s tick, so every island was a new node and
replayed its 520ms entrance animation. Five more panels built the same way
would have been the same bug five more times. They are reconciled by key from
the first line instead, and -- exactly as in `test_lock_screen_repaint.py` --
**these tests assert on NODE IDENTITY, not on rendered values.** "The names are
still right" passes on the broken code too; the broken code always rebuilt the
list correctly, that was the whole problem.

`test_the_harness_observes_the_defect` puts the wipe back and requires identity
to break. Without that control, a painter that quietly did nothing would report
every property below as satisfied.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess

import pytest

import tinyagentos.routes.auth as auth
from tinyagentos.auth_middleware import EXEMPT_PATHS
from tinyagentos.routes.auth import _LOCK_SCREEN_SCRIPT as LOCK_SCRIPT

from test_lock_screen_gestures import _function
from test_lock_screen_repaint import _DOM, _var


# ---------------------------------------------------------------- the server


class TestTheContentIsScriptedAndGated:
    """The pre-sign-in constraint, asserted rather than commented."""

    def test_the_master_flag_alone_does_not_turn_the_panels_on(self, monkeypatch):
        """Two flags, like the notification stacks.

        The point of the second flag is that the master one stays the single
        move that takes down EVERYTHING invented on this screen. A device can
        run the agent islands -- the part that shows real state -- with none of
        the scripted inbox content beside them.
        """
        monkeypatch.setenv("TAOS_LOCK_DEMO_AGENTS", "a,b")
        monkeypatch.delenv("TAOS_LOCK_DEMO_PANELS", raising=False)
        assert auth._demo_panels_enabled() is False

    def test_the_panel_flag_alone_does_not_turn_the_panels_on(self, monkeypatch):
        """And the second flag cannot REPLACE the master one.

        Were this to pass, switching off TAOS_LOCK_DEMO_AGENTS would leave a
        phone showing invented mail while believing it was in its real state.
        """
        monkeypatch.delenv("TAOS_LOCK_DEMO_AGENTS", raising=False)
        monkeypatch.setenv("TAOS_LOCK_DEMO_PANELS", "1")
        assert auth._demo_panels_enabled() is False

    def test_both_flags_together_turn_them_on(self, monkeypatch):
        """The positive control. Without it the two tests above are also what a
        function that returned False unconditionally would produce."""
        monkeypatch.setenv("TAOS_LOCK_DEMO_AGENTS", "a,b")
        monkeypatch.setenv("TAOS_LOCK_DEMO_PANELS", "1")
        assert auth._demo_panels_enabled() is True

    def test_the_route_is_exempt_from_auth(self):
        """The lock screen fetches this BEFORE sign-in, so it must be exempt.

        `/auth/lock-stats` shipped in #3103 without this and would have 401'd on
        the glass -- the stats panel was empty for exactly that reason. One line
        of registration, one whole panel, no error anywhere.
        """
        assert "/auth/lock-panels" in EXEMPT_PATHS

    def test_every_item_in_every_panel_is_marked_demo(self):
        """Marked at construction, so nothing downstream has to work out that
        these are placeholders by elimination."""
        panels = auth._demo_panels()
        assert set(panels) == {"phone", "mailbox", "apps", "projects", "decisions"}
        for name, items in panels.items():
            assert items, f"{name} is empty"
            for item in items:
                assert item["demo"] is True, (name, item)

    def test_no_panel_item_carries_a_route_to_a_real_account(self):
        """The pre-auth constraint, as a property of the payload.

        A URL, an address, an account id or a message id is the shape a real
        inbox leaks through: it is what a "helpful" later change would add to
        make a row openable. There is nothing to open. This asserts the SHAPE of
        what is served rather than a list of bad values, because the values a
        leak would carry are exactly the ones nobody thought to enumerate.
        """
        allowed = {
            "key", "demo", "at", "app", "who", "detail", "kind", "glyph",
            "mono", "tint", "source", "subject", "preview", "unread",
            "badge", "note", "title", "agent",
            "name", "progress", "agents", "blocked",
        }
        for name, items in auth._demo_panels().items():
            for item in items:
                extra = set(item) - allowed
                assert not extra, f"{name} item {item['key']} carries {extra}"

    def test_the_scripted_tables_are_the_only_source(self):
        """`_demo_panels()` reads module constants and the clock. Nothing else.

        If this ever needs relaxing, that is the moment to re-read the rule at
        the top of this file: the change that breaks it is the change that wires
        a pre-sign-in screen to a real inbox.
        """
        import inspect

        src = inspect.getsource(auth._demo_panels)
        for forbidden in ("request", "await", "open(", "fetch", "session", "db", "sql"):
            assert forbidden not in src.lower(), forbidden

    @pytest.mark.parametrize("panel", ["phone", "mailbox", "apps", "projects", "decisions"])
    def test_every_key_within_a_panel_is_unique(self, panel):
        """Keys are what the client reconciles on. Two rows sharing one would
        make the second permanently overwrite the first -- and it would look
        like a content bug, not a keying bug."""
        keys = [item["key"] for item in auth._demo_panels()[panel]]
        assert len(keys) == len(set(keys)), keys


class TestJaysContent:
    """Jay specified these by name. A panel that renders beautifully without the
    thing he asked for is still not the thing he asked for."""

    def test_the_phone_panel_names_every_source_jay_asked_for(self):
        """Dialer, WhatsApp Business, an agent's Twilio line, and a voicemail."""
        items = auth._demo_panels()["phone"]
        apps = {item["app"] for item in items}
        assert "Phone" in apps, apps
        assert "WA+" in apps, apps
        assert "Twilio" in apps, apps
        assert any(item["kind"] == "voicemail" for item in items), items
        assert any(item["kind"] == "missed" for item in items), items

    def test_the_twilio_call_is_an_agents_line(self):
        """The one entry that is about the product rather than the person: an
        agent holds a number and something rang it. That is the demo's point,
        and a generic missed call in its place would lose it."""
        twilio = [i for i in auth._demo_panels()["phone"] if i["app"] == "Twilio"]
        assert twilio, "the agent's Twilio line is gone"
        assert "agent" in twilio[0]["who"].lower(), twilio[0]

    def test_no_demo_number_can_reach_a_real_subscriber(self):
        """Ofcom reserves 07700 900xxx for drama. A plausible-looking number
        that is not in that range is somebody's actual phone."""
        import re

        for item in auth._demo_panels()["phone"]:
            for number in re.findall(r"0\d[\d ]{7,}", item.get("detail", "")):
                digits = number.replace(" ", "")
                assert digits.startswith("07700900"), (item["key"], number)

    def test_the_mailbox_is_unified_not_email_only(self):
        """Jay: "like blackberry's unified messaging system" -- mail AND SMS AND
        X DMs AND LinkedIn in ONE stream. An email-only list with a nice header
        is the thing he specifically did not ask for."""
        sources = {item["source"] for item in auth._demo_panels()["mailbox"]}
        assert sources == {"mail", "sms", "x", "linkedin"}, sources

    def test_the_mailbox_is_one_stream_ordered_by_arrival(self):
        """Not grouped by source. A unified inbox that sorts into four blocks is
        four inboxes on one screen."""
        items = auth._demo_panels()["mailbox"]
        ats = [item["at"] for item in items]
        assert ats == sorted(ats, reverse=True), ats
        # And the order genuinely interleaves -- a table that happened to be
        # authored source-by-source would satisfy the sort and fail the point.
        runs = [item["source"] for item in items]
        assert len(set(runs[:3])) == 3, runs

    def test_every_message_says_where_it_came_from(self):
        """The per-item source is the design, not decoration: a unified list
        that does not name each line's origin is just a worse inbox."""
        for item in auth._demo_panels()["mailbox"]:
            assert item["app"], item

    def test_the_apps_panel_carries_the_four_apps_jay_named(self):
        """A superset is fine -- he asked for more content, not fewer apps --
        but his four are the ones he named and must all be there."""
        apps = {item["app"] for item in auth._demo_panels()["apps"]}
        assert {"Instagram", "Reddit", "Bank", "YouTube"} <= apps, apps

    def test_the_bank_tile_shows_no_balance(self):
        """The one genuinely sensitive-looking line on a pre-auth screen. The
        tile says a payment needs a look; it does not say how much is there."""
        bank = [i for i in auth._demo_panels()["apps"] if i["app"] == "Bank"][0]
        assert "£" not in bank["note"], bank
        assert not any(ch.isdigit() for ch in bank["note"]), bank

    def test_decisions_name_the_agent_that_is_blocked(self):
        """These rows are the lock screen's reason to exist: an agent got far
        enough to need a human and stopped. Without the agent the panel is a
        to-do list."""
        items = auth._demo_panels()["decisions"]
        assert items
        for item in items:
            assert item["agent"], item
            assert item["title"] and item["detail"], item

    def test_every_project_says_how_far_along_and_who_is_on_it(self):
        """A project is a body of work with agents on it. Without the progress
        and the agent count it is a bookmark."""
        items = auth._demo_panels()["projects"]
        assert items
        for item in items:
            assert item["name"] and item["note"], item
            assert 0 <= item["progress"] <= 100, item
            assert item["agents"] >= 1, item

    def test_blocked_projects_sort_above_everything_else(self):
        """Work that has stopped and is waiting on a person is the reason this
        panel is on a LOCK screen -- and going quiet is exactly what would sink
        it to the bottom of a pure recency sort."""
        items = auth._demo_panels()["projects"]
        blocked = [i for i in items if i["blocked"]]
        assert blocked, "nothing is blocked, so this proves nothing"
        assert all(i["blocked"] for i in items[:len(blocked)]), [
            (i["key"], i["blocked"]) for i in items
        ]
        # And within the blocked run, still newest-first.
        ats = [i["at"] for i in blocked]
        assert ats == sorted(ats, reverse=True), ats

    def test_the_projects_panel_carries_no_actions(self):
        """It replaced a panel of pre-auth ACTIONS -- "stop all agents",
        reachable by anyone holding the phone. Swapping it for read-only content
        removed that exposure; an action creeping back in here would restore it
        without anyone deciding to.
        """
        for item in auth._demo_panels()["projects"]:
            assert "kind" not in item, item
            assert "action" not in item, item

    def test_the_lock_screen_has_no_settings_tab_at_all(self):
        """Jay swapped it for projects. The tab going but the panel staying
        would leave the actions on the page, just harder to reach."""
        keys = [key for key, _l, _i, _p in auth._LOCK_VIEWS]
        assert "settings" not in keys, keys
        html = auth._lock_head_html()
        assert "ls-settings" not in html
        assert "lv-settings" not in auth._VIEW_SPRITE

    def test_the_tab_order_is_the_one_jay_gave(self):
        """"the icon order on lockscreen should be agents, projects, alerts,
        mailbox, phone, stats" -- then apps, which he asked to keep but did not
        place. Asserted as the whole list, in order: a membership check would
        pass on any shuffle of it, and the order IS the ask.
        """
        keys = [key for key, _l, _i, _p in auth._LOCK_VIEWS]
        assert keys == ["agents", "projects", "alerts", "mailbox",
                        "phone", "stats", "apps"], keys

    def test_decisions_are_not_a_tab_but_live_inside_alerts(self):
        """Jay: "i meant alerts not decisions (but thats were decisions will go
        for quick answering)". So the decisions container is a child of the
        alerts panel, not a panel of its own -- and if it ever became one, the
        answering would move off the screen he put it on.
        """
        keys = [key for key, _l, _i, _p in auth._LOCK_VIEWS]
        assert "decisions" not in keys, keys
        html = auth._lock_head_html()
        alerts = html.index('id="ls-notifs"')
        decisions = html.index('id="ls-decisions"')
        closing = html.index("</div>", alerts)
        assert alerts < decisions < closing, "decisions is not inside the alerts panel"

    def test_every_view_with_scripted_content_has_a_panel_in_the_markup(self):
        """The failure this catches is a tab that opens onto nothing."""
        html = auth._lock_head_html()
        for key in ("phone", "mailbox", "apps", "projects"):
            panel = [p for k, _l, _i, p in auth._LOCK_VIEWS if k == key][0]
            assert f'id="{panel}"' in html, key
            assert f'aria-labelledby="ls-tab-{key}"' in html, key


class TestTheRoute:
    """The gate, exercised rather than read."""

    @staticmethod
    def _call(monkeypatch, *, console=True, agents="a,b", panels="1"):
        import asyncio

        monkeypatch.setattr(auth, "_request_is_console", lambda _r: console)
        if agents is None:
            monkeypatch.delenv("TAOS_LOCK_DEMO_AGENTS", raising=False)
        else:
            monkeypatch.setenv("TAOS_LOCK_DEMO_AGENTS", agents)
        if panels is None:
            monkeypatch.delenv("TAOS_LOCK_DEMO_PANELS", raising=False)
        else:
            monkeypatch.setenv("TAOS_LOCK_DEMO_PANELS", panels)
        return asyncio.run(auth.lock_panels(object()))

    def test_a_non_console_request_is_refused(self, monkeypatch):
        """Same rule as every other lock-screen endpoint: this screen is the
        device's own glass, and a LAN browser is not it."""
        assert self._call(monkeypatch, console=False).status_code == 403

    def test_the_demo_flags_off_is_a_404_not_an_empty_payload(self, monkeypatch):
        """404 so the client leaves the panels alone and they render their own
        "nothing here". An empty payload would be a claim that the user has no
        mail, which is a different and wrong statement."""
        assert self._call(monkeypatch, panels=None).status_code == 404

    def test_the_flags_on_serve_the_panels(self, monkeypatch):
        """The positive control for the two refusals above."""
        resp = self._call(monkeypatch)
        assert resp.status_code == 200
        body = json.loads(bytes(resp.body))
        assert body["demo"] is True
        assert set(body) == {"phone", "mailbox", "apps", "projects", "decisions", "demo"}
        assert body["phone"] and body["mailbox"] and body["apps"]
        assert body["decisions"] and body["projects"]


# ----------------------------------------------------------- the client paint

#: Listener recording and `closest()`, which the shared stand-in does not carry.
#: The settings switches and the decision buttons are the first things on this
#: screen the user OPERATES rather than reads, so a harness that cannot deliver
#: a click cannot see whether an answer survives the next repaint -- which is
#: the property that matters, since a repaint lands every 15 minutes regardless
#: of what the user is in the middle of.
_EVENTS = r"""
var __realMake = makeNode;
makeNode = function (tag) {
  var el = __realMake(tag);
  el.__handlers = {};
  // A style store that can be READ BACK. The shared stand-in's setProperty is
  // a no-op returning undefined, and paintTile asks what the tint currently is
  // before writing it -- against the no-op that is a TypeError, and had it
  // merely returned undefined every tile would have been rewritten on every
  // paint while the identity assertions still passed.
  el.style = {
    _p: {},
    setProperty: function (k, v) { this._p[k] = String(v); },
    getPropertyValue: function (k) {
      return Object.prototype.hasOwnProperty.call(this._p, k) ? this._p[k] : "";
    }
  };
  el.addEventListener = function (t, fn) {
    (this.__handlers[t] = this.__handlers[t] || []).push(fn);
  };
  el.closest = function (sel) {
    var want = sel.replace(/^\./, ""), n = this;
    while (n) {
      if (String(n.className).split(/\s+/).indexOf(want) !== -1) return n;
      n = n.parent;
    }
    return null;
  };
  return el;
};
// createElement captured the ORIGINAL makeNode when the stand-in was built, so
// reassigning the name alone would have left every element the painters create
// without a listener store -- and every click test silently doing nothing.
document.createElement = makeNode;

function fire(target, type) {
  var n = target;
  while (n) {
    var hs = n.__handlers && n.__handlers[type];
    if (hs) {
      for (var i = 0; i < hs.length; i++) {
        hs[i]({ target: target, preventDefault: function () {} });
      }
    }
    n = n.parent;
  }
}

function findPart(el, key) {
  for (var i = 0; i < el.children.length; i++) {
    var c = el.children[i];
    if (c.getAttribute("data-part") === key) return c;
    var deep = findPart(c, key);
    if (deep) return deep;
  }
  return null;
}
function partText(el, key) {
  var n = findPart(el, key);
  return n ? n.textContent : null;
}
function findClass(el, cls) { return el.querySelector("." + cls); }
"""

_PANEL_STATE = r"""
var panelEls = {
  phone: makeNode("div"), mailbox: makeNode("div"), apps: makeNode("div"),
  projects: makeNode("div"), decisions: makeNode("div")
};
// Server-rendered HIDDEN, exactly as _lock_head_html() emits them. The harness
// used to create them visible, which is why 64 tests passed while every panel
// was display:none on the real glass.
panelEls.phone.hidden = true;
panelEls.mailbox.hidden = true;
panelEls.apps.hidden = true;
panelEls.projects.hidden = true;
// The decisions container lives INSIDE the alerts panel on the real page, so
// the harness gives it the same home -- otherwise the attach/detach the
// painter performs would have nothing to attach to and would silently no-op.
var notifsEl = makeNode("div");
notifsEl.setAttribute("id", "ls-notifs");
panelEls.decisions.setAttribute("id", "ls-decisions");
notifsEl.appendChild(panelEls.decisions);

// State the real notification painter reads. screenEl null means "no sheet is
// open", which is the state a poll normally lands in.
var screenEl = null;
var notifOpen = {};
var notifClocks = [];

// A real id lookup rather than a map to the node we happen to want. The roots
// deliberately EXCLUDE the decisions container itself: once a notification
// paint has removed it from the panel it is detached, and a detached node is
// not findable by getElementById in a browser either. A stub that handed it
// back regardless would hide exactly the failure this file is here to catch.
var __roots = [notifsEl, panelEls.phone, panelEls.mailbox,
               panelEls.apps, panelEls.projects];
document.getElementById = function (id) {
  for (var i = 0; i < __roots.length; i++) {
    if (__roots[i].getAttribute("id") === id) return __roots[i];
    var found = (function walk(n) {
      for (var j = 0; j < n.children.length; j++) {
        if (n.children[j].getAttribute("id") === id) return n.children[j];
        var deep = walk(n.children[j]);
        if (deep) return deep;
      }
      return null;
    })(__roots[i]);
    if (found) return found;
  }
  return null;
};
var panelClocks = [];
var decAnswered = {};
var lastPanels = {};
function syncFeedFade() {}
"""

#: The defect put back: empty every panel, then paint. The rendered values come
#: out identical -- that is the point, and why identity is the only thing that
#: can tell the two apart.
_WIPE = r"""
var __reconciled = paintPanels;
paintPanels = function (data) {
  for (var k in panelEls) { if (panelEls[k]) panelEls[k].textContent = ""; }
  return __reconciled(data);
};
"""

_SNAPSHOT = r"""
function snapshot() {
  var out = {};
  for (var k in panelEls) {
    // The apps panel nests its tiles in a grid; everything else is rows
    // directly under the panel.
    var host = panelEls[k];
    if (k === "apps" && host.children.length
        && String(host.children[0].className).indexOf("ls-apps-grid") !== -1) {
      host = host.children[0];
    }
    out[k] = host.children.map(function (el) {
      var actions = findPart(el, "actions");
      return {
        id: el.__id,
        key: el.getAttribute("data-part"),
        cls: el.className,
        title: partText(el, "title"),
        subject: partText(el, "subject"),
        sub: partText(el, "sub"),
        app: partText(el, "app"),
        when: partText(el, "when"),
        mark: (findPart(el, "tile") || {}).textContent,
        badge: partText(el, "badge"),
        name: partText(el, "name"),
        note: partText(el, "note"),
        label: partText(el, "label"),
        done: partText(el, "done"),
        unread: el.getAttribute("data-unread"),
        kind: el.getAttribute("data-kind"),
        answered: el.getAttribute("data-answered"),
        flag: partText(el, "flag"),
        pct: (findPart(el, "bar") || { getAttribute: function () { return null; } })
          .getAttribute("aria-valuenow"),
        handlers: actions && actions.__handlers.click
          ? actions.__handlers.click.length : null
      };
    });
  }
  out.__clocks = panelClocks.length;
  // Where the decisions container actually IS. Painted into a detached node it
  // would be correct, complete and invisible.
  out.__hidden = {};
  for (var h in panelEls) out.__hidden[h] = !!panelEls[h].hidden;
  out.__decisions_parented = panelEls.decisions.parent === notifsEl;
  out.__decisions_first = notifsEl.children[0] === panelEls.decisions;
  return out;
}

// Click the first control matching a class inside a named panel row.
function clickIn(panel, rowKey, cls) {
  var host = panelEls[panel];
  var row = findPart(host, rowKey);
  if (!row) throw new Error("no row " + rowKey + " in " + panel);
  var btn = findClass(row, cls);
  if (!btn) throw new Error("no ." + cls + " in " + rowKey);
  fire(btn, "click");
  return btn;
}

"""

_TICKS = r"""
var SCN = JSON.parse(process.env.LS_PANELS);
var snapshots = [];
for (var t = 0; t < SCN.ticks.length; t++) {
  paintPanels(SCN.ticks[t]);
  var acts = (SCN.clicks || {})[String(t)] || [];
  for (var a = 0; a < acts.length; a++) {
    clickIn(acts[a][0], acts[a][1], acts[a][2]);
  }
  // The two painters run on independent timers. Driving a notification paint
  // AFTER the panels is the order that deletes the decisions, so it is the
  // order worth driving.
  if (SCN.notifyAfter) paintNotifications({ groups: SCN.groups });
  snapshots.push(snapshot());
}
process.stdout.write(JSON.stringify(snapshots));
"""

_DRIVER = _SNAPSHOT + _TICKS


#: THE POLL, not the painter. Everything above drives `paintPanels` with a
#: payload; this drives `pollPanels` with a RESPONSE, because the bug that
#: reached the glass lived in the branch between the two -- on a device with
#: the demo flags off the route 404s, and the client skipped the paint.
#:
#: `paintPanels` is the only thing that clears the markup's `hidden`, so
#: skipping it left all four panels not empty but blank. Every assertion in
#: this file passed while that was true: they all start from a 200.
_POLL_DRIVER = r"""
var RESP = JSON.parse(process.env.LS_POLL);

// The response the device actually gets, modelled at the fetch boundary rather
// than by calling paintPanels differently -- the branch under test is inside
// pollPanels, so a harness that reached past it would test nothing.
function fetch(url, opts) {
  __FETCHED__.push(url);
  if (RESP.rejects) return Promise.reject(new Error("no network"));
  return Promise.resolve({
    ok: !!RESP.ok,
    status: RESP.status,
    json: function () { return Promise.resolve(RESP.body); }
  });
}
var __FETCHED__ = [];

pollPanels();

// The whole chain is microtasks, and node drains those before any timer runs,
// so one zero-delay macrotask lands after the last .then.
setTimeout(function () {
  var out = snapshot();
  out.__fetched = __FETCHED__.length;
  process.stdout.write(JSON.stringify(out));
}, 0);
"""

#: The defect put back: the not-ok branch skips the paint. Rendered output is
#: identical on the 200 path -- which is why nothing here caught it.
_SKIP_ON_NOTHING = r"""
pollPanels = function () {
  fetch("/auth/lock-panels", { credentials: "same-origin" })
    .then(function (r) { return r.ok ? r.json() : null; })
    .then(function (d) { if (d) paintPanels(d); })
    .catch(function () {});
};
"""


def _panel_source(*, reconciled: bool = True) -> str:
    """The SHIPPED painters, lifted out of the served script.

    Extracted rather than re-typed for the reason the repaint suite gives: a
    copy of the code under test is a test of the copy.
    """
    parts = [
        _var("NOTIF_GLYPHS"),
        _function("placeInOrder"),
        _function("partOf"),
        _function("setText"),
        _function("setAttrIfChanged"),
        _function("whenText"),
        _function("paintTile"),
        _function("paintRowHead"),
        _function("paintEmpty"),
        _function("paintPhone"),
        _function("paintMailbox"),
        _function("paintApps"),
        _function("paintDecisions"),
        _function("paintProjects"),
        _function("paintPanels"),
        # The REAL notification painter, because the collision this file tests
        # is between two painters that both end in placeInOrder on the same
        # container. A stand-in for one of them would be a test of the stand-in.
        _function("notifCard"),
        _function("notifGroup"),
        _function("notifIdentity"),
        _function("paintNotifications"),
    ]
    src = "\n".join(parts)
    if not reconciled:
        src += _WIPE
    return src


def _run(ticks, *, clicks=None, reconciled: bool = True, notify_after: bool = False):
    node = shutil.which("node") or shutil.which("nodejs")
    if not node:
        pytest.skip("node is not installed")
    script = (
        _DOM + _EVENTS + _PANEL_STATE
        + _panel_source(reconciled=reconciled)
        + _DRIVER
    )
    env = dict(os.environ)
    env["LS_PANELS"] = json.dumps({
        "ticks": ticks,
        "clicks": clicks or {},
        "notifyAfter": notify_after,
        "groups": auth._demo_notifications(),
    })
    proc = subprocess.run(
        [node, "-e", script], capture_output=True, text=True, env=env, timeout=60
    )
    assert proc.returncode == 0, proc.stderr[-4000:]
    return json.loads(proc.stdout)


def _run_poll(*, ok=True, status=200, body=None, rejects=False, skip=False):
    """Drive the POLL with a response, rather than the painter with a payload.

    `skip` puts the pre-fix client back, so the control can show this harness
    is able to observe the defect at all.
    """
    node = shutil.which("node") or shutil.which("nodejs")
    if not node:
        pytest.skip("node is not installed")
    src = _panel_source() + _function("pollPanels")
    if skip:
        src += _SKIP_ON_NOTHING
    script = _DOM + _EVENTS + _PANEL_STATE + src + _SNAPSHOT + _POLL_DRIVER
    env = dict(os.environ)
    env["LS_POLL"] = json.dumps(
        {"ok": ok, "status": status, "body": body, "rejects": rejects}
    )
    proc = subprocess.run(
        [node, "-e", script], capture_output=True, text=True, env=env, timeout=60
    )
    assert proc.returncode == 0, proc.stderr[-4000:]
    return json.loads(proc.stdout)


def _payload(**over):
    """A payload of the shape the route serves, from the real tables."""
    data = auth._demo_panels()
    data.update(over)
    return data


def _ids(snap, panel):
    return [row["id"] for row in snap[panel]]


def _keys(snap, panel):
    return [row["key"] for row in snap[panel]]


PANELS = ["phone", "mailbox", "apps", "projects", "decisions"]


class TestThePanelsSurviveTheRepaint:
    """Node identity across an unchanged repaint. This is the whole point."""

    @pytest.mark.parametrize("panel", PANELS)
    def test_an_unchanged_payload_keeps_every_row_node(self, panel):
        """The property Jay sees as "it does not flicker".

        A row that is the same node across a repaint cannot replay its 520ms
        entrance animation, because nothing entered. A row that is a new node
        replays it whether or not one byte of the payload changed.
        """
        one, two = _run([_payload(), _payload()])
        assert _ids(one, panel) == _ids(two, panel)
        assert _ids(one, panel), f"{panel} rendered nothing to compare"

    @pytest.mark.parametrize("panel", PANELS)
    def test_an_unchanged_payload_leaves_the_order_alone(self, panel):
        one, two = _run([_payload(), _payload()])
        assert _keys(one, panel) == _keys(two, panel)

    def test_a_changed_line_is_written_into_the_SAME_row(self):
        """The discriminating case. Rebuilding gets the text right too -- it
        always did -- so a text assertion alone proves nothing. This requires
        the new text AND the old node."""
        first = _payload()
        second = auth._demo_panels()
        second["mailbox"][0] = dict(second["mailbox"][0],
                                    preview="changed while you were reading it")
        one, two = _run([first, second])
        assert _ids(one, "mailbox") == _ids(two, "mailbox")
        assert two["mailbox"][0]["sub"] == "changed while you were reading it"
        assert one["mailbox"][0]["sub"] != two["mailbox"][0]["sub"]

    def test_a_new_row_is_added_without_disturbing_the_others(self):
        first = _payload()
        second = auth._demo_panels()
        second["phone"] = [dict(second["phone"][0], key="call-new",
                                who="Someone New")] + second["phone"]
        one, two = _run([first, second])
        assert "call-new" in _keys(two, "phone")
        # Every row that was there before is the same node, still in order.
        kept = [row for row in two["phone"] if row["key"] != "call-new"]
        assert [row["id"] for row in kept] == _ids(one, "phone")

    def test_a_departed_row_is_removed_without_disturbing_the_others(self):
        first = _payload()
        second = auth._demo_panels()
        gone = second["mailbox"].pop(2)
        one, two = _run([first, second])
        assert gone["key"] not in _keys(two, "mailbox")
        kept = [row for row in one["mailbox"] if row["key"] != gone["key"]]
        assert _ids(two, "mailbox") == [row["id"] for row in kept]

    def test_the_minute_labels_are_collected_from_what_is_on_screen(self):
        """They are retouched in place on their own timer. Collected from the
        DOM rather than from what the paint just built, so a row the paint left
        untouched still has its minutes moved on."""
        one, _ = _run([_payload(), _payload()])
        # phone, mailbox, projects and decisions all carry timestamps; the
        # apps grid deliberately does not.
        panels = auth._demo_panels()
        want = (len(panels["phone"]) + len(panels["mailbox"])
                + len(panels["projects"]) + len(panels["decisions"]))
        assert one["__clocks"] == want, one["__clocks"]

    def test_the_apps_grid_itself_survives(self):
        """The grid is created by the painter, not the markup, so it is one
        more thing a wipe would replace under the user."""
        one, two = _run([_payload(), _payload()])
        assert _ids(one, "apps") == _ids(two, "apps")


class TestWhatTheUserDidSurvivesAPaint:
    """A repaint lands every 15 minutes whatever the user is in the middle of.

    Jay will be holding the phone in front of people. A switch that snaps back,
    or an approval that reappears unanswered, is worse than the panel not being
    there -- it reads as the device ignoring him.
    """

    def test_an_answered_decision_stays_answered_across_a_repaint(self):
        snaps = _run(
            [_payload(), _payload()],
            clicks={"0": [["decisions", "dec-invoice", "ls-dec-btn"]]},
        )
        first = {r["key"]: r for r in snaps[0]["decisions"]}["dec-invoice"]
        assert first["answered"] == "1", "the click did nothing"
        assert first["done"] == "Denied (demo)"
        after = {r["key"]: r for r in snaps[1]["decisions"]}["dec-invoice"]
        assert after["answered"] == "1"
        assert after["done"] == "Denied (demo)"
        assert after["id"] == first["id"]

    def test_an_approval_is_distinguishable_from_a_refusal(self):
        """Both arms. A painter that wrote the same word either way would pass
        the test above, which only ever clicks one button."""
        snaps = _run(
            [_payload()],
            clicks={"0": [["decisions", "dec-invoice", "ls-dec-btn"],
                          ["decisions", "dec-reply", "ls-dec-approve"]]},
        )
        rows = {r["key"]: r for r in snaps[0]["decisions"]}
        assert rows["dec-invoice"]["done"] == "Denied (demo)", rows["dec-invoice"]
        assert rows["dec-reply"]["done"] == "Approved (demo)", rows["dec-reply"]

    def test_a_repaint_does_not_stack_a_second_click_handler(self):
        """The bug a reconciled list grows quietly: wiring on every paint means
        the fourth repaint fires an action four times from one tap. It never
        shows up as a rendering fault, which is why it is asserted directly."""
        snaps = _run([_payload(), _payload(), _payload(), _payload()])
        for snap in snaps:
            rows = {r["key"]: r for r in snap["decisions"]}
            assert rows["dec-invoice"]["handlers"] == 1, rows["dec-invoice"]


class TestTheContentReachesTheGlass:
    """That the reconciler is faithful, not just stable. A painter that drew
    nothing would satisfy every identity assertion above."""

    def test_the_phone_panel_renders_a_row_per_call(self):
        one, = _run([_payload()])
        assert _keys(one, "phone") == [i["key"] for i in auth._demo_panels()["phone"]]
        missed = [r for r in one["phone"] if r["kind"] == "missed"]
        voicemail = [r for r in one["phone"] if r["kind"] == "voicemail"]
        # Counts come from the table rather than being written in: the content
        # is Jay's to grow, and a hard number here turns every addition red.
        assert len(missed) == len(
            [i for i in auth._demo_panels()["phone"] if i["kind"] == "missed"])
        assert missed and voicemail, one["phone"]

    def test_the_mailbox_marks_unread_rows_and_leaves_read_ones_alone(self):
        """Both arms. An attribute set on everything is not a mark."""
        one, = _run([_payload()])
        unread = [r["key"] for r in one["mailbox"] if r["unread"] == "1"]
        read = [r["key"] for r in one["mailbox"] if r["unread"] is None]
        assert unread and read, one["mailbox"]
        expected = {i["key"] for i in auth._demo_panels()["mailbox"] if i["unread"]}
        assert set(unread) == expected

    def test_each_mailbox_row_shows_its_source_subject_and_preview(self):
        one, = _run([_payload()])
        row = {r["key"]: r for r in one["mailbox"]}["dm-x-marcus"]
        assert row["app"] == "X"
        assert row["title"] == "@marcus_dev"
        assert row["subject"] == "Direct message"
        assert "4GB board" in row["sub"]

    def test_an_app_tile_keeps_its_badge_beside_its_monogram(self):
        """The badge is a SIBLING of the mark, because the painter rewrites the
        mark's contents outright -- parented inside it, the badge was wiped on
        the first paint of every tile without a glyph, which is all four."""
        one, = _run([_payload()])
        rows = {r["key"]: r for r in one["apps"]}
        assert rows["app-reddit"]["badge"] == "12", rows["app-reddit"]
        assert rows["app-reddit"]["name"] == "Reddit"
        assert rows["app-instagram"]["badge"] == "7"

    def test_an_empty_panel_says_so_rather_than_rendering_blank(self):
        """A panel that served nothing and a panel that failed to load must not
        look the same to the user."""
        one, = _run([_payload(phone=[], apps=[])])
        assert _keys(one, "phone") == ["empty"], one["phone"]
        assert _keys(one, "apps") == ["empty"], one["apps"]

    def test_an_empty_panel_refills_when_content_arrives(self):
        """The "nothing here" card is a row like any other, so it has to be
        cleared by the reconciler rather than lingering above the content."""
        one, two = _run([_payload(phone=[]), _payload()])
        assert _keys(one, "phone") == ["empty"]
        assert "empty" not in _keys(two, "phone")
        assert _keys(two, "phone") == [i["key"] for i in auth._demo_panels()["phone"]]

    def test_a_project_row_shows_its_progress_and_flags_the_blocked_ones(self):
        """Both arms again: a flag drawn on every row is not a flag."""
        one, = _run([_payload()])
        rows = {r["key"]: r for r in one["projects"]}
        assert rows["prj-brightside"]["flag"] == "Blocked", rows["prj-brightside"]
        assert rows["prj-taos-site"]["flag"] is None, rows["prj-taos-site"]
        assert rows["prj-taos-site"]["pct"] == "88", rows["prj-taos-site"]
        assert rows["prj-taos-site"]["app"] == "2 agents"
        # One agent is not "1 agents".
        assert rows["prj-northlight"]["app"] == "1 agent"

    def test_the_progress_bar_keeps_its_node_so_it_animates_from_where_it_was(self):
        """The bar's width is a CSS transition on a node the reconciler keeps.
        Rebuild the row and every bar re-runs from 0% on every poll -- the same
        flicker as the islands wearing a different costume."""
        first = _payload()
        second = auth._demo_panels()
        for row in second["projects"]:
            if row["key"] == "prj-taos-site":
                row["progress"] = 93
        one, two = _run([first, second])
        before = {r["key"]: r for r in one["projects"]}["prj-taos-site"]
        after = {r["key"]: r for r in two["projects"]}["prj-taos-site"]
        assert before["pct"] == "88" and after["pct"] == "93"
        assert before["id"] == after["id"]

    def test_painting_a_panel_un_hides_it(self):
        """The bug that reached the glass: 354 rows in the DOM and nothing
        visible.

        The panels are server-rendered `hidden`, and the view switcher only
        toggles `data-off` -- it never clears `hidden`. The two older panels
        escape it because their own painters set `hidden` themselves. These
        four had nobody doing it, so `.ls-feed > [data-view][hidden]` held them
        at display:none whichever tab was selected. Every content and identity
        assertion in this file passed throughout, because the harness had been
        creating the panels VISIBLE -- it was not reproducing the markup.
        """
        one, = _run([_payload()])
        for panel in ("phone", "mailbox", "apps", "projects"):
            assert one["__hidden"][panel] is False, (
                f"{panel} is still hidden after being painted -- "
                "it will render nothing on the device"
            )

    def test_an_empty_panel_is_shown_rather_than_hidden(self):
        """A panel with no rows must still render its "nothing here" card. The
        lazy fix for the above -- hide when empty, show when not -- would make
        an empty panel vanish, which is the state that is hardest to tell from
        a failure to load."""
        one, = _run([_payload(phone=[])])
        assert one["__hidden"]["phone"] is False
        assert _keys(one, "phone") == ["empty"]

    def test_decisions_sit_at_the_top_of_the_alerts_panel(self):
        """Not a tab of their own: Jay put them in alerts for quick answering.
        Painted into a detached container they would be correct, complete and
        invisible -- which is why this asserts the PARENT, not the contents."""
        one, = _run([_payload()])
        assert one["__decisions_parented"] is True
        assert one["__decisions_first"] is True

    def test_a_decision_arriving_after_the_container_was_dropped_re_attaches(self):
        """The sequence a mutation caught this suite missing.

        With nothing pending, the decisions container empties and steps out of
        the alerts panel -- and the next notification paint, finding it empty,
        legitimately leaves it out of `want`, so placeInOrder removes it. When
        a decision then arrives, paintDecisions is painting into a DETACHED
        node: correct, complete and invisible.

        Deleting the re-attach left all 63 assertions green, because every one
        of them started with the container already in place. An untested repair
        path and a repair path that does nothing are the same reading.
        """
        empty = _payload(decisions=[])
        one, two = _run([empty, _payload()], notify_after=True)
        assert one["__decisions_parented"] is False, (
            "the container should have been dropped while empty -- "
            "this scenario is not reaching the state it means to test"
        )
        assert two["__decisions_parented"] is True, (
            "a decision arrived and was painted into a detached container"
        )
        assert two["__decisions_first"] is True
        assert len(two["decisions"]) == len(auth._demo_panels()["decisions"])

    def test_a_notification_paint_does_not_delete_the_decisions(self):
        """The two run on independent timers and both end in placeInOrder,
        which removes everything past the last wanted element. This is the
        collision, driven in the order that breaks it."""
        one, = _run([_payload()], notify_after=True)
        assert one["__decisions_parented"] is True, (
            "a notification poll dropped the decisions out of the alerts panel"
        )
        assert len(one["decisions"]) == len(auth._demo_panels()["decisions"])


class TestTheHarnessCanFail:
    """Without these, every assertion above is also what a painter that did
    nothing at all would produce."""

    def test_the_harness_observes_the_defect(self):
        """Put the wipe back -- empty each panel, then paint -- and identity
        must break in every panel.

        This is the control that makes the whole file mean something. The
        mutation leaves every rendered value correct, exactly as the real bug
        did: the islands were always rebuilt with the right names. If these
        assertions could not tell the two apart they would be measuring
        nothing.
        """
        one, two = _run([_payload(), _payload()], reconciled=False)
        for panel in PANELS:
            assert _keys(one, panel) == _keys(two, panel), (
                f"{panel}: the mutation should change identity, not content"
            )
            assert _ids(one, panel) != _ids(two, panel), (
                f"{panel}: identity survived a full wipe -- "
                "this suite cannot see the defect it exists to catch"
            )

    def test_the_mutation_applied(self):
        """A mutation that did not apply is not a green. The wipe is asserted
        into the source and read back out, so a renamed painter cannot turn the
        control above into a silent no-op."""
        mutated = _panel_source(reconciled=False)
        assert "paintPanels = function (data)" in mutated
        assert 'panelEls[k].textContent = ""' in mutated
        assert "var __reconciled = paintPanels;" in mutated
        assert _WIPE not in _panel_source(reconciled=True)

    def test_the_extractor_returns_one_function_each(self):
        """`_function` reads to a balanced brace. When an apostrophe in a
        comment once opened a string that never closed, it returned 12kB
        instead of 5kB -- three functions where one was asked for -- and every
        test still passed, because the extra functions were the real ones.
        """
        for name, ceiling in (
            ("paintPhone", 3000),
            ("paintMailbox", 3000),
            ("paintApps", 3000),
            ("paintProjects", 6000),
            ("paintPanels", 3000),
        ):
            src = _function(name)
            assert src.startswith(f"function {name}("), name
            assert src.count(f"function {name}(") == 1, name
            assert len(src) < ceiling, (name, len(src))

    def test_the_driver_would_notice_a_painter_that_drew_nothing(self):
        """The positive control for the snapshot itself: with no content in the
        payload every panel comes back as its empty card, which is a different
        reading from the populated one above."""
        one, = _run([{"phone": [], "mailbox": [], "apps": [],
                      "projects": [], "decisions": []}])
        # Decisions is the exception BY DESIGN: it is the head of the alerts
        # panel, not a panel, so with nothing pending it empties and detaches
        # rather than showing a "nothing to decide" card above the stacks.
        for panel in ("phone", "mailbox", "apps", "projects"):
            assert _keys(one, panel) == ["empty"], (panel, one[panel])
        assert _keys(one, "decisions") == [], one["decisions"]


class TestPerAgentUsageInTheStatsPanel:
    """Jay: "in the stats it should show live demo data for agents cpu, ram and
    storage usage".

    LIVE is the load-bearing word. The stats view polls every 3 SECONDS, so a
    fixed table would sit there dead and read as broken rather than as demo
    content -- which is the opposite of what he asked for.
    """

    def test_the_names_come_from_the_same_env_var_as_the_islands(self, monkeypatch):
        """A second list would drift the first time one drop-in was edited and
        not the other, and then the stats panel and the agent islands would
        disagree about who is running."""
        monkeypatch.setenv(
            "TAOS_LOCK_DEMO_AGENTS",
            "Personal Assistant:hermes:Drafting,Accountant:deepseek:Reconciling",
        )
        assert auth._demo_agent_names() == ["Personal Assistant", "Accountant"]

    def test_a_repeated_name_is_not_listed_twice(self, monkeypatch):
        monkeypatch.setenv("TAOS_LOCK_DEMO_AGENTS", "Ann:x,Ann:y,Bob:z")
        assert auth._demo_agent_names() == ["Ann", "Bob"]

    def test_no_demo_agents_means_no_usage_rather_than_zeroes(self, monkeypatch):
        """Absent, not zero -- the rule this whole panel is built on. A row of
        0% would be a claim that the agents are idle."""
        monkeypatch.setenv("TAOS_LOCK_DEMO_AGENTS", "")
        assert auth._demo_agent_usage() == []

    def test_every_agent_reports_all_three_readings(self, monkeypatch):
        monkeypatch.setenv("TAOS_LOCK_DEMO_AGENTS", "Ann:x,Bob:y,Cal:z")
        rows = auth._demo_agent_usage()
        assert len(rows) == 3
        for row in rows:
            assert row["cpu_percent"] > 0
            assert row["ram_mb"] >= 48
            assert row["storage_mb"] > 0
            assert row["demo"] is True

    def test_the_readings_move_between_polls(self, monkeypatch):
        """The actual ask. Asserted by moving the CLOCK rather than sleeping,
        so this cannot be the test that makes the suite slow."""
        monkeypatch.setenv("TAOS_LOCK_DEMO_AGENTS", "Ann:x")
        clock = [1_700_000_000.0]
        monkeypatch.setattr(auth.time, "time", lambda: clock[0])
        first = auth._demo_agent_usage()[0]
        clock[0] += 30
        second = auth._demo_agent_usage()[0]
        assert first["cpu_percent"] != second["cpu_percent"], (first, second)
        assert first["ram_mb"] != second["ram_mb"], (first, second)

    def test_storage_only_ever_grows(self, monkeypatch):
        """Storage that wobbles downward is a tell that the number is invented,
        and it is the one reading here a viewer might actually reason about."""
        monkeypatch.setenv("TAOS_LOCK_DEMO_AGENTS", "Ann:x")
        clock = [1_700_000_000.0]
        monkeypatch.setattr(auth.time, "time", lambda: clock[0])
        seen = []
        for _ in range(8):
            seen.append(auth._demo_agent_usage()[0]["storage_mb"])
            clock[0] += 600
        assert seen == sorted(seen), seen
        assert seen[-1] > seen[0], seen

    def test_a_baseline_is_stable_for_a_given_name(self, monkeypatch):
        """Derived from a CRC of the NAME, not from a random seed: an agent
        showing 6% now and 21% after a controller bounce reads as a different
        agent. Same clock, so only the baseline is in play."""
        clock = [1_700_000_000.0]
        monkeypatch.setattr(auth.time, "time", lambda: clock[0])
        monkeypatch.setenv("TAOS_LOCK_DEMO_AGENTS", "Accountant:x")
        first = auth._demo_agent_usage()[0]
        monkeypatch.setenv("TAOS_LOCK_DEMO_AGENTS", "Accountant:x")
        again = auth._demo_agent_usage()[0]
        assert first["ram_mb"] == again["ram_mb"]
        assert first["storage_mb"] == again["storage_mb"]

    def test_different_agents_get_different_baselines(self, monkeypatch):
        """Otherwise six identical rows, which reads as a rendering bug."""
        monkeypatch.setenv("TAOS_LOCK_DEMO_AGENTS", "Ann:x,Bob:y,Cal:z,Dee:w")
        rams = [r["ram_mb"] for r in auth._demo_agent_usage()]
        assert len(set(rams)) == len(rams), rams

    def test_the_agents_cannot_add_up_to_an_impossible_machine(self, monkeypatch):
        """Six agents at an unbounded baseline would happily report a phone
        that is 300% busy, next to a REAL cpu_percent from /proc/stat."""
        monkeypatch.setenv(
            "TAOS_LOCK_DEMO_AGENTS",
            ",".join("Agent%d:f:s" % i for i in range(12)),
        )
        total = sum(r["cpu_percent"] for r in auth._demo_agent_usage())
        assert total <= 82.5, total

    def test_the_payload_key_is_absent_when_demo_is_off(self, monkeypatch):
        """"No agents running" and "nothing is measuring agents" are different
        answers, and this endpoint draws that distinction for every hardware
        reading already."""
        monkeypatch.delenv("TAOS_LOCK_DEMO_AGENTS", raising=False)
        assert auth._demo_enabled() is False
        assert auth._demo_agent_usage() == []

    def test_the_stats_painter_draws_a_bar_only_for_cpu(self):
        """RAM and storage have no ceiling to draw against, and a bar against
        an invented maximum is worse than no bar."""
        js = auth._LOCK_SCREEN_SCRIPT
        start = js.index('partOf(statsEl, "agents"')
        block = js[start:start + 1600]
        assert "ag.cpu_percent" in block
        # The value line carries all three readings as text...
        assert "ram_mb" in block and "storage_mb" in block
        # ...but only cpu_percent is passed as the percentage argument.
        assert "statRow(acard" in block


class TestADeviceWithTheDemoFlagsOffStillHasPanels:
    """The starting state this file never varied: a response that is NOT 200.

    @taOS-dev found it in review and it is the same lesson one level up. Every
    other test here begins at `_payload()`, which is the 200 path; the flags-off
    path was asserted only as far as `status_code == 404`, and the client half
    of that sentence -- "the panels render their own nothing-here" -- was never
    run. It was not true. `paintPanels` is the ONLY thing that clears the
    markup's `hidden`, the old client called it only on the `r.ok` branch, and
    so on every device that is not in demo mode -- which is every real one --
    tapping Phone, Mailbox, Apps or Projects rendered nothing at all. Not an
    empty state: blank.

    `test_the_harness_observes_the_skip` puts the old branch back and requires
    these assertions to go red, because "the panel is visible" is exactly the
    sort of claim a harness can satisfy by accident.
    """

    OWNED = ["phone", "mailbox", "apps", "projects"]

    def test_a_404_unhides_all_four_panels(self):
        snap = _run_poll(ok=False, status=404, body=None)
        assert snap["__fetched"] == 1
        for panel in self.OWNED:
            assert snap["__hidden"][panel] is False, panel

    def test_a_404_paints_each_panel_its_own_empty_state(self):
        """Visible AND saying something. A panel unhidden but never painted is
        an empty box on the glass, which is not what the route's docstring
        promises either."""
        snap = _run_poll(ok=False, status=404, body=None)
        for panel in self.OWNED:
            assert _keys(snap, panel) == ["empty"], (panel, _keys(snap, panel))
            assert "ls-empty" in snap[panel][0]["cls"], panel

    def test_a_404_leaves_no_decisions_head_in_the_alerts_panel(self):
        """With nothing pending the head is detached rather than sitting there
        empty -- the one panel whose absence is correct."""
        snap = _run_poll(ok=False, status=404, body=None)
        assert snap["__decisions_parented"] is False
        assert snap["decisions"] == []

    def test_a_dead_network_is_treated_as_nothing_to_show(self):
        """A rejected fetch and a 404 are the same thing to a user: no content.
        They must not be the same as a blank screen."""
        snap = _run_poll(rejects=True)
        for panel in self.OWNED:
            assert snap["__hidden"][panel] is False, panel
            assert _keys(snap, panel) == ["empty"], panel

    def test_the_demo_path_still_paints_the_real_tables(self):
        """The discriminating case: with the flags ON nothing above applies, and
        the panels carry content rather than an empty state. Without this, an
        implementation that painted the empty state unconditionally would
        satisfy every assertion in this class."""
        snap = _run_poll(ok=True, status=200, body=_payload())
        for panel in self.OWNED:
            assert snap["__hidden"][panel] is False, panel
            assert _keys(snap, panel) != ["empty"], panel
            assert len(snap[panel]) > 1, panel

    def test_the_harness_observes_the_skip(self):
        """The control. The pre-fix client, against the same 404: the panels
        stay exactly as the server rendered them, which is hidden."""
        snap = _run_poll(ok=False, status=404, body=None, skip=True)
        assert snap["__fetched"] == 1
        for panel in self.OWNED:
            assert snap["__hidden"][panel] is True, panel
            assert snap[panel] == [], panel
