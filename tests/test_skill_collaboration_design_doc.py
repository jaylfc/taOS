"""The skill-collaboration design doc carries two load-bearing invariants.

Both were raised in the #3222 lead review. They are prose, and nothing imports a
markdown file, so they are cheap to re-break silently:

1. the isolation unit is the **user** (or a project), not the taOS instance --
   a multi-user instance hosts several unrelated fleets, so "the same taOS
   instance" is not "a user's own agents";
2. S2 is blocked on the A2A bus redesign (maintainer hold since 2026-08-24), not
   only on the isolation question.

Each guard is paired with a mutation case that feeds it the pre-fix wording and
requires the guard to FAIL there, so a doc that quietly reverts to an
instance-scoped default, or drops the bus hold, does not keep passing.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
DOC = REPO_ROOT / "docs" / "design" / "skill-collaboration.md"

# Code surfaces the doc cites as evidence for the user/project boundary. If one
# of these moves, the doc's justification is stale and this test says so.
CITED_CODE_PATHS = [
    "tinyagentos/auth.py",
    "tinyagentos/auth_context.py",
    "tinyagentos/agent_registry_store.py",
    "tinyagentos/routes/a2a_bus.py",
]

# The wording the pre-fix doc used, kept verbatim so the mutation cases below
# test the real regression rather than a paraphrase of it.
PREFIX_ISOLATION = (
    "### 3.2 Instance isolation is a precondition, not later hardening\n"
    "\n"
    "The bus proxy authorizes reads on the `a2a_receive` grant alone and forwards\n"
    "the channel unfiltered, and the default bus URL is a **shared local service**.\n"
    "Two taOS instances pointed at one bus would therefore see each other's\n"
    "supplements.\n"
    "\n"
    "### 3.3 Who publishes, who subscribes\n"
    "\n"
    "- **Subscribers (S1):** default *on* within the same taOS instance (the user's\n"
    "  own fleet, which is the whole point: \"a user's OWN agents\"). Opt-out per\n"
    "  agent.\n"
    "\n"
    "### 3.4 What a subscription means\n"
)

_FIXED_ISOLATION = (
    "### 3.2 User (and project) isolation is a precondition, not later hardening\n"
    "\n"
    "The bus proxy forwards the channel unfiltered, so the unit this design has\n"
    "to isolate is the user -- not the instance. An agent belongs to the user who\n"
    "registered it (`agent_registry.user_id`).\n"
    "\n"
    "### 3.3 Who publishes, who subscribes\n"
    "\n"
    "- **Subscribers (S1):** default *on* within the same user's fleet, and\n"
    "  deliberately **not instance-wide**.\n"
    "\n"
    "### 3.4 What a subscription means\n"
)

PREFIX_S2 = (
    "**S2. Publish / subscribe on the `learning` channel.**\n"
    "- Files: `tinyagentos/guides/bus.py`, the instance-isolation setting in\n"
    "  `config.py`, a startup task in `app.py`.\n"
    "\n"
    "**S3. Review gate + #896 governance surface. MAINTAINER-REVIEW.**\n"
)


def _plain(text: str) -> str:
    """Drop markdown emphasis/backticks so prose guards survive re-formatting."""
    return text.replace("*", "").replace("`", "")


def _norm(text: str) -> str:
    """Case-folded prose: the doc writes "taOS", a reversion may write "taos"."""
    return _plain(text).lower()


def _section(text: str, start: str, end: str) -> str:
    i = text.index(start)
    j = text.index(end, i + len(start))
    return text[i:j]


def _sections(text: str) -> str:
    """3.2 through 3.3 inclusive: the isolation unit and the subscribe default."""
    return _section(text, "### 3.2", "### 3.4")


def _s2(text: str) -> str:
    return _section(text, "**S2. Publish", "**S3.")


def user_is_the_isolation_unit(text: str) -> bool:
    """Guard 1: the isolation unit is the user (or project), not the instance."""
    body = _norm(_sections(text))
    return (
        "not the instance" in body
        and "agent_registry.user_id" in body
        and "not instance-wide" in body
        and "within the same taos instance" not in body
    )


def _envelope_json(text: str) -> str:
    """The §4 supplement envelope, the one fenced ```json block in the section."""
    section = _section(
        text,
        "## 4. The supplement: data shape",
        "Rules that make the shape safe",
    )
    start = section.index("```json") + len("```json\n")
    end = section.index("```", start)
    return section[start:end]


def envelope_example_is_valid_json(text: str) -> bool:
    """Guard 3: an implementer can copy the envelope example and parse it."""
    try:
        parsed = json.loads(_envelope_json(text))
    except json.JSONDecodeError:
        return False
    return isinstance(parsed, dict) and parsed.get("kind") == "guide.supplement"


def s2_is_blocked_on_the_bus_redesign(text: str) -> bool:
    """Guard 2: S2 declares the bus redesign as a blocking dependency."""
    body = _plain(_s2(text)).lower()
    return "2026-08-24" in body and "blocked" in body and "redesign" in body


