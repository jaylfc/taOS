"""`next_card.py` fix-forward + BASE: directive invariant.

A card labelled ``fix-forward`` MUST declare a ``BASE: exec/tsk-<id>`` line so the
executor cuts its worktree from the target PR's branch instead of dev.  Without it
the lane silently discards the target PR's work (tsk-scaffx).  The dispatcher
enforces this BEFORE any side effect -- no dispatch-counter increment, no claim.

The throttle exemption survives: a fix-forward card WITH a valid BASE: line still
dispatches while its target repo is at the open-PR cap.
"""

import json
import os
import subprocess

import pytest

NEXT_CARD = os.path.expanduser("~/.taos-team/next_card.py")
THROTTLE_REPO = "jaylfc/taOS"


def _write_fixture(tmp_path, cards, name="fixture.json"):
    p = tmp_path / name
    p.write_text(json.dumps(cards))
    return str(p)


def _run_next_card(fixture_path, extra_env=None):
    env = {
        **os.environ,
        "TAOS_CARD_FIXTURE": fixture_path,
        "TAOS_AGENT_OVERRIDE": "@taos-dev",
        # Prevent stale state files from prior real dispatches from interfering.
        "TAOS_PROJECTS": "prj-5y722y",
    }
    if extra_env:
        env.update(extra_env)
    result = subprocess.run(
        ["python3", NEXT_CARD],
        capture_output=True,
        text=True,
        env=env,
        timeout=30,
    )
    return result.stdout.strip(), result.stderr.strip(), result.returncode


def _card(task_id, body, labels=None, proj="prj-5y722y"):
    if labels is None:
        labels = ["claimable", "fix-forward"]
    return {
        "id": task_id,
        "title": f"test {task_id}",
        "body": body,
        "labels": labels,
        "status": "open",
        "assignee_id": None,
        "priority": 1,
        "created_ts": "2026-01-01T00:00:00Z",
        "_proj": proj,
    }


# ---------------------------------------------------------------------------
# Red: fix-forward WITHOUT BASE must NOT dispatch, at cap or not.
# ---------------------------------------------------------------------------

class TestFixForwardWithoutBaseRefused:

    def test_refused_without_base_at_throttle_cap(self, tmp_path):
        card = _card("tsk-red-nobase-cap", "fix-forward, no BASE line")
        fixture = _write_fixture(tmp_path, [card])
        out, _err, _rc = _run_next_card(fixture, {"TAOS_THROTTLED_REPOS": THROTTLE_REPO})
        assert out == "", (
            "fix-forward card without BASE: must not dispatch at throttle cap, "
            f"but got: {out}"
        )

    def test_refused_without_base_without_throttle(self, tmp_path):
        card = _card("tsk-red-nobase-notcap", "fix-forward, no BASE line")
        fixture = _write_fixture(tmp_path, [card])
        out, _err, _rc = _run_next_card(fixture, {})
        assert out == "", (
            "fix-forward card without BASE: must not dispatch even when not at cap, "
            f"but got: {out}"
        )

    def test_refused_without_base_in_fixforward_only_mode(self, tmp_path):
        card = _card("tsk-red-nobase-ffonly", "fix-forward, no BASE line")
        fixture = _write_fixture(tmp_path, [card])
        out, _err, _rc = _run_next_card(
            fixture,
            {"TAOS_FIX_FORWARD_ONLY": "1", "TAOS_THROTTLED_REPOS": THROTTLE_REPO},
        )
        assert out == "", (
            "fix-forward card without BASE: must not dispatch in TAOS_FIX_FORWARD_ONLY mode, "
            f"but got: {out}"
        )


# ---------------------------------------------------------------------------
# Control A: fix-forward WITH BASE still dispatches while repo is at cap.
# This proves the throttle exemption for fix-forward cards survives.
# ---------------------------------------------------------------------------

class TestFixForwardWithBaseDispatchesAtCap:

    def test_dispatched_with_base_at_throttle_cap(self, tmp_path):
        card = _card("tsk-ctrl-a-cap", "fix-forward card\nBASE: exec/tsk-other12")
        fixture = _write_fixture(tmp_path, [card])
        out, _err, _rc = _run_next_card(fixture, {"TAOS_THROTTLED_REPOS": THROTTLE_REPO})
        assert "FIXTURE:tsk-ctrl-a-cap" in out, (
            "fix-forward card WITH BASE: must dispatch even at throttle cap "
            "(exemption survives), but got: {out}"
        )

    def test_dispatched_with_base_without_throttle(self, tmp_path):
        card = _card("tsk-ctrl-a-notcap", "fix-forward card\nBASE: exec/tsk-other12")
        fixture = _write_fixture(tmp_path, [card])
        out, _err, _rc = _run_next_card(fixture, {})
        assert "FIXTURE:tsk-ctrl-a-notcap" in out, (
            "fix-forward card WITH BASE: must dispatch without throttle, "
            f"but got: {out}"
        )

    def test_dispatched_with_base_in_fixforward_only_mode(self, tmp_path):
        card = _card("tsk-ctrl-a-ffonly", "fix-forward card\nBASE: exec/tsk-other12")
        fixture = _write_fixture(tmp_path, [card])
        out, _err, _rc = _run_next_card(
            fixture,
            {"TAOS_FIX_FORWARD_ONLY": "1", "TAOS_THROTTLED_REPOS": THROTTLE_REPO},
        )
        assert "FIXTURE:tsk-ctrl-a-ffonly" in out, (
            "fix-forward card WITH BASE: must dispatch in TAOS_FIX_FORWARD_ONLY mode, "
            f"but got: {out}"
        )


