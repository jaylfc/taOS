"""Agent template library — built-in + vendored templates from external repos.

Vendored data in data/templates/:
- openclaw-agents.json  — 196 agents from mergisi/awesome-openclaw-agents (MIT)
- system-prompts.json   — 1259 prompts from danielrosehill/System-Prompt-Library (CC BY 4.0)
"""
from __future__ import annotations

import json
import logging
from pathlib import Path

logger = logging.getLogger(__name__)

DATA_DIR = Path(__file__).parent.parent / "data" / "templates"

CATEGORIES = [
    "productivity", "development", "marketing", "finance",
    "education", "creative", "customer-support", "research",
    "devops", "data-science", "legal", "hr",
    "automation", "business", "compliance", "ecommerce",
    "freelance", "healthcare", "personal", "real-estate",
    "saas", "security", "supply-chain", "voice",
    "customer-success", "moltbook", "general",
]

TEMPLATES = [
    {
        "id": "research-assistant",
        "name": "Research Assistant",
        "category": "research",
        "description": "Searches the web, summarises papers, and compiles research briefs",
        "system_prompt": "You are a research assistant. Your job is to help the user find, summarise, and organise information. Always cite your sources. Present findings in clear, structured format with key takeaways first.",
        "color": "#4ecdc4",
    },
    {
        "id": "code-reviewer",
        "name": "Code Reviewer",
        "category": "development",
        "description": "Reviews pull requests, finds bugs, suggests improvements",
        "system_prompt": "You are a senior code reviewer. Review code for bugs, security issues, performance problems, and style inconsistencies. Be constructive and specific. Always explain why something is a problem and suggest a fix.",
        "color": "#ff6b6b",
    },
    {
        "id": "content-writer",
        "name": "Content Writer",
        "category": "marketing",
        "description": "Writes blog posts, social media copy, and marketing content",
        "system_prompt": "You are a professional content writer. Write engaging, clear, and persuasive content. Adapt your tone to the target audience. Use active voice, short paragraphs, and compelling headlines.",
        "color": "#ffd93d",
    },
    {
        "id": "customer-support",
        "name": "Customer Support Agent",
        "category": "customer-support",
        "description": "Handles customer inquiries, troubleshoots issues, escalates when needed",
        "system_prompt": "You are a friendly customer support agent. Help customers resolve their issues quickly and professionally. If you cannot solve a problem, acknowledge it and offer to escalate. Always be empathetic and patient.",
        "color": "#45b7d1",
    },
    {
        "id": "data-analyst",
        "name": "Data Analyst",
        "category": "data-science",
        "description": "Analyses datasets, creates summaries, identifies trends and anomalies",
        "system_prompt": "You are a data analyst. Analyse data to find patterns, trends, and insights. Present findings with clear visualisation descriptions and actionable recommendations. Always note data quality issues and limitations.",
        "color": "#96ceb4",
    },
    {
        "id": "meeting-summariser",
        "name": "Meeting Summariser",
        "category": "productivity",
        "description": "Summarises meeting transcripts into action items and key decisions",
        "system_prompt": "You are a meeting summariser. Given a meeting transcript, extract: 1) Key decisions made, 2) Action items with owners and deadlines, 3) Open questions, 4) Brief summary (3-5 sentences). Be concise and structured.",
        "color": "#dda0dd",
    },
    {
        "id": "devops-assistant",
        "name": "DevOps Assistant",
        "category": "devops",
        "description": "Helps with CI/CD, Docker, Kubernetes, infrastructure automation",
        "system_prompt": "You are a DevOps engineer. Help with infrastructure automation, CI/CD pipelines, containerisation, monitoring, and deployment. Prioritise reliability, security, and reproducibility. Suggest infrastructure-as-code approaches.",
        "color": "#ff8c42",
    },
    {
        "id": "tutor",
        "name": "Personal Tutor",
        "category": "education",
        "description": "Explains concepts, creates practice exercises, adapts to learning level",
        "system_prompt": "You are a patient and encouraging tutor. Explain concepts clearly using analogies and examples. Check understanding by asking questions. Adapt your explanations to the learner's level. Break complex topics into manageable steps.",
        "color": "#a8e6cf",
    },
    {
        "id": "creative-writer",
        "name": "Creative Writer",
        "category": "creative",
        "description": "Writes stories, poetry, scripts, and creative fiction",
        "system_prompt": "You are a creative writer. Write engaging fiction, poetry, and scripts with vivid imagery and compelling characters. Experiment with different styles and genres. Help users develop their creative ideas into polished pieces.",
        "color": "#e056a0",
    },
    {
        "id": "finance-advisor",
        "name": "Finance Advisor",
        "category": "finance",
        "description": "Analyses financial data, explains concepts, helps with budgeting",
        "system_prompt": "You are a financial advisor. Help with budgeting, financial analysis, and explaining financial concepts. Always note that you provide general information, not personalised financial advice. Be precise with numbers and cite relevant regulations.",
        "color": "#2ecc71",
    },
    {
        "id": "legal-assistant",
        "name": "Legal Research Assistant",
        "category": "legal",
        "description": "Researches legal topics, summarises regulations, drafts documents",
        "system_prompt": "You are a legal research assistant. Help find relevant laws, regulations, and case precedents. Summarise legal documents in plain language. Always note that you provide legal information, not legal advice, and recommend consulting a qualified lawyer.",
        "color": "#8b7355",
    },
    {
        "id": "hr-assistant",
        "name": "HR Assistant",
        "category": "hr",
        "description": "Helps with job descriptions, interview questions, HR policies",
        "system_prompt": "You are an HR assistant. Help draft job descriptions, create interview questions, summarise HR policies, and assist with employee communications. Be inclusive and professional. Follow best practices for diversity and compliance.",
        "color": "#c39bd3",
    },
]

