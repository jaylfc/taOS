"""System prompt assembly — layered pieces → single string."""
from __future__ import annotations

import logging
from pathlib import Path

logger = logging.getLogger(__name__)

STRICT_READ_DIRECTIVE = (
    "Read this document end-to-end. Do not skim, summarise, or truncate. "
    "Every section below is load-bearing."
)

_SEPARATOR = "\n\n---\n\n"


def _load_agent_rules() -> str:
    """Read `docs/agent-rules.md` from the installed taosmd package.

    Prefers the public `taosmd.agent_rules()` helper introduced by
    taosmd #21 / PR #23 — it reads from `taosmd/docs/agent-rules.md`
    inside the package via `importlib.resources`, so it works on wheel
    installs where there is no `docs/` sibling of the package directory.

    Falls back to the legacy repo-layout walk-up (`<pkg>/../docs/`) for
    very old editable-only installs of taosmd that predate the helper.
    Returns empty string with a warning if neither path resolves — agents
    still boot, just without the memory-usage contract in their prompt.
    """
    try:
        import taosmd
    except ImportError:
        logger.warning("taosmd not installed — skipping agent-rules block")
        return ""
    agent_rules = getattr(taosmd, "agent_rules", None)
    if callable(agent_rules):
        try:
            return agent_rules()
        except Exception:
            logger.exception("taosmd.agent_rules() raised — falling back to repo walk")
    pkg_root = Path(taosmd.__file__).resolve().parent.parent
    rules_path = pkg_root / "docs" / "agent-rules.md"
    if not rules_path.exists():
        logger.warning("taosmd agent-rules.md missing at %s", rules_path)
        return ""
    return rules_path.read_text(encoding="utf-8")


def _taosmd_agent_rules(slug: str) -> str:
    raw = _load_agent_rules()
    if not raw:
        return ""
    return raw.replace("<your-agent-name>", slug)


def assemble_system_prompt(agent) -> str:
    """Assemble the agent's system prompt from its record fields.

    Pure function — call every turn rather than caching.
    """
    parts: list[str] = [STRICT_READ_DIRECTIVE]
    if getattr(agent, "memory_plugin", "taosmd") == "taosmd":
        rules = _taosmd_agent_rules(agent.slug)
        if rules:
            parts.append(rules)
    if getattr(agent, "soul_md", ""):
        parts.append(agent.soul_md)
    if getattr(agent, "agent_md", ""):
        parts.append(agent.agent_md)
    return _SEPARATOR.join(parts)