class TestSkillCollaborationDesignDoc:
    def test_doc_exists(self):
        assert DOC.is_file(), f"{DOC} is missing"

    def test_user_not_instance_is_the_isolation_unit(self):
        text = DOC.read_text(encoding="utf-8")
        assert user_is_the_isolation_unit(text), (
            "docs/design/skill-collaboration.md no longer scopes the isolation "
            "unit to the user (or project): a multi-user instance would "
            "subscribe one user's agents to another user's lessons."
        )

    def test_s2_declares_the_bus_redesign_as_a_blocker(self):
        text = DOC.read_text(encoding="utf-8")
        assert s2_is_blocked_on_the_bus_redesign(text), (
            "S2 no longer records that it is blocked on the A2A bus redesign "
            "(maintainer hold since 2026-08-24), so the slice reads as startable "
            "on the current proxy."
        )

    def test_open_questions_carry_the_bus_redesign(self):
        text = _plain(
            _section(
                DOC.read_text(encoding="utf-8"), "## 7. Open questions", "## 8."
            )
        )
        assert "2026-08-24" in text, (
            "section 7 no longer lists the bus redesign as a decision needed "
            "before S2"
        )

    def test_bus_table_row_records_the_hold(self):
        text = _plain(
            _section(
                DOC.read_text(encoding="utf-8"), "| Piece | State |", "## 3."
            )
        )
        assert "hold" in text.lower(), (
            "the §2 bus row no longer records that the bus is under a hold, so "
            "the 'already exists' framing overstates the transport's readiness"
        )

    def test_envelope_example_is_valid_json(self):
        text = DOC.read_text(encoding="utf-8")
        assert envelope_example_is_valid_json(text), (
            "the §4 supplement envelope no longer parses as JSON, so anyone "
            "copying the example verbatim hits a parse error"
        )

    def test_cited_code_surfaces_exist(self):
        text = DOC.read_text(encoding="utf-8")
        missing = [
            p
            for p in CITED_CODE_PATHS
            if p in text and not (REPO_ROOT / p).exists()
        ]
        assert not missing, (
            "the doc cites code paths that are not in the tree:\n"
            + "\n".join(f"  FAIL  {p}" for p in missing)
        )

    def test_agent_registry_carries_an_owning_user(self):
        """The doc's user boundary rests on this column; assert it is real."""
        schema = (REPO_ROOT / "tinyagentos" / "agent_registry_store.py").read_text(
            encoding="utf-8"
        )
        assert re.search(r"\buser_id\s+TEXT", schema), (
            "agent_registry has no user_id column, so 'the agents that user "
            "owns' is no longer enforceable"
        )


class TestGuardMutation:
    """The guards must FAIL on the pre-fix wording, or they prove nothing."""

    def test_isolation_guard_rejects_the_instance_scoped_default(self):
        assert not user_is_the_isolation_unit(PREFIX_ISOLATION)

    def test_isolation_guard_rejects_a_case_mixed_partial_reversion(self):
        """The negative check must bite on "taOS", not only on "taos".

        A partial reversion that restores the instance-scoped default keeps the
        mixed-case spelling the doc uses, so a case-sensitive comparison would
        miss exactly the regression this guard exists for.
        """
        fixed = _FIXED_ISOLATION.replace(
            "default *on*", "default *on* within the same taOS instance"
        )
        assert user_is_the_isolation_unit(fixed) is False

    def test_isolation_guard_accepts_the_fixed_wording(self):
        assert user_is_the_isolation_unit(_FIXED_ISOLATION)

    def test_bus_redesign_guard_rejects_the_pre_fix_slice(self):
        assert not s2_is_blocked_on_the_bus_redesign(PREFIX_S2)

    def test_bus_redesign_guard_accepts_a_declared_blocker(self):
        fixed = PREFIX_S2.replace(
            "- Files:",
            "- BLOCKED on the A2A bus redesign (hold since 2026-08-24).\n- Files:",
        )
        assert s2_is_blocked_on_the_bus_redesign(fixed)

    def test_json_guard_rejects_a_js_style_comment(self):
        """A `//` note inside the envelope block is what Kilo flagged."""
        text = (
            "## 4. The supplement: data shape\n"
            "\n"
            "```json\n"
            "{\n"
            '  "kind": "guide.supplement",\n'
            '  "status": "draft|review|fleet",   // retraction is a tombstone\n'
            "}\n"
            "```\n"
            "\n"
            "Rules that make the shape safe:\n"
        )
        assert envelope_example_is_valid_json(text) is False

    def test_json_guard_accepts_the_comment_free_block(self):
        text = (
            "## 4. The supplement: data shape\n"
            "\n"
            "```json\n"
            '{\n  "kind": "guide.supplement",\n  "status": "draft|review|fleet"\n}\n'
            "```\n"
            "\n"
            "Rules that make the shape safe:\n"
        )
        assert envelope_example_is_valid_json(text) is True


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(pytest.main([__file__, "-q"]))