# Alias used by persona-memory code — same list, persona-only fields.
BUILTIN_TEMPLATES = TEMPLATES


EXTERNAL_SOURCES = [
    {
        "id": "awesome-openclaw",
        "name": "Awesome OpenClaw Agents",
        "repo": "mergisi/awesome-openclaw-agents",
        "description": "202 agent personas across 24 categories",
        "url": "https://github.com/mergisi/awesome-openclaw-agents",
        "license": "MIT",
        "format": "soul-md",
        "count": 202,
    },
    {
        "id": "system-prompt-library",
        "name": "System Prompt Library",
        "repo": "danielrosehill/System-Prompt-Library",
        "description": "937+ system prompts with metadata and capability flags",
        "url": "https://github.com/danielrosehill/System-Prompt-Library",
        "license": "CC BY 4.0",
        "format": "json",
        "count": 937,
    },
]


async def fetch_external_index(source_id: str, http_client) -> list[dict]:
    """Fetch template index from an external GitHub source via the API.
    Returns a list of template stubs (id, name, category, path) without full content."""
    source = next((s for s in EXTERNAL_SOURCES if s["id"] == source_id), None)
    if not source:
        return []

    repo = source["repo"]
    try:
        if source["format"] == "soul-md":
            # Fetch the repo tree to find SOUL.md files
            resp = await http_client.get(
                f"https://api.github.com/repos/{repo}/git/trees/main?recursive=1",
                headers={"Accept": "application/vnd.github.v3+json"},
                timeout=15,
            )
            if resp.status_code != 200:
                return []
            tree = resp.json().get("tree", [])
            templates = []
            for item in tree:
                path = item.get("path", "")
                if path.endswith("/SOUL.md") and path.startswith("agents/"):
                    parts = path.split("/")
                    if len(parts) >= 3:
                        category = parts[1]
                        name = parts[2]
                        templates.append({
                            "id": f"{source_id}:{name}",
                            "name": name.replace("-", " ").title(),
                            "category": category,
                            "source": source_id,
                            "path": path,
                            "description": f"{category} agent from {source['name']}",
                        })
            return templates

        elif source["format"] == "json":
            resp = await http_client.get(
                f"https://api.github.com/repos/{repo}/git/trees/main?recursive=1",
                headers={"Accept": "application/vnd.github.v3+json"},
                timeout=15,
            )
            if resp.status_code != 200:
                return []
            tree = resp.json().get("tree", [])
            templates = []
            for item in tree:
                path = item.get("path", "")
                if path.startswith("system-prompts/json/") and path.endswith(".json"):
                    filename = path.rsplit("/", 1)[-1].replace(".json", "")
                    templates.append({
                        "id": f"{source_id}:{filename}",
                        "name": filename.replace("-", " ").replace("_", " ").title(),
                        "category": "general",
                        "source": source_id,
                        "path": path,
                        "description": f"System prompt from {source['name']}",
                    })
            return templates

    except Exception:
        return []

    return []


