from __future__ import annotations

import fnmatch
import logging
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from tinyagentos.knowledge_store import KnowledgeStore

logger = logging.getLogger(__name__)


class CategoryEngine:
    """Assigns categories to KnowledgeItems.

    Rule-based matching runs first (free). LLM fallback fires only
    when no rules produce a match.
    """

    def __init__(self, store: "KnowledgeStore", http_client=None, llm_url: str = "") -> None:
        self._store = store
        self._http_client = http_client
        self._llm_url = llm_url

    async def categorise(
        self,
        source_type: str,
        source_url: str,
        title: str,
        summary: str,
        metadata: dict,
    ) -> list[str]:
        """Return a list of category strings for the given item attributes.

        Checks all rules in priority order. A rule's pattern uses glob
        matching (``*`` wildcard). Multiple rules can match, each adding
        its category. If no rules match, ``_llm_categorise`` is called.
        """
        rules = await self._store.list_rules()
        matched: list[str] = []

        # Build lookup values for each match_on field
        lookup: dict[str, str] = {
            "source_type": source_type,
            "source_url": source_url,
            "title": title.lower(),
            "subreddit": metadata.get("subreddit", ""),
            "channel": metadata.get("channel", ""),
            "author": metadata.get("author", ""),
        }

        for rule in rules:
            field_value = lookup.get(rule["match_on"], "")
            pattern = rule["pattern"]
            pattern_lower = pattern.lower()
            value_lower = field_value.lower()
            # Try direct match, case-insensitive match, and prefix-wildcard match
            # (so "github.com/rockchip*" matches "https://github.com/rockchip-...")
            if (
                fnmatch.fnmatch(field_value, pattern)
                or fnmatch.fnmatch(value_lower, pattern_lower)
                or fnmatch.fnmatch(value_lower, "*" + pattern_lower)
            ):
                if rule["category"] not in matched:
                    matched.append(rule["category"])

        if not matched:
            try:
                matched = await self._llm_categorise(
                    source_type=source_type,
                    source_url=source_url,
                    title=title,
                    summary=summary,
                )
            except Exception as exc:
                logger.warning("LLM category fallback failed: %s", exc)

        return matched

    async def _llm_categorise(
        self,
        source_type: str,
        source_url: str,
        title: str,
        summary: str,
    ) -> list[str]:
        """Call the LLM to suggest 1-3 categories for an unmatched item.

        Sends a short prompt to the configured LLM endpoint. Returns an
        empty list if the LLM is unavailable, so the caller can still
        proceed without categories.
        """
        if not self._http_client or not self._llm_url:
            return []

        # Fetch existing categories to guide the LLM
        rules = await self._store.list_rules()
        existing_cats = list({r["category"] for r in rules})

        prompt = (
            f"You are categorising a saved knowledge item.\n"
            f"Title: {title}\n"
            f"Source type: {source_type}\n"
            f"URL: {source_url}\n"
            f"Summary: {summary}\n"
            f"Existing categories: {', '.join(existing_cats) if existing_cats else 'none yet'}\n\n"
            f"Respond with a JSON array of 1-3 category strings. "
            f"Prefer existing categories when they fit. "
            f"Only propose a new category if none fit. "
            f"Example: [\"AI/ML\", \"Development\"]"
        )

        resp = await self._http_client.post(
            self._llm_url,
            json={"prompt": prompt, "max_tokens": 60},
            timeout=30,
        )
        resp.raise_for_status()
        data = resp.json()
        raw = data.get("text", data.get("content", "[]"))

        import json
        try:
            categories = json.loads(raw)
            if isinstance(categories, list):
                return [str(c) for c in categories[:3]]
        except Exception:
            pass
        return []
