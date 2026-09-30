"""Doc-contract invariants for ``docs/design/cross-node-summarization.md``.

The spike is doc-only, so the only artifact that can drift silently is the
contract the document declares.  jaylfc's Lead review on #3225 (2026-09-29)
flagged that the ``summaries`` schema (section 4.3) and its vector metadata
carried ``agent_name`` but no owner: under per-user agent namespacing both the
store and the RAG splice must be user-scoped, or one user's summaries become
retrievable by another.  It also flagged that "default is on" (section 5) is a
product decision, not a design one, and belongs in the open questions.

These tests pin both fixes and are structural checks over the document itself,
in the spirit of ``tests/test_agent_onboarding_doc_paths.py``: they fail against
the pre-review revision of the doc (head ``0e70f8bb``) and pass once the
ownership contract is written down.
"""

import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
DESIGN_DOC = REPO_ROOT / "docs" / "design" / "cross-node-summarization.md"

OWNER = "user_id"

_SCHEMA_RE = re.compile(r"summaries\(\n(?P<body>.*?)\n\)", re.S)
_COLUMN_RE = re.compile(r"^\s*(?P<name>[a-z_]+)\s+TEXT", re.M)
_PRIMARY_KEY_RE = re.compile(r"PRIMARY KEY\s*\(([^)]*)\)")
# A "key tuple" is a parenthesised, comma-separated list of bare identifiers,
# e.g. ``(user_id, conversation_id, segment_id, source_sha256)``.  Prose in
# parentheses (``(`source_sha256` mismatch)``) and value payloads
# (``{..., kind: "summary"}``) are deliberately not key tuples.
_KEY_TUPLE_RE = re.compile(r"\(([^()]*)\)")
_IDENTIFIER_RE = re.compile(r"^`?[A-Za-z_][A-Za-z0-9_]*`?$")


def _key_tuples() -> list[str]:
    """Every key-shaped tuple in the doc that keys on ``source_sha256``."""
    found = []
    for tup in _KEY_TUPLE_RE.findall(_doc()):
        parts = [part.strip() for part in tup.split(",")]
        if len(parts) < 2 or not all(_IDENTIFIER_RE.match(p) for p in parts):
            continue
        if "source_sha256" in parts:
            found.append(tup)
    return found


def _doc() -> str:
    return DESIGN_DOC.read_text(encoding="utf-8")


def _schema_body() -> str:
    match = _SCHEMA_RE.search(_doc())
    assert match, "section 4.3 must declare the summaries(...) schema"
    return match.group("body")


class TestSummaryStoreIsOwnerScoped:
    def test_schema_declares_an_owner_column(self):
        columns = _COLUMN_RE.findall(_schema_body())
        assert OWNER in columns, (
            f"the summaries schema must declare an owner column ({OWNER}); "
            f"declared columns: {columns}"
        )

    def test_primary_key_includes_the_owner(self):
        match = _PRIMARY_KEY_RE.search(_schema_body())
        assert match, "the summaries schema must declare a PRIMARY KEY"
        columns = [c.strip() for c in match.group(1).split(",")]
        assert OWNER in columns, (
            f"PRIMARY KEY must be owner-scoped so two users' rows cannot "
            f"collide: {columns}"
        )

    def test_every_staleness_keyed_tuple_is_owner_scoped(self):
        tuples = _key_tuples()
        assert len(tuples) >= 3, (
            "expected the primary key, the ON CONFLICT target and the direct "
            f"lookup tier to key on source_sha256; found {tuples}"
        )
        ownerless = [tup for tup in tuples if OWNER not in tup]
        assert not ownerless, (
            "every source_sha256-keyed tuple must be owner-scoped, but these "
            f"are not: {ownerless}"
        )


class TestVectorMetadataCarriesOwner:
    def test_metadata_json_includes_the_owner(self):
        match = re.search(r"metadata_json.*?\{([^}]*)\}", _doc(), re.S)
        assert match, "expected the vector metadata_json payload to be described"
        payload = match.group(1)
        assert OWNER in payload, f"vector metadata must carry the owner: {payload}"
        assert 'kind: "summary"' in payload


class TestDefaultOnIsAnOpenQuestion:
    def test_off_able_row_does_not_assert_a_default(self):
        match = re.search(
            r"\| \*\*Summarisation must be off-able\*\* \|[^\n]*", _doc()
        )
        assert match, "section 5 must describe the per-agent off switch"
        assert "Default is on" not in match.group(0), (
            "the off-switch row must not assert a default; that is a product "
            "decision (open question 7)"
        )

    def test_default_on_off_is_recorded_as_a_product_decision(self):
        parts = _doc().split("## 9. Open questions", 1)
        assert len(parts) == 2, "the doc must keep a section 9 open questions"
        open_questions = parts[1]
        assert re.search(r"(?i)default", open_questions), (
            "the default on/off choice must be listed as an open question"
        )
        assert re.search(r"(?i)product decision", open_questions), (
            "the default question must be recorded as a product decision"
        )