async def fetch_external_template(source_id: str, path: str, http_client) -> dict | None:
    """Fetch the full content of a single external template."""
    source = next((s for s in EXTERNAL_SOURCES if s["id"] == source_id), None)
    if not source:
        return None

    repo = source["repo"]
    try:
        raw_url = f"https://raw.githubusercontent.com/{repo}/main/{path}"
        resp = await http_client.get(raw_url, timeout=10)
        if resp.status_code != 200:
            return None

        if source["format"] == "soul-md":
            content = resp.text
            parts = path.split("/")
            category = parts[1] if len(parts) >= 2 else "general"
            name = parts[2] if len(parts) >= 3 else "agent"
            return {
                "id": f"{source_id}:{name}",
                "name": name.replace("-", " ").title(),
                "category": category,
                "source": source_id,
                "system_prompt": content,
                "color": "#888888",
            }

        elif source["format"] == "json":
            import json
            data = json.loads(resp.text)
            return {
                "id": f"{source_id}:{path.rsplit('/', 1)[-1].replace('.json', '')}",
                "name": data.get("agent_name", data.get("title", "Agent")),
                "category": "general",
                "source": source_id,
                "system_prompt": data.get("System Prompt", data.get("system_prompt", "")),
                "description": data.get("Description", data.get("description", "")),
                "color": "#888888",
            }

    except Exception:
        return None

    return None


def _load_vendored() -> list[dict]:
    """Load vendored templates from JSON files in data/templates/."""
    templates = []
    if not DATA_DIR.exists():
        return templates
    for path in DATA_DIR.glob("*.json"):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(data, list):
                templates.extend(data)
        except Exception as e:
            logger.warning(f"Failed to load vendored templates from {path}: {e}")
    return templates


# Cache vendored templates on first access
_vendored_cache: list[dict] | None = None


def _get_vendored() -> list[dict]:
    global _vendored_cache
    if _vendored_cache is None:
        _vendored_cache = _load_vendored()
    return _vendored_cache


def vendored_templates() -> list[dict]:
    """Public accessor for vendored templates loaded from data/templates/."""
    return _get_vendored()


def list_templates(category: str | None = None, source: str | None = None,
                   include_vendored: bool = True) -> list[dict]:
    """Return templates, optionally filtered by category and/or source.

    source: "builtin", "awesome-openclaw-agents", "system-prompt-library", or None for all.
    """
    all_templates = list(TEMPLATES)
    if include_vendored:
        all_templates.extend(_get_vendored())

    if source == "builtin":
        all_templates = [t for t in all_templates if "source" not in t]
    elif source:
        all_templates = [t for t in all_templates if t.get("source") == source]

    if category:
        all_templates = [t for t in all_templates if t["category"] == category]

    return all_templates


def get_template(template_id: str) -> dict | None:
    """Get a single template by ID (searches built-in + vendored)."""
    # Check built-in first
    for t in TEMPLATES:
        if t["id"] == template_id:
            return t
    # Check vendored
    for t in _get_vendored():
        if t["id"] == template_id:
            return t
    return None


def template_stats() -> dict:
    """Return template count stats."""
    vendored = _get_vendored()
    by_source: dict[str, int] = {}
    for t in vendored:
        src = t.get("source", "unknown")
        by_source[src] = by_source.get(src, 0) + 1
    return {
        "builtin": len(TEMPLATES),
        "vendored": len(vendored),
        "total": len(TEMPLATES) + len(vendored),
        "by_source": by_source,
    }
