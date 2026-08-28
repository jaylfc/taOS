"""Tests for scripts/check_bot_review.py."""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from unittest.mock import patch

import pytest
import yaml

_SCRIPT = Path(__file__).resolve().parent.parent.parent / "scripts" / "check_bot_review.py"
REPO_ROOT = Path(__file__).resolve().parent.parent.parent


def _load_module():
    spec = importlib.util.spec_from_file_location("check_bot_review", _SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    sys.modules["check_bot_review"] = mod
    spec.loader.exec_module(mod)
    return mod


ACK_BODY = (
    "<!-- This is an auto-generated reply by CodeRabbit -->\n"
    "<!-- CodeRabbit review command invocation: v2:11111111-1111-1111-1111-111111111111 -->\n"
    "<details>\n"
    "<summary>✅ Action performed</summary>\n"
    "\n"
    "Full review finished.\n"
    "\n"
    "</details>"
)
SUMMARY_BODY = "<!-- This is an auto-generated comment: summarize by coderabbit.ai -->"


@pytest.fixture(scope="module")
def check_mod():
    return _load_module()


class TestIsRateLimitStub:
    def test_review_limit_reached_matches(self, check_mod) -> None:
        assert check_mod.is_rate_limit_stub("review limit reached")

    def test_review_rate_limited_matches(self, check_mod) -> None:
        assert check_mod.is_rate_limit_stub("Review rate limited")

    def test_rate_limit_reached_matches(self, check_mod) -> None:
        assert check_mod.is_rate_limit_stub("rate limit reached")

    def test_rate_limit_exhausted_matches(self, check_mod) -> None:
        assert check_mod.is_rate_limit_stub("rate limit exhausted")

    def test_plan_quota_reached_matches(self, check_mod) -> None:
        assert check_mod.is_rate_limit_stub("plan quota reached")

    def test_review_quota_exhausted_matches(self, check_mod) -> None:
        assert check_mod.is_rate_limit_stub("review quota exhausted")

    def test_case_insensitive(self, check_mod) -> None:
        assert check_mod.is_rate_limit_stub("REVIEW LIMIT REACHED")

    def test_real_review_body_does_not_match(self, check_mod) -> None:
        assert not check_mod.is_rate_limit_stub(
            "Consider adding rate limit handling to this endpoint."
        )

    def test_empty_body_does_not_match(self, check_mod) -> None:
        assert not check_mod.is_rate_limit_stub("")
        assert not check_mod.is_rate_limit_stub(None)

    def test_generic_rate_limit_without_context_does_not_match(self, check_mod) -> None:
        assert not check_mod.is_rate_limit_stub("This endpoint handles rate limiting correctly.")


class TestIsRealItem:
    def test_real_review_with_substantive_body(self, check_mod) -> None:
        item = check_mod.CRItem(
            id=1, body="Line 42: potential memory leak here.", is_review=True,
        )
        assert check_mod.is_real_item(item)

    def test_real_review_approved(self, check_mod) -> None:
        item = check_mod.CRItem(
            id=1, body="", is_review=True, review_state="APPROVED",
        )
        assert check_mod.is_real_item(item)

    def test_real_review_changes_requested(self, check_mod) -> None:
        item = check_mod.CRItem(
            id=1, body="", is_review=True, review_state="CHANGES_REQUESTED",
        )
        assert check_mod.is_real_item(item)

    def test_rate_limit_stub_review_is_not_real(self, check_mod) -> None:
        item = check_mod.CRItem(
            id=1, body="Review rate limited", is_review=True, review_state="COMMENTED",
        )
        assert not check_mod.is_real_item(item)

    def test_empty_body_comment_is_not_real(self, check_mod) -> None:
        item = check_mod.CRItem(
            id=1, body="", is_review=False,
        )
        assert not check_mod.is_real_item(item)

    def test_rate_limit_stub_comment_is_not_real(self, check_mod) -> None:
        item = check_mod.CRItem(
            id=1, body="review limit reached", is_review=False,
        )
        assert not check_mod.is_real_item(item)

    def test_coderabbit_acknowledgement_is_not_real(self, check_mod) -> None:
        item = check_mod.CRItem(
            id=1, body=ACK_BODY, is_review=False,
        )
        assert not check_mod.is_real_item(item)

    def test_coderabbit_summary_is_not_real(self, check_mod) -> None:
        item = check_mod.CRItem(
            id=1, body=SUMMARY_BODY, is_review=False,
        )
        assert not check_mod.is_real_item(item)

    def test_coderabbit_acknowledgement_review_is_not_real(self, check_mod) -> None:
        item = check_mod.CRItem(
            id=1, body=ACK_BODY, is_review=True, review_state="COMMENTED",
        )
        assert not check_mod.is_real_item(item)

    def test_genuine_review_control_is_real(self, check_mod) -> None:
        item = check_mod.CRItem(
            id=1,
            body="## Review\n\n### Findings\n\nLine 42 needs null-checking.",
            is_review=True,
        )
        assert check_mod.is_real_item(item)

    def test_rate_limit_stub_control_is_not_real(self, check_mod) -> None:
        item = check_mod.CRItem(
            id=1, body="Review rate limited. Please try again later.", is_review=False,
        )
        assert not check_mod.is_real_item(item)

    @pytest.mark.parametrize("state", ["APPROVED", "CHANGES_REQUESTED"])
    def test_stub_bodied_review_is_not_real_whatever_the_state(
        self, check_mod, state: str,
    ) -> None:
        """The stub checks outrank the review state, and must.

        A review carrying a decisive state whose BODY is a rate-limit stub is
        the fake-green shape this gate exists to catch. Trusting the state
        here is the one ordering that lets it through, so the gate fails
        closed and this test pins that ordering.
        """
        item = check_mod.CRItem(
            id=1,
            body="Review rate limited. Please try again later.",
            is_review=True,
            review_state=state,
        )
        assert not check_mod.is_real_item(item)

    @pytest.mark.parametrize("state", ["APPROVED", "CHANGES_REQUESTED"])
    def test_scaffolding_bodied_review_is_not_real_whatever_the_state(
        self, check_mod, state: str,
    ) -> None:
        item = check_mod.CRItem(
            id=1, body=ACK_BODY, is_review=True, review_state=state,
        )
        assert not check_mod.is_real_item(item)

    @pytest.mark.parametrize("state", ["APPROVED", "CHANGES_REQUESTED"])
    def test_empty_bodied_decisive_review_is_real(
        self, check_mod, state: str,
    ) -> None:
        """Control for the two tests above.

        Without this, they would pass just as well if the state guard had
        been deleted outright. An empty-bodied APPROVED/CHANGES_REQUESTED
        review is real ONLY because the state guard is still reached, so
        this is the case that goes red if the guard is removed.
        """
        item = check_mod.CRItem(
            id=1, body="", is_review=True, review_state=state,
        )
        assert check_mod.is_real_item(item)


class TestClassify:
    def test_empty_items_is_absent(self, check_mod) -> None:
        exit_code, message = check_mod.classify([])
        assert exit_code == 0
        assert "absent, not stubbed" in message

    def test_real_review_exists(self, check_mod) -> None:
        items = [
            check_mod.CRItem(
                id=1, body="Review rate limited", is_review=True,
            ),
            check_mod.CRItem(
                id=2, body="Found a bug on line 42.", is_review=False,
            ),
        ]
        exit_code, message = check_mod.classify(items)
        assert exit_code == 0
        assert "real CodeRabbit review" in message

    def test_only_stubs_fails(self, check_mod) -> None:
        items = [
            check_mod.CRItem(
                id=1, body="review limit reached", is_review=False,
            ),
        ]
        exit_code, message = check_mod.classify(items)
        assert exit_code == 1
        assert "FAIL" in message
        assert "rate-limit stub" in message

    def test_multiple_stubs_fails(self, check_mod) -> None:
        items = [
            check_mod.CRItem(
                id=1, body="Review rate limited", is_review=False,
            ),
            check_mod.CRItem(
                id=2, body="review limit reached", is_review=True,
            ),
        ]
        exit_code, message = check_mod.classify(items)
        assert exit_code == 1
        assert "FAIL" in message

    def test_approved_review_is_real_even_with_stub_comment(self, check_mod) -> None:
        items = [
            check_mod.CRItem(
                id=1, body="review limit reached", is_review=False,
            ),
            check_mod.CRItem(
                id=2, body="", is_review=True, review_state="APPROVED",
            ),
        ]
        exit_code, message = check_mod.classify(items)
        assert exit_code == 0
        assert "real CodeRabbit review" in message

    def test_latest_not_stub_but_no_real_review(self, check_mod) -> None:
        """If any CR output is a stub signature (even when no real reviews
        exist), the check fails -- a stub masquerading as a review is
        fake-green."""
        items = [
            check_mod.CRItem(
                id=1, body="review limit reached", is_review=False,
            ),
            check_mod.CRItem(
                id=2, body="", is_review=False,
            ),
        ]
        exit_code, message = check_mod.classify(items)
        assert exit_code == 1
        assert "FAIL" in message

    def test_collect_coderabbit_items_with_coderabbitai_bot(self, check_mod) -> None:
        """Test that collect_coderabbit_items correctly identifies
        coderabbitai[bot] login from raw API-shaped JSON, with only
        _api_get mocked (the narrow filter scope)."""
        call_count = 0

        def _api_get_side_effect(url, token=None):
            nonlocal call_count
            call_count += 1
            # Return API-shaped JSON with coderabbitai[bot] login
            # for the first call only; subsequent calls return empty
            # to avoid duplicate collection from three endpoints.
            if call_count == 1:
                return [
                    {
                        "id": 1,
                        "user": {"login": "coderabbitai[bot]"},
                        "body": "Some review comment",
                        "state": "COMMENTED",
                        "created_at": "2024-01-01T00:00:00Z",
                    }
                ]
            return []

        with patch.object(check_mod, "_api_get", side_effect=_api_get_side_effect):
            items = check_mod.collect_coderabbit_items("jaylfc", "taOS", 2412)
        assert len(items) == 1
        assert items[0].id == 1
        assert items[0].body == "Some review comment"
        assert items[0].is_review is True

    def test_message_echoes_exit_code(self, check_mod) -> None:
        items = [
            check_mod.CRItem(
                id=1, body="Review rate limited", is_review=False,
            ),
        ]
        exit_code, message = check_mod.classify(items)
        assert f"(exit {exit_code})" in message

    def _pr2482_items(self, check_mod):
        """Build the exact #2482 item set: one auto-summary + two
        acknowledgement replies, all is_review=False (the live instance
        verified 2026-08-17 where GET /reviews returned zero reviews yet
        the gate went green on three stub items)."""
        return [
            check_mod.CRItem(id=1, body=SUMMARY_BODY, is_review=False),
            check_mod.CRItem(id=2, body=ACK_BODY, is_review=False),
            check_mod.CRItem(id=3, body=ACK_BODY, is_review=False),
        ]

    def test_acknowledgement_only_fails(self, check_mod) -> None:
        items = [
            check_mod.CRItem(id=1, body=ACK_BODY, is_review=False),
        ]
        exit_code, message = check_mod.classify(items)
        assert exit_code == 1
        assert "FAIL" in message

    def test_summary_plus_acknowledgements_fails(self, check_mod) -> None:
        """The #2482 set: summary + two acknowledgements. Must exit non-zero
        -- a trigger was accepted and nothing came back."""
        items = self._pr2482_items(check_mod)
        exit_code, message = check_mod.classify(items)
        assert exit_code == 1
        assert "FAIL" in message
        assert "scaffolding" in message

    def test_genuine_review_control_passes(self, check_mod) -> None:
        items = [
            check_mod.CRItem(
                id=1,
                body="## Review\n\n### Findings\n\nLine 42 needs null-checking.",
                is_review=True,
                review_state="COMMENTED",
            ),
        ]
        exit_code, message = check_mod.classify(items)
        assert exit_code == 0
        assert "real CodeRabbit review" in message

    def test_rate_limit_stub_control_fails(self, check_mod) -> None:
        items = [
            check_mod.CRItem(
                id=1, body="Review rate limited. Please try again later.",
                is_review=False,
            ),
        ]
        exit_code, message = check_mod.classify(items)
        assert exit_code == 1
        assert "FAIL" in message
        assert "rate-limit stub" in message

    def test_mixed_genuine_review_and_acknowledgement_passes(self, check_mod) -> None:
        """A genuine review plus an acknowledgement must stay green -- the
        real review is what counts."""
        items = [
            check_mod.CRItem(
                id=1,
                body="## Review\n\n### Findings\n\nLine 42 needs null-checking.",
                is_review=True,
                review_state="COMMENTED",
            ),
            check_mod.CRItem(id=2, body=ACK_BODY, is_review=False),
            check_mod.CRItem(id=3, body=SUMMARY_BODY, is_review=False),
        ]
        exit_code, message = check_mod.classify(items)
        assert exit_code == 0
        assert "real CodeRabbit review" in message

    def test_mixed_rate_limit_stub_and_acknowledgement_fails(self, check_mod) -> None:
        """Both kinds of stub present, no real review -> FAIL."""
        items = [
            check_mod.CRItem(
                id=1, body="Review rate limited. Please try again later.",
                is_review=False,
            ),
            check_mod.CRItem(id=2, body=ACK_BODY, is_review=False),
        ]
        exit_code, message = check_mod.classify(items)
        assert exit_code == 1
        assert "FAIL" in message
        # The message is the only thing a human reads off a red gate, so it
        # must name every stub kind present, not just whichever branch the
        # implementation happened to test first.
        assert "rate-limit stub" in message
        assert "scaffolding" in message

    def test_rate_limit_only_message_does_not_mention_scaffolding(
        self, check_mod,
    ) -> None:
        """Control for the assertion above: with one stub kind present the
        message names that kind ALONE. Without this, a message that blindly
        listed both kinds every time would satisfy the mixed-case test."""
        items = [
            check_mod.CRItem(
                id=1, body="Review rate limited. Please try again later.",
                is_review=False,
            ),
        ]
        exit_code, message = check_mod.classify(items)
        assert exit_code == 1
        assert "rate-limit stub" in message
        assert "scaffolding" not in message

    def test_scaffolding_only_message_does_not_mention_rate_limit(
        self, check_mod,
    ) -> None:
        items = [check_mod.CRItem(id=1, body=ACK_BODY, is_review=False)]
        exit_code, message = check_mod.classify(items)
        assert exit_code == 1
        assert "scaffolding" in message
        assert "rate-limit stub" not in message


class TestCheckBotReview:
    """Integration tests that mock collect_coderabbit_items."""

    def test_absent_returns_ok_with_note(self, check_mod) -> None:
        with patch.object(check_mod, "collect_coderabbit_items", return_value=[]):
            exit_code, message = check_mod.check_bot_review("jaylfc", "taOS", 2419)
        assert exit_code == 0
        assert "absent, not stubbed" in message

    def test_only_stub_returns_fail(self, check_mod) -> None:
        items = [
            check_mod.CRItem(
                id=100, body="Review rate limited", is_review=False,
            ),
        ]
        with patch.object(check_mod, "collect_coderabbit_items", return_value=items), \
             patch.object(check_mod, "collect_pr_labels", return_value=set()):
            exit_code, message = check_mod.check_bot_review("jaylfc", "taOS", 2416)
        assert exit_code == 1
        assert "FAIL" in message
        assert "rate-limit stub" in message

    def test_real_review_returns_ok(self, check_mod) -> None:
        items = [
            check_mod.CRItem(
                id=100, body="## Review\n\n### Findings", is_review=True, review_state="COMMENTED",
            ),
            check_mod.CRItem(
                id=101, body="Line 42: potential bug here.", is_review=False,
            ),
        ]
        with patch.object(check_mod, "collect_coderabbit_items", return_value=items):
            exit_code, message = check_mod.check_bot_review("jaylfc", "taOS", 2366)
        assert exit_code == 0
        assert "real CodeRabbit review" in message

    def test_api_failure_returns_error(self, check_mod) -> None:
        with patch.object(check_mod, "collect_coderabbit_items", return_value=None):
            exit_code, message = check_mod.check_bot_review("jaylfc", "taOS", 99999)
        assert exit_code == 2
        assert "error" in message.lower()


class TestMain:
    def test_main_exit_1_on_stub(self, check_mod, capsys: pytest.CaptureFixture) -> None:
        items = [
            check_mod.CRItem(
                id=1, body="Review rate limited", is_review=False,
            ),
        ]
        with patch.object(
            check_mod, "collect_coderabbit_items", return_value=items,
        ), patch.object(check_mod, "collect_pr_labels", return_value=set()):
            rc = check_mod.main(["2416", "--owner", "jaylfc", "--repo", "taOS"])
        captured = capsys.readouterr()
        assert rc == 1
        assert "FAIL" in captured.out

    def test_main_exit_0_on_real_review(self, check_mod, capsys: pytest.CaptureFixture) -> None:
        items = [
            check_mod.CRItem(
                id=1, body="## Review\n\nFound an issue.", is_review=True, review_state="COMMENTED",
            ),
        ]
        with patch.object(
            check_mod, "collect_coderabbit_items", return_value=items,
        ):
            rc = check_mod.main(["2366", "--owner", "jaylfc", "--repo", "taOS"])
        captured = capsys.readouterr()
        assert rc == 0
        assert "PASS" in captured.out

    def test_main_exit_0_on_absent(self, check_mod, capsys: pytest.CaptureFixture) -> None:
        with patch.object(
            check_mod, "collect_coderabbit_items", return_value=[],
        ):
            rc = check_mod.main(["2419", "--owner", "jaylfc", "--repo", "taOS"])
        captured = capsys.readouterr()
        assert rc == 0
        assert "absent, not stubbed" in captured.out

    def test_main_exit_2_on_api_error(self, check_mod, capsys: pytest.CaptureFixture) -> None:
        with patch.object(
            check_mod, "collect_coderabbit_items", return_value=None,
        ):
            rc = check_mod.main(["99999", "--owner", "jaylfc", "--repo", "taOS"])
        captured = capsys.readouterr()
        assert rc == 2
        assert "error" in captured.out.lower()

    def test_main_label_waives_stub(self, check_mod, capsys: pytest.CaptureFixture) -> None:
        """The --label flag wires through to check_bot_review's waiver: a stub
        with the label waived via the CLI exits 0 and prints WAIVED."""
        items = [
            check_mod.CRItem(id=1, body="Review rate limited", is_review=False),
        ]
        with patch.object(check_mod, "collect_coderabbit_items", return_value=items), \
             patch.object(check_mod, "collect_pr_labels",
                          return_value={check_mod.DEFAULT_ALLOW_LABEL}):
            rc = check_mod.main(["2578", "--owner", "jaylfc", "--repo", "taOS"])
        captured = capsys.readouterr()
        assert rc == 0
        assert "WAIVED" in captured.out

    def test_main_custom_label_waives_stub(self, check_mod, capsys: pytest.CaptureFixture) -> None:
        """A custom --label value waives when that label is present."""
        items = [
            check_mod.CRItem(id=1, body="Review rate limited", is_review=False),
        ]
        with patch.object(check_mod, "collect_coderabbit_items", return_value=items), \
             patch.object(check_mod, "collect_pr_labels",
                          return_value={"my-custom-label"}):
            rc = check_mod.main(
                ["2578", "--owner", "jaylfc", "--repo", "taOS",
                 "--label", "my-custom-label"],
            )
        captured = capsys.readouterr()
        assert rc == 0
        assert "WAIVED" in captured.out

    def test_main_requires_pr_number(self, check_mod) -> None:
        with pytest.raises(SystemExit):
            check_mod.main([])


# ---------------------------------------------------------------------------
# collect_pr_labels(owner, repo, pr) -- API-fetched label set (mocked at _api_get)
# ---------------------------------------------------------------------------


class TestCollectPrLabels:
    def test_extracts_label_names(self, check_mod) -> None:
        payload = [{"labels": [{"name": "bot-review-allow"}, {"name": "bug"}]}]
        with patch.object(check_mod, "_api_get", return_value=payload):
            labels = check_mod.collect_pr_labels("jaylfc", "taOS", 2578)
        assert labels == {"bot-review-allow", "bug"}

    def test_empty_labels_returns_empty_set(self, check_mod) -> None:
        with patch.object(check_mod, "_api_get", return_value=[{"labels": []}]):
            labels = check_mod.collect_pr_labels("jaylfc", "taOS", 2578)
        assert labels == set()

    def test_none_on_api_failure(self, check_mod) -> None:
        with patch.object(check_mod, "_api_get", return_value=None):
            labels = check_mod.collect_pr_labels("jaylfc", "taOS", 2578)
        assert labels is None


# ---------------------------------------------------------------------------
# Waiver label (bot-review-allow) -- the override mechanism (tsk-4f2ix2)
# ---------------------------------------------------------------------------


class TestWaiverLabel:
    """bot-review-allow override label.

    Acceptance criteria from the task:
      1. WAIVER WORKS: a rate-limit stub AND the label -> exit 0, output WAIVED.
      2. MUTATION: same stub, label REMOVED -> exit 1 again.
      3. The waiver must not hide a real failure of a different kind.
    """

    def test_stub_waived_by_allow_label(self, check_mod) -> None:
        """1. WAIVER WORKS: rate-limit stub + label -> exit 0, WAIVED."""
        items = [
            check_mod.CRItem(id=1, body="Review rate limited", is_review=False),
        ]
        with patch.object(check_mod, "collect_coderabbit_items", return_value=items), \
             patch.object(check_mod, "collect_pr_labels",
                          return_value={check_mod.DEFAULT_ALLOW_LABEL}):
            exit_code, message = check_mod.check_bot_review("jaylfc", "taOS", 2578)
        assert exit_code == 0
        assert "WAIVED" in message
        assert check_mod.DEFAULT_ALLOW_LABEL in message

    def test_stub_not_waived_without_label(self, check_mod) -> None:
        """2. MUTATION: same stub, label REMOVED -> exit 1 again. Removing the
        label must restore the FAIL verdict, proving the waiver is a per-run
        override, not a blanket disable of the gate."""
        items = [
            check_mod.CRItem(id=1, body="Review rate limited", is_review=False),
        ]
        with patch.object(check_mod, "collect_coderabbit_items", return_value=items), \
             patch.object(check_mod, "collect_pr_labels", return_value=set()):
            exit_code, message = check_mod.check_bot_review("jaylfc", "taOS", 2578)
        assert exit_code == 1
        assert "FAIL" in message

    def test_stub_waived_then_label_removed_exits_1(self, check_mod) -> None:
        """The full mutation pair in one fixture: the SAME stub items, label
        present -> waived (exit 0), label removed -> exits 1 again. The label
        is the only switching variable between the two halves."""
        items = [
            check_mod.CRItem(id=1, body="Review rate limited", is_review=False),
        ]

        with patch.object(check_mod, "collect_coderabbit_items", return_value=items), \
             patch.object(check_mod, "collect_pr_labels",
                          return_value={check_mod.DEFAULT_ALLOW_LABEL}):
            code_with, msg_with = check_mod.check_bot_review("jaylfc", "taOS", 2578)
        assert code_with == 0
        assert "WAIVED" in msg_with

        with patch.object(check_mod, "collect_coderabbit_items", return_value=items), \
             patch.object(check_mod, "collect_pr_labels", return_value=set()):
            code_without, msg_without = check_mod.check_bot_review("jaylfc", "taOS", 2578)
        assert code_without == 1
        assert "FAIL" in msg_without

    def test_waiver_does_not_mask_api_error(self, check_mod) -> None:
        """3. The waiver must not hide a real failure of a different kind: a
        cannot-fetch (EXIT_ERROR) is NOT waived, even with the label, so the
        gate stays fail-closed on true infrastructure failure."""
        with patch.object(check_mod, "collect_coderabbit_items", return_value=None), \
             patch.object(check_mod, "collect_pr_labels",
                          return_value={check_mod.DEFAULT_ALLOW_LABEL}):
            exit_code, message = check_mod.check_bot_review("jaylfc", "taOS", 2578)
        assert exit_code == 2
        assert "error" in message.lower()

    def test_waiver_covers_scaffolding_stub(self, check_mod) -> None:
        """The waiver also covers the auto-generated-scaffolding verdict class --
        intended, because ack/summary stubs are infrastructural (a trigger
        accepted with no review produced), the same failure mode as the rate-limit
        stub. The label overrides both stub kinds equally."""
        items = [
            check_mod.CRItem(id=1, body=SUMMARY_BODY, is_review=False),
            check_mod.CRItem(id=2, body=ACK_BODY, is_review=False),
        ]
        with patch.object(check_mod, "collect_coderabbit_items", return_value=items), \
             patch.object(check_mod, "collect_pr_labels",
                          return_value={check_mod.DEFAULT_ALLOW_LABEL}):
            exit_code, message = check_mod.check_bot_review("jaylfc", "taOS", 2578)
        assert exit_code == 0
        assert "WAIVED" in message

    def test_waiver_message_never_says_pass(self, check_mod) -> None:
        """A waived gate must never print a message that looks like a genuine
        pass -- the output always says WAIVED so a human can tell it was
        overridden, not cleared by the bot."""
        items = [
            check_mod.CRItem(id=1, body="Review rate limited", is_review=False),
        ]
        with patch.object(check_mod, "collect_coderabbit_items", return_value=items), \
             patch.object(check_mod, "collect_pr_labels",
                          return_value={check_mod.DEFAULT_ALLOW_LABEL}):
            _, message = check_mod.check_bot_review("jaylfc", "taOS", 2578)
        assert "WAIVED" in message
        # The waiver message must not look like a real PASS -- "real CodeRabbit
        # review" only appears on a genuine pass, never on a waiver.
        assert "real CodeRabbit review" not in message

    def test_label_read_from_api_not_payload(self, check_mod) -> None:
        """The label is read from the API at run time, not from a stale event
        payload: collect_pr_labels is consulted (here mocked) and the stub
        verdict is flipped only when the API-served label is present."""
        items = [
            check_mod.CRItem(id=1, body="Review rate limited", is_review=False),
        ]
        with patch.object(check_mod, "collect_coderabbit_items", return_value=items), \
             patch.object(check_mod, "collect_pr_labels",
                          return_value={check_mod.DEFAULT_ALLOW_LABEL}) as mock_labels:
            exit_code, _ = check_mod.check_bot_review("jaylfc", "taOS", 2578)
        assert exit_code == 0
        mock_labels.assert_called_once_with("jaylfc", "taOS", 2578, None)

    def test_wrong_label_does_not_waive(self, check_mod) -> None:
        """A different label name is not the allow label -> still FAIL."""
        items = [
            check_mod.CRItem(id=1, body="Review rate limited", is_review=False),
        ]
        with patch.object(check_mod, "collect_coderabbit_items", return_value=items), \
             patch.object(check_mod, "collect_pr_labels", return_value={"some-other-label"}):
            exit_code, message = check_mod.check_bot_review("jaylfc", "taOS", 2578)
        assert exit_code == 1
        assert "FAIL" in message

    def test_label_fetch_failure_does_not_waive(self, check_mod) -> None:
        """If the labels API errors (cannot-see), the waiver does not apply --
        fail closed rather than assume the label is absent."""
        items = [
            check_mod.CRItem(id=1, body="Review rate limited", is_review=False),
        ]
        with patch.object(check_mod, "collect_coderabbit_items", return_value=items), \
             patch.object(check_mod, "collect_pr_labels", return_value=None):
            exit_code, message = check_mod.check_bot_review("jaylfc", "taOS", 2578)
        assert exit_code == 1
        assert "FAIL" in message

    def test_real_review_passes_without_label_fetch(self, check_mod) -> None:
        """A genuine review passes regardless of the waiver label -- and on the
        PASS path collect_pr_labels is NOT called (no extra API round-trip)."""
        items = [
            check_mod.CRItem(
                id=1, body="## Review\n\nFound an issue.", is_review=True,
                review_state="COMMENTED",
            ),
        ]
        with patch.object(check_mod, "collect_coderabbit_items", return_value=items), \
             patch.object(check_mod, "collect_pr_labels") as mock_labels:
            exit_code, _ = check_mod.check_bot_review("jaylfc", "taOS", 2578)
        assert exit_code == 0
        mock_labels.assert_not_called()

    def test_absent_pr_does_not_fetch_labels(self, check_mod) -> None:
        """On the absent path (no CR output) the waiver is irrelevant; labels
        are not fetched to avoid an unnecessary API call."""
        with patch.object(check_mod, "collect_coderabbit_items", return_value=[]), \
             patch.object(check_mod, "collect_pr_labels") as mock_labels:
            exit_code, _ = check_mod.check_bot_review("jaylfc", "taOS", 2578)
        assert exit_code == 0
        mock_labels.assert_not_called()


# ---------------------------------------------------------------------------
# Workflow YAML regression guard
# ---------------------------------------------------------------------------


class TestWorkflowTriggers:
    """The committed workflow YAML must re-run on verdict-changing activities."""

    def test_workflow_subscribes_to_labeled_unlabeled(self, check_mod) -> None:
        """The bot-review-gate workflow must re-run on `labeled` and `unlabeled`
        so applying and removing the override label both re-runs the gate --
        otherwise the waiver is neither applicable nor revokable."""
        workflow = REPO_ROOT / ".github" / "workflows" / "bot-review-gate.yml"
        spec = yaml.safe_load(workflow.read_text(encoding="utf-8"))
        trigger = spec.get("on", spec.get(True))
        types = set(trigger["pull_request"]["types"])
        for required in ("labeled", "unlabeled"):
            assert required in types, (
                f"bot-review-gate.yml does not re-run on {required}; the "
                "override label would be neither applicable nor revokable"
            )
        # The default activities must also remain present (no regression).
        for required in ("opened", "synchronize", "reopened"):
            assert required in types


class TestCheckRunVerdict:
    """Acceptance #1: a self-healed PR -- a stale FAILURE check run followed by
    a later SUCCESS on the SAME head SHA -- must not read gate-red.

    bot-review-gate triggers on every pull_request / pull_request_review event,
    and GitHub keeps a separate "Bot review gate" check run for each workflow
    run on the same SHA. mergeStateStatus keys off ANY failing check run, so a
    stale FAILURE left behind by a self-heal pins the PR on UNSTABLE forever.

    The gate anchors its verdict to the head SHA: the LATEST COMPLETED
    bot-review-gate check run is authoritative, so the later SUCCESS supersedes
    the stale FAILURE. Against code without check-run anchoring this fails to
    report the PR as green (the function does not exist, so the test errors).
    """

    HEAD_SHA = "1baa21a" + "0" * 34

    @staticmethod
    def _run(run_id, conclusion, started_at, /, *, in_progress=False):
        return {
            "id": run_id,
            "name": "Bot review gate",
            "head_sha": "1baa21a",
            "status": "in_progress" if in_progress else "completed",
            "conclusion": None if in_progress else conclusion,
            "started_at": started_at,
            "completed_at": started_at,
        }

    @staticmethod
    def _mock_list(check_runs):
        def side_effect(url, token=None):
            return [{"total_count": len(check_runs), "check_runs": check_runs}]
        return side_effect

    def test_stale_failure_then_success_is_not_red(self, check_mod) -> None:
        # The #2493 condition: a 10-day-old FAILURE and three SUCCESS runs, all
        # on the same head SHA. The latest completed run is success, so the
        # self-heal must read green.
        runs = [
            self._run(32071499652, "failure", "2026-08-17T21:30:58Z"),
            self._run(33070472073, "success", "2026-08-27T12:08:17Z"),
            self._run(33070472412, "success", "2026-08-27T12:08:18Z"),
            self._run(32072071321, "success", "2026-08-27T12:08:20Z"),
        ]
        with patch.object(check_mod, "_api_get", side_effect=self._mock_list(runs)):
            exit_code, message = check_mod.check_run_verdict("jaylfc", "taOS", self.HEAD_SHA)
        assert exit_code == 0
        assert "success" in message

    def test_only_stale_failure_is_red(self, check_mod) -> None:
        runs = [self._run(32071499652, "failure", "2026-08-17T21:30:58Z")]
        with patch.object(check_mod, "_api_get", side_effect=self._mock_list(runs)):
            exit_code, message = check_mod.check_run_verdict("jaylfc", "taOS", self.HEAD_SHA)
        assert exit_code == 1
        assert "failure" in message

    def test_latest_is_failure_even_with_older_success(self, check_mod) -> None:
        # A genuine failure that self-heals LATER is green; a success that is
        # then re-failed by a NEW stub is red. Latest wins.
        runs = [
            self._run(32071499652, "success", "2026-08-17T21:30:58Z"),
            self._run(33070472073, "failure", "2026-08-27T12:08:17Z"),
        ]
        with patch.object(check_mod, "_api_get", side_effect=self._mock_list(runs)):
            exit_code, message = check_mod.check_run_verdict("jaylfc", "taOS", self.HEAD_SHA)
        assert exit_code == 1
        assert "failure" in message

    def test_in_progress_latest_does_not_pretend_self_heal(self, check_mod) -> None:
        # An unsettled (in-progress) latest run must not by itself clear a stale
        # failure: latest_check_run_conclusion trusts only completed runs, so a
        # 'latest comment' never decides the verdict mid-run.
        runs = [
            self._run(32071499652, "failure", "2026-08-17T21:30:58Z"),
            self._run(33070472073, "success", "2026-08-27T12:08:17Z", in_progress=True),
        ]
        with patch.object(check_mod, "_api_get", side_effect=self._mock_list(runs)):
            exit_code, message = check_mod.check_run_verdict("jaylfc", "taOS", self.HEAD_SHA)
        assert exit_code == 1
        assert "failure" in message

    def test_no_check_run_is_not_red(self, check_mod) -> None:
        with patch.object(check_mod, "_api_get", side_effect=self._mock_list([])):
            exit_code, message = check_mod.check_run_verdict("jaylfc", "taOS", self.HEAD_SHA)
        assert exit_code == 0
        assert "no bot-review-gate check run" in message.lower() or "none" in message.lower()

    def test_api_failure_returns_error(self, check_mod) -> None:
        with patch.object(check_mod, "_api_get", return_value=None):
            exit_code, message = check_mod.check_run_verdict("jaylfc", "taOS", self.HEAD_SHA)
        assert exit_code == 2
        assert "error" in message.lower()

    def test_latest_check_run_conclusion_picks_latest_completed(self, check_mod) -> None:
        runs = [
            self._run(32071499652, "failure", "2026-08-17T21:30:58Z"),
            self._run(33070472073, "success", "2026-08-27T12:08:17Z"),
        ]
        assert check_mod.latest_check_run_conclusion(runs) == "success"

    def test_latest_check_run_conclusion_none_when_no_completed(self, check_mod) -> None:
        runs = [self._run(1, "success", "2026-08-27T12:08:17Z", in_progress=True)]
        assert check_mod.latest_check_run_conclusion(runs) is None

    def test_filters_to_bot_review_gate_runs_only(self, check_mod) -> None:
        runs = [
            self._run(32071499652, "failure", "2026-08-17T21:30:58Z"),
            {"id": 9, "name": "CodeRabbit", "status": "completed", "conclusion": "success",
             "started_at": "2026-08-28T00:00:00Z", "completed_at": "2026-08-28T00:00:00Z"},
        ]
        with patch.object(check_mod, "_api_get", side_effect=self._mock_list(runs)):
            exit_code, _ = check_mod.check_run_verdict("jaylfc", "taOS", self.HEAD_SHA)
        # The foreign "CodeRabbit" check run must not decide the verdict; only
        # the stale bot-review-gate failure remains, so it stays red.
        assert exit_code == 1


class TestReconcileCheckRun:
    """The #2493 fix in the write path: the self-heal run UPDATES the stale
    FAILURE check run on the head SHA instead of letting it coexist with the
    new SUCCESS (which pins mergeStateStatus on UNSTABLE)."""

    HEAD_SHA = "1baa21a" + "0" * 34

    @staticmethod
    def _run(run_id, conclusion, started_at):
        return {
            "id": run_id,
            "name": "Bot review gate",
            "head_sha": "1baa21a",
            "status": "completed",
            "conclusion": conclusion,
            "started_at": started_at,
            "completed_at": started_at,
        }

    def test_patches_stale_failure_to_success(self, check_mod) -> None:
        runs = [
            self._run(32071499652, "failure", "2026-08-17T21:30:58Z"),
            self._run(33070472073, "success", "2026-08-27T12:08:17Z"),
        ]
        with patch.object(check_mod, "_api_get", return_value=[{"total_count": 2, "check_runs": runs}]), \
             patch.object(check_mod, "_api_mutate") as mutate:
            result = check_mod.reconcile_head_sha_check_run("jaylfc", "taOS", self.HEAD_SHA, "success")
        assert result is not None
        # Only the stale failure (== target differs) is patched; the already-
        # success run is left alone (idempotent).
        assert mutate.call_count == 1
        call = mutate.call_args
        assert call.kwargs.get("method") == "PATCH"
        assert "32071499652" in call.args[0]
        assert call.args[1]["conclusion"] == "success"
        assert call.args[1]["status"] == "completed"

    def test_idempotent_when_all_match(self, check_mod) -> None:
        runs = [self._run(33070472073, "success", "2026-08-27T12:08:17Z")]
        with patch.object(check_mod, "_api_get", return_value=[{"total_count": 1, "check_runs": runs}]), \
             patch.object(check_mod, "_api_mutate") as mutate:
            check_mod.reconcile_head_sha_check_run("jaylfc", "taOS", self.HEAD_SHA, "success")
        assert mutate.call_count == 0

    def test_creates_when_absent(self, check_mod) -> None:
        with patch.object(check_mod, "_api_get", return_value=[{"total_count": 0, "check_runs": []}]), \
             patch.object(check_mod, "_api_mutate") as mutate:
            check_mod.reconcile_head_sha_check_run("jaylfc", "taOS", self.HEAD_SHA, "success")
        assert mutate.call_count == 1
        call = mutate.call_args
        assert call.kwargs.get("method") == "POST"
        assert call.args[1]["name"] == "Bot review gate"
        assert call.args[1]["head_sha"] == self.HEAD_SHA
        assert call.args[1]["conclusion"] == "success"

    def test_skips_in_progress_run(self, check_mod) -> None:
        runs = [
            {"id": 1, "name": "Bot review gate", "head_sha": self.HEAD_SHA,
             "status": "in_progress", "conclusion": None,
             "started_at": "2026-08-27T12:08:17Z", "completed_at": None},
            self._run(32071499652, "failure", "2026-08-17T21:30:58Z"),
        ]
        with patch.object(check_mod, "_api_get", return_value=[{"total_count": 2, "check_runs": runs}]), \
             patch.object(check_mod, "_api_mutate") as mutate:
            check_mod.reconcile_head_sha_check_run("jaylfc", "taOS", self.HEAD_SHA, "success")
        # Only the completed stale failure is patched; the in-progress run is
        # left for the job's exit code to settle.
        assert mutate.call_count == 1
        assert "32071499652" in mutate.call_args.args[0]

    def test_returns_none_on_api_failure(self, check_mod) -> None:
        with patch.object(check_mod, "_api_get", return_value=None), \
             patch.object(check_mod, "_api_mutate") as mutate:
            result = check_mod.reconcile_head_sha_check_run("jaylfc", "taOS", self.HEAD_SHA, "success")
        assert result is None
        assert mutate.call_count == 0

    def test_reconciles_all_stale_runs(self, check_mod) -> None:
        # Multiple stale FAILURE runs on the SHA all get updated so none is left
        # behind to pin mergeStateStatus on UNSTABLE.
        runs = [
            self._run(32071499652, "failure", "2026-08-17T21:30:58Z"),
            self._run(33070472073, "failure", "2026-08-27T12:08:17Z"),
        ]
        with patch.object(check_mod, "_api_get", return_value=[{"total_count": 2, "check_runs": runs}]), \
             patch.object(check_mod, "_api_mutate") as mutate:
            check_mod.reconcile_head_sha_check_run("jaylfc", "taOS", self.HEAD_SHA, "success")
        assert mutate.call_count == 2
        patched_ids = {call.args[0] for call in mutate.call_args_list}
        assert all("32071499652" in u or "33070472073" in u for u in patched_ids)


class TestDetectorIsolation:
    """Acceptance #2: each fake-green detector is non-vacuous in isolation, so
    neutering one cannot hide behind another. The 2026-08-16 audit measured
    that neutering both halves at once read green at 43/43 with an untested
    half masking the gap -- these tests prove each half stands alone.

    Each body below is matched by EXACTLY one detector. Neutering that detector
    loses protection for its body but leaves the other detectors' bodies caught,
    so no single neuter can stay green by leaning on a different detector."""

    RL_BODY = "Review rate limited. Please try again later."

    def test_rate_limit_body_rejected(self, check_mod) -> None:
        item = check_mod.CRItem(id=1, body=self.RL_BODY, is_review=True, review_state="APPROVED")
        assert not check_mod.is_real_item(item)

    def test_neutering_rate_limit_loses_only_its_protection(self, check_mod) -> None:
        with patch.object(check_mod, "is_rate_limit_stub", return_value=False):
            rl = check_mod.CRItem(id=1, body=self.RL_BODY, is_review=True, review_state="APPROVED")
            assert check_mod.is_real_item(rl) is True  # protection lost
            ack = check_mod.CRItem(id=2, body=ACK_BODY, is_review=True, review_state="APPROVED")
            assert not check_mod.is_real_item(ack)  # acknowledgement still caught

    def test_acknowledgement_body_rejected(self, check_mod) -> None:
        item = check_mod.CRItem(id=1, body=ACK_BODY, is_review=True, review_state="APPROVED")
        assert not check_mod.is_real_item(item)

    def test_neutering_acknowledgement_loses_only_its_protection(self, check_mod) -> None:
        with patch.object(check_mod, "is_coderabbit_acknowledgement", return_value=False):
            ack = check_mod.CRItem(id=1, body=ACK_BODY, is_review=True, review_state="APPROVED")
            assert check_mod.is_real_item(ack) is True  # protection lost
            summary = check_mod.CRItem(id=2, body=SUMMARY_BODY, is_review=True, review_state="APPROVED")
            assert not check_mod.is_real_item(summary)  # summary still caught

    def test_auto_summary_body_rejected(self, check_mod) -> None:
        item = check_mod.CRItem(id=1, body=SUMMARY_BODY, is_review=True, review_state="APPROVED")
        assert not check_mod.is_real_item(item)

    def test_neutering_auto_summary_loses_only_its_protection(self, check_mod) -> None:
        with patch.object(check_mod, "is_coderabbit_auto_summary", return_value=False):
            summary = check_mod.CRItem(id=1, body=SUMMARY_BODY, is_review=True, review_state="APPROVED")
            assert check_mod.is_real_item(summary) is True  # protection lost
            ack = check_mod.CRItem(id=2, body=ACK_BODY, is_review=True, review_state="APPROVED")
            assert not check_mod.is_real_item(ack)  # acknowledgement still caught

    def test_neutering_every_detector_loses_every_protection(self, check_mod) -> None:
        """The trap the audit caught, reproduced: neutering ALL stub detectors
        must let EVERY stub kind through (green), not stay red on one because an
        untested detector was left on. Each stub must flip independently."""
        with patch.object(check_mod, "is_rate_limit_stub", return_value=False), \
             patch.object(check_mod, "is_coderabbit_acknowledgement", return_value=False), \
             patch.object(check_mod, "is_coderabbit_auto_summary", return_value=False):
            rl = check_mod.CRItem(id=1, body=self.RL_BODY, is_review=True, review_state="APPROVED")
            ack = check_mod.CRItem(id=2, body=ACK_BODY, is_review=True, review_state="APPROVED")
            summary = check_mod.CRItem(id=3, body=SUMMARY_BODY, is_review=True, review_state="APPROVED")
            assert check_mod.is_real_item(rl) is True
            assert check_mod.is_real_item(ack) is True
            assert check_mod.is_real_item(summary) is True


class TestTrueGateFailure:
    """Acceptance #3: a TRUE gate failure (#2554 -- CodeRabbit scaffolding only,
    no review content) must STILL be reported red. The fix clears stale red by
    SUPERSISING a later verdict, never by weakening the gate, so a real
    failure that never self-heals stays red on the head SHA."""

    def _pr2554_items(self, check_mod):
        """#2554 condition: the only CodeRabbit output is auto-generated
        scaffolding (acknowledgement + auto-summary), no review content."""
        return [
            check_mod.CRItem(id=1, body=ACK_BODY, is_review=False),
            check_mod.CRItem(id=2, body=SUMMARY_BODY, is_review=False),
        ]

    def test_pr2554_scaffolding_only_is_red(self, check_mod) -> None:
        exit_code, message = check_mod.classify(self._pr2554_items(check_mod))
        assert exit_code == 1
        assert "FAIL" in message
        assert "scaffolding" in message

    def test_pr2554_check_run_stays_red_without_self_heal(self, check_mod) -> None:
        # #2554 never self-heals: its only bot-review-gate check run is a
        # FAILURE (scaffolding-only verdict), so latest-wins keeps it red.
        head = "deadbeef" + "0" * 32
        runs = [
            {"id": 1, "name": "Bot review gate", "head_sha": head,
             "status": "completed", "conclusion": "failure",
             "started_at": "2026-08-27T12:08:17Z", "completed_at": "2026-08-27T12:08:18Z"},
        ]
        with patch.object(check_mod, "_api_get", return_value=[{"total_count": 1, "check_runs": runs}]):
            exit_code, message = check_mod.check_run_verdict("jaylfc", "taOS", head)
        assert exit_code == 1
        assert "failure" in message

    def test_pr2554_review_and_check_run_both_red(self, check_mod) -> None:
        """The gate's two verdict surfaces agree on #2554: the review detection
        is red (scaffolding only) and the latest check run is red (no self-heal).
        A fix that clears one by weakening the other is a regression."""
        with patch.object(check_mod, "collect_coderabbit_items",
                          return_value=self._pr2554_items(check_mod)):
            exit_code, _ = check_mod.check_bot_review("jaylfc", "taOS", 2554,
                                                      token="t")
        # Review-detection surface on #2554: scaffolding-only, must be red.
        assert exit_code == 1
        ec2, msg2 = check_mod.classify(self._pr2554_items(check_mod))
        assert ec2 == 1
        assert "FAIL" in msg2
