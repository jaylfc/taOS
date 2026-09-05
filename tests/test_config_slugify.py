"""Non-ASCII agent names must slugify to distinct, non-empty slugs.

The ASCII-only character class the slugifier used to carry deleted every
CJK/Cyrillic/Greek/Arabic/Hebrew/Thai code point *before* the emptiness check,
so a name made entirely of letters was rejected as containing none, and the
registry's ``or "agent"`` sentinel gave every such name the same slug -- a
shared identity prefix in the one table that exists to hold distinct agent
identities.
"""

import re
import tomllib
from pathlib import Path

import pytest
import pytest_asyncio

from tinyagentos.agent_registry_store import AgentRegistryStore, _slugify
from tinyagentos.config import (
    AppConfig,
    slugify_agent_name,
    unique_agent_slug,
    validate_agent_name,
)

# One name per script the old ASCII class silently emptied.
NON_ASCII_NAMES = {
    "arabic": "عميل",
    "chinese": "我的代理",
    "cyrillic": "Агент Иванов",
    "greek": "Ελληνικά",
    "hebrew": "סוכן",
    "japanese": "日本語エージェント",
    "korean": "한국어 에이전트",
    "thai": "ตัวแทน",
}

# mint_canonical_id appends -YYYYMMDD-HHMMSS, plus a 2-hex suffix when the same
# slug is minted twice in one second. Strip it to recover the slug half.
_CANONICAL_TAIL_RE = re.compile(r"-\d{8}-\d{6}(?:-[0-9a-f]{2})?$")

REPO_ROOT = Path(__file__).resolve().parent.parent


def canonical_slug(canonical_id: str) -> str:
    """Return the slug half of *canonical_id* (the timestamp tail removed)."""
    return _CANONICAL_TAIL_RE.sub("", canonical_id)


@pytest_asyncio.fixture
async def store(tmp_path):
    s = AgentRegistryStore(tmp_path / "agent_registry.db")
    await s.init()
    yield s
    await s.close()


class TestNonAsciiNamesAreAccepted:
    @pytest.mark.parametrize("script,name", sorted(NON_ASCII_NAMES.items()))
    def test_a_non_ascii_agent_name_is_accepted(self, script, name):
        assert validate_agent_name(name) is None

    @pytest.mark.parametrize("script,name", sorted(NON_ASCII_NAMES.items()))
    def test_a_non_ascii_agent_name_produces_a_non_empty_slug(self, script, name):
        slug = slugify_agent_name(name)
        assert slug != ""
        assert slug == slugify_agent_name(name), "slugification must be deterministic"

    def test_distinct_non_ascii_names_produce_distinct_slugs(self):
        slugs = [slugify_agent_name(n) for n in NON_ASCII_NAMES.values()]
        assert len(set(slugs)) == len(slugs)

    def test_accents_fold_to_their_base_letter_instead_of_being_dropped(self):
        # "naïve résumé" used to mangle into "na-ve-r-sum".
        assert slugify_agent_name("naïve résumé") == "naive-resume"
        assert slugify_agent_name("München") == "munchen"

    def test_a_cyrillic_homoglyph_of_a_reserved_word_still_resolves_to_it(self):
        # Transliteration closes a bypass: "усер" now slugs to "user", which
        # the reserved-prefix guard rejects, instead of vanishing to "".
        assert slugify_agent_name("усер") == "user"


class TestSlugifyKeepsAsciiBehaviour:
    """The new slugifier applies at creation time only, so ASCII must not drift."""

    @pytest.mark.parametrize(
        "name,expected",
        [
            ("My Agent", "my-agent"),
            ("Mary's Coding Buddy", "mary-s-coding-buddy"),
            ("🚀 Alpha v2", "alpha-v2"),
            ("Agent_42!", "agent-42"),
            ("agent@v2.0!", "agent-v2-0"),
            ("a  b  c", "a-b-c"),
            ("  hello  ", "hello"),
            ("TaOS Agent", "taos-agent"),
            ("!@#$%", ""),
            ("", ""),
        ],
    )
    def test_ascii_slug_is_unchanged(self, name, expected):
        assert slugify_agent_name(name) == expected


class TestTheAgentSentinelIsGone:
    def test_the_registry_slugifier_no_longer_returns_the_agent_sentinel(self):
        assert _slugify("") == ""
        assert _slugify("!@#$%") == ""


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "first_name,second_name",
    [
        ("我的代理", "Агент Иванов"),
        # Nothing survives either of these, so only a per-name fallback can
        # keep the two identities apart.
        ("🚀", "🎉"),
    ],
)
async def test_two_non_ascii_names_in_the_same_second_get_distinct_canonical_ids(
    store, first_name, second_name
):
    first = await store.register(display_name=first_name, framework="claude-code")
    second = await store.register(display_name=second_name, framework="claude-code")
    assert canonical_slug(first["canonical_id"]) != canonical_slug(
        second["canonical_id"]
    )
    assert first["canonical_id"] != second["canonical_id"]


@pytest.mark.asyncio
async def test_a_non_ascii_agent_is_findable_by_the_slug_of_its_own_name(store):
    rec = await store.register(display_name="我的代理", framework="claude-code")
    found = await store.get_by_slug(slugify_agent_name("我的代理"))
    assert found is not None
    assert found["canonical_id"] == rec["canonical_id"]


class TestTheGplUnidecodeIsNeverInstalled:
    """``python-slugify[unidecode]`` pulls GPL-only ``Unidecode``.

    The extra is a blocker for the commercial arm of the dual licence (see
    docs/dependency-licences.md). Without it python-slugify uses
    ``text-unidecode``, whose Artistic-1.0 arm we elect. Enforce that
    mechanically so a dependency refresh cannot quietly swap it in.
    """

    def test_pyproject_never_requests_the_unidecode_extra(self):
        text = (REPO_ROOT / "pyproject.toml").read_text(encoding="utf-8")
        assert "python-slugify[unidecode]" not in text

    def test_the_lockfile_never_resolves_the_gpl_unidecode(self):
        lock = tomllib.loads((REPO_ROOT / "uv.lock").read_text(encoding="utf-8"))
        # text-unidecode is the permissive one and is expected; bare
        # "unidecode" is the GPL-only package and must never appear.
        names = {pkg["name"] for pkg in lock["package"]}
        assert "unidecode" not in names
        assert "text-unidecode" in names


class TestUniqueAgentSlugRespectsTheLengthCap:
    def test_a_deduped_63_char_slug_still_fits_the_container_name_limit(self):
        base = "a" * 63
        cfg = AppConfig(agents=[{"name": base}])
        deduped = unique_agent_slug(cfg, base)
        assert deduped != base
        assert len(deduped) <= 63