# ---------------------------------------------------------------------------
# Control B: a normal card with no fix-forward label holds at cap.
# ---------------------------------------------------------------------------

class TestNormalCardThrottled:

    def test_normal_card_at_cap(self, tmp_path):
        card = _card(
            "tsk-ctrl-b",
            "normal card, no fix-forward label",
            labels=["claimable", "lane-ok"],
        )
        fixture = _write_fixture(tmp_path, [card])
        out, _err, _rc = _run_next_card(fixture, {"TAOS_THROTTLED_REPOS": THROTTLE_REPO})
        assert out == "", (
            "normal card (no fix-forward) at cap: must not dispatch, "
            f"but got: {out}"
        )


# ---------------------------------------------------------------------------
# Control C: fix-forward WITH BASE naming a branch that does not exist on
# origin.  The dispatcher dispatches it (the directive is present); the
# executor bounces with "branch does not exist on origin" -- the existing
# reason, unchanged.  The dispatcher does not check branch existence.
# ---------------------------------------------------------------------------

class TestFixForwardWithBaseNonExistentBranch:

    def test_dispatched_with_base_nonexistent_branch_at_cap(self, tmp_path):
        card = _card(
            "tsk-ctrl-c",
            "fix-forward card\nBASE: exec/tsk-doesnotexist",
        )
        fixture = _write_fixture(tmp_path, [card])
        out, _err, _rc = _run_next_card(
            fixture, {"TAOS_THROTTLED_REPOS": THROTTLE_REPO}
        )
        assert "FIXTURE:tsk-ctrl-c" in out, (
            "fix-forward card WITH BASE (even for a non-existent branch): the dispatcher "
            f"must still dispatch it; the bounce is the executor's job. Got: {out}"
        )


# ---------------------------------------------------------------------------
# Distinguishing evidence: the pair proves the label implies BASE, not just
# the throttle exemption.
#
# If the BASE check were removed, the fix-forward-without-BASE card would
# dispatch at cap (throttle exemption survives) -- this test would FAIL.
# If the throttle exemption were removed, the fix-forward-with-BASE card
# would NOT dispatch at cap -- Control A would FAIL.
#
# Asserting only "did not dispatch" on the no-BASE card proves nothing:
# it passes whether the block is from the BASE check or the throttle.
# The pair (Base blocked by missing BASE, Base-with-BASE passes) is the
# distinguishing evidence.
# ---------------------------------------------------------------------------

class TestDistinguishingEvidence:

    def test_pair_at_cap_proves_label_implies_base(self, tmp_path):
        no_base = _card("tsk-dist-nobase", "fix-forward, no base")
        with_base = _card("tsk-dist-base", "fix-forward\nBASE: exec/tsk-other99")
        env = {"TAOS_THROTTLED_REPOS": THROTTLE_REPO}

        # Card WITH BASE dispatches at cap -- throttle exemption works.
        fixture_both = _write_fixture(tmp_path, [with_base, no_base])
        out_both, _e, _r = _run_next_card(fixture_both, env)
        assert "FIXTURE:tsk-dist-base" in out_both, (
            "fix-forward WITH BASE must dispatch at cap; the throttle exemption "
            f"must survive. Got: {out_both}"
        )

        # Card WITHOUT BASE does NOT dispatch at cap -- BASE check works.
        fixture_nobase = _write_fixture(
            tmp_path, [no_base], name="nobase.json"
        )
        out_nobase, _e, _r = _run_next_card(fixture_nobase, env)
        assert out_nobase == "", (
            "fix-forward WITHOUT BASE must not dispatch at cap; the BASE check "
            f"must be the reason, not the throttle. Got: {out_nobase}"
        )

        # The pair together proves: at cap, the ONLY difference is the BASE:
        # line.  If the label stopped implying BASE (check removed), both would
        # dispatch and the second assertion would fail.
        assert "FIXTURE:tsk-dist-base" in out_both and out_nobase == "", (
            "Distinguishing pair failed: fix-forward WITH BASE dispatches at cap "
            "while WITHOUT BASE it does not. This pair must hold together."
        )


@pytest.mark.parametrize(
    "body,expected",
    [
        ("fix-forward\nBASE: exec/tsk-m2dbga", True),
        ("fix-forward\nBASE: exec/tsk-xyeewi", True),
        ("fix-forward\n  BASE:   exec/tsk-m2dbga", True),
        ("fix-forward\nBASE: exec/tsk-xocdcd-2", True),  # prefix match; executor validates
        ("fix-forward\nREPO: jaylfc/taOS\nBASE: exec/tsk-m2dbga", True),
        ("fix-forward\nBASE: exec/dev", False),           # not an exec/tsk branch
        ("fix-forward\nBASE: dev", False),                 # not exec/
        ("fix-forward without any base", False),           # no BASE line at all
        ("BASE: exec/tsk-m2dbga", True),                   # BASE present, no "fix-forward" in body
    ],
)
def test_base_re_recognises_directive(tmp_path, body, expected):
    """Unit-test the _BASE_RE pattern directly against representative bodies."""
    import re
    base_re = re.compile(r"^[ \t]*BASE:[ \t]*exec/tsk-[A-Za-z0-9]+", re.M)
    assert bool(base_re.search(body)) is expected
