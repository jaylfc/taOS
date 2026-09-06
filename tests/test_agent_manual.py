"""Tests for tinyagentos.agent_manual.build_manual."""
import pytest

from tinyagentos.agent_manual import build_manual


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _dm_channel():
    return {"id": "c1", "type": "dm", "settings": {}}


def _group_channel():
    return {"id": "c2", "type": "group", "settings": {"response_mode": "quiet"}}


def _a2a_project_channel(leads=None):
    return {
        "id": "c3",
        "type": "group",
        "project_id": "proj-1",
        "settings": {
            "kind": "a2a",
            "leads": leads or [],
        },
    }


def _token_estimate(text: str) -> float:
    """Simple heuristic: words * 1.3 (matches spec)."""
    return len(text.split()) * 1.3


# ---------------------------------------------------------------------------
# DM channel
# ---------------------------------------------------------------------------

class TestDMChannel:
    def test_dm_has_header(self):
        text = build_manual(_dm_channel(), "alice", [])
        assert "taOS" in text
        assert "operating manual" in text

    def test_dm_has_dm_stub(self):
        text = build_manual(_dm_channel(), "alice", [])
        assert "1-on-1" in text or "direct-message" in text

    def test_dm_no_verbs_section(self):
        # The verbs section header must not appear (stub may mention the word)
        text = build_manual(_dm_channel(), "alice", [])
        assert "## 2. Project task verbs" not in text
        assert "/new" not in text

    def test_dm_no_lead_section(self):
        text = build_manual(_dm_channel(), "alice", [])
        assert "Lead vs non-lead" not in text

    def test_dm_no_mention_routing_section(self):
        # The section header must not appear (stub mentions the term to say it doesn't apply)
        text = build_manual(_dm_channel(), "alice", [])
        assert "## 1. @-mentions are how messages route" not in text

    def test_dm_token_budget(self):
        text = build_manual(_dm_channel(), "alice", [])
        assert _token_estimate(text) <= 200, (
            f"DM manual exceeds 200-token budget: {_token_estimate(text):.0f} tokens"
        )


# ---------------------------------------------------------------------------
# Group channel without project_id
# ---------------------------------------------------------------------------

class TestGroupChannelNoProject:
    def test_has_mention_section(self):
        text = build_manual(_group_channel(), "alice", [])
        assert "@-mention routing" in text

    def test_has_quick_ref_section(self):
        text = build_manual(_group_channel(), "alice", [])
        assert "Quick reference" in text

    def test_no_task_verbs_section(self):
        text = build_manual(_group_channel(), "alice", [])
        assert "kanban board" not in text

    def test_no_lead_section(self):
        text = build_manual(_group_channel(), "alice", [])
        assert "Lead vs non-lead" not in text


# ---------------------------------------------------------------------------
# Project a2a channel — lead agent
# ---------------------------------------------------------------------------

class TestProjectA2AChannelLead:
    def setup_method(self):
        self.channel = _a2a_project_channel(leads=["coord"])
        self.text = build_manual(self.channel, "coord", ["coord"])

    def test_has_mention_section(self):
        assert "@-mention routing" in self.text

    def test_has_task_verbs_section(self):
        assert "kanban board" in self.text

    def test_has_lead_section(self):
        assert "Lead vs non-lead" in self.text

    def test_lead_branch_text(self):
        assert "You ARE designated lead" in self.text

    def test_has_quick_ref_section(self):
        assert "Quick reference" in self.text

    def test_no_non_lead_text(self):
        assert "You are NOT a lead" not in self.text


# ---------------------------------------------------------------------------
# Project a2a channel — non-lead agent
# ---------------------------------------------------------------------------

class TestProjectA2AChannelNonLead:
    def setup_method(self):
        self.channel = _a2a_project_channel(leads=["coord"])
        self.text = build_manual(self.channel, "worker", ["coord"])

    def test_has_mention_section(self):
        assert "@-mention routing" in self.text

    def test_has_task_verbs_section(self):
        assert "kanban board" in self.text

    def test_has_lead_section(self):
        assert "Lead vs non-lead" in self.text

    def test_non_lead_branch_text(self):
        assert "You are NOT a lead" in self.text

    def test_has_quick_ref_section(self):
        assert "Quick reference" in self.text

    def test_no_lead_text(self):
        assert "You ARE designated lead" not in self.text


# ---------------------------------------------------------------------------
# Project a2a channel — empty leads list
# ---------------------------------------------------------------------------

class TestProjectA2AEmptyLeads:
    def test_empty_leads_gives_non_lead_branch(self):
        channel = _a2a_project_channel(leads=[])
        text = build_manual(channel, "worker", [])
        assert "You are NOT a lead" in text
        assert "You ARE designated lead" not in text


# ---------------------------------------------------------------------------
# Token budget — longest branch (lead in project a2a)
# ---------------------------------------------------------------------------

class TestTokenBudget:
    def test_longest_branch_under_500_tokens(self):
        channel = _a2a_project_channel(leads=["coord"])
        text = build_manual(channel, "coord", ["coord"])
        estimate = _token_estimate(text)
        assert estimate <= 500, (
            f"Manual exceeds 500-token budget: {estimate:.0f} estimated tokens"
        )
