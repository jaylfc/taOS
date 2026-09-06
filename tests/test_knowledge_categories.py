from __future__ import annotations
import pytest
import pytest_asyncio
from pathlib import Path
from unittest.mock import AsyncMock, patch
from tinyagentos.knowledge_store import KnowledgeStore
from tinyagentos.knowledge_categories import CategoryEngine


@pytest_asyncio.fixture
async def store(tmp_path):
    s = KnowledgeStore(tmp_path / "knowledge.db", media_dir=tmp_path / "media")
    await s.init()
    await s.add_rule(pattern="LocalLLaMA", match_on="subreddit", category="AI/ML", priority=10)
    await s.add_rule(pattern="github.com/rockchip*", match_on="source_url", category="Rockchip", priority=5)
    await s.add_rule(pattern="github", match_on="source_type", category="Development", priority=1)
    yield s
    await s.close()


@pytest.fixture
def engine(store):
    return CategoryEngine(store)


@pytest.mark.asyncio
async def test_rule_match_subreddit(engine):
    categories = await engine.categorise(
        source_type="reddit",
        source_url="https://reddit.com/r/LocalLLaMA/comments/abc",
        title="Cool post",
        summary="About LLMs.",
        metadata={"subreddit": "LocalLLaMA"},
    )
    assert "AI/ML" in categories


@pytest.mark.asyncio
async def test_rule_match_source_url_glob(engine):
    categories = await engine.categorise(
        source_type="github",
        source_url="https://github.com/rockchip-linux/rknn-toolkit2",
        title="RKNN Toolkit",
        summary="NPU toolkit.",
        metadata={},
    )
    assert "Rockchip" in categories
    assert "Development" in categories  # source_type=github rule also fires


@pytest.mark.asyncio
async def test_rule_match_source_type(engine):
    categories = await engine.categorise(
        source_type="github",
        source_url="https://github.com/some/repo",
        title="Some Repo",
        summary="Generic repo.",
        metadata={},
    )
    assert "Development" in categories


@pytest.mark.asyncio
async def test_no_rule_match_calls_llm_fallback(engine):
    """When no rules match, the LLM fallback should be called."""
    engine._llm_categorise = AsyncMock(return_value=["Hardware"])
    categories = await engine.categorise(
        source_type="article",
        source_url="https://unknownsite.com/post",
        title="Some obscure post",
        summary="About hardware.",
        metadata={},
    )
    engine._llm_categorise.assert_awaited_once()
    assert "Hardware" in categories


@pytest.mark.asyncio
async def test_glob_wildcard_matching(engine):
    """* in pattern should match any substring."""
    categories = await engine.categorise(
        source_type="github",
        source_url="https://github.com/rockchip-extra/rknpu",
        title="RKNPU",
        summary="NPU driver.",
        metadata={},
    )
    assert "Rockchip" in categories


@pytest.mark.asyncio
async def test_llm_fallback_skipped_when_rules_match(engine):
    """_llm_categorise must NOT be called when a rule already matched."""
    engine._llm_categorise = AsyncMock(return_value=["Irrelevant"])
    await engine.categorise(
        source_type="reddit",
        source_url="https://reddit.com/r/LocalLLaMA/comments/abc",
        title="Post",
        summary="Summary.",
        metadata={"subreddit": "LocalLLaMA"},
    )
    engine._llm_categorise.assert_not_awaited()
