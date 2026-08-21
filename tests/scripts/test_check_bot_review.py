"""Tests for scripts/check_bot_review.py."""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

_SCRIPT = Path(__file__).resolve().parent.parent.parent / "scripts" / "check_bot_review.py"


def _load_module():
    spec = importlib.util.spec_from_file_location("check_bot_review", _SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    sys.modules["check_bot_review"] = mod
    spec.loader.exec_module(mod)
    return mod


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
        with patch.object(check_mod, "collect_coderabbit_items", return_value=items):
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
        ):
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

    def test_main_requires_pr_number(self, check_mod) -> None:
        with pytest.raises(SystemExit):
            check_mod.main([])
