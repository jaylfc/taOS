# Chat Phase 1 — Conversational Core Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Turn the existing 1:1-per-agent chat into a functional multi-agent room: agents see each other, decide when to reply, can't loop-spam, and respect `@mentions`, reactions, and slash commands.

**Architecture:** `AgentChatRouter` now fans out every non-system message (user or agent, minus self) to other agent members, gated by channel mode (quiet/lively), mention state, a hop counter, per-agent cooldown, and channel-wide rate cap. Each bridge event carries a rolling context window; bridges can suppress a reply with the literal string `NO_RESPONSE`. 11 slash commands cover channel admin; two reactions (`👎`, `🙋`) have semantics. Spec at `docs/superpowers/specs/2026-04-19-chat-phase-1-conversational-core-design.md`.

**Tech Stack:** Python 3.12, FastAPI, pytest + pytest-asyncio, existing `tinyagentos/chat/*` stores, in-container Python bridges (httpx + per-framework SDKs).

---

## File Structure

**New files:**
- `tinyagentos/chat/mentions.py` — mention parser
- `tinyagentos/chat/group_policy.py` — hop/cooldown/rate tracker
- `tinyagentos/chat/context_window.py` — rolling-window builder
- `tinyagentos/chat/slash_commands.py` — 11 command handlers + dispatcher
- `tinyagentos/chat/reactions.py` — semantic-reaction dispatcher
- `tests/test_chat_mentions.py`
- `tests/test_chat_group_policy.py`
- `tests/test_chat_context_window.py`
- `tests/test_chat_slash_commands.py`
- `tests/test_chat_reactions.py`

**Modified files:**
- `tinyagentos/agent_chat_router.py` — fanout + mentions + hops + policy
- `tinyagentos/bridge_session.py` — force_respond, context, re-dispatch, hops metadata
- `tinyagentos/routes/openclaw.py` — event payload, regenerate reply handling
- `tinyagentos/routes/chat.py` — slash intercept, reactions endpoints, context endpoint
- `tinyagentos/chat/channel_store.py` — default settings on read + setter helpers
- `tinyagentos/chat/message_store.py` — propagate `metadata.hops_since_user` on send
- `tinyagentos/scripts/install_hermes.sh` — NO_RESPONSE + context + force_respond
- `tinyagentos/scripts/install_smolagents.sh` — same
- `tinyagentos/scripts/install_langroid.sh` — same
- `tinyagentos/scripts/install_pocketflow.sh` — same
- `tinyagentos/scripts/install_openai_agents_sdk.sh` — same (underscored copy)
- `tinyagentos/scripts/install_openai-agents-sdk.sh` — same (dashed copy)
- `tests/test_agent_chat_router.py` — extended coverage
- `tests/e2e/test_roundtable_group_chat.py` — E2E (new; extending conventions)

---

## Task 1: Channel settings defaults + mutation helpers

**Files:**
- Modify: `tinyagentos/chat/channel_store.py`
- Test: `tests/test_chat_channel_settings.py` (new)

- [ ] **Step 1: Write the failing test**

```python
# tests/test_chat_channel_settings.py
import pytest
from tinyagentos.chat.channel_store import ChannelStore, PHASE1_DEFAULT_SETTINGS


@pytest.mark.asyncio
async def test_get_channel_backfills_phase1_defaults(tmp_path):
    store = ChannelStore(tmp_path / "chat.db")
    await store.init()
    ch_id = await store.create_channel(
        name="test", type="group", description="", topic="",
        members=["user", "tom"], settings={}, created_by="user",
    )
    ch = await store.get_channel(ch_id)
    for key, default in PHASE1_DEFAULT_SETTINGS.items():
        assert ch["settings"][key] == default


@pytest.mark.asyncio
async def test_set_response_mode_persists(tmp_path):
    store = ChannelStore(tmp_path / "chat.db")
    await store.init()
    ch_id = await store.create_channel(
        name="t", type="group", description="", topic="",
        members=["user", "tom"], settings={}, created_by="user",
    )
    await store.set_response_mode(ch_id, "lively")
    ch = await store.get_channel(ch_id)
    assert ch["settings"]["response_mode"] == "lively"


@pytest.mark.asyncio
async def test_mute_and_unmute_agent(tmp_path):
    store = ChannelStore(tmp_path / "chat.db")
    await store.init()
    ch_id = await store.create_channel(
        name="t", type="group", description="", topic="",
        members=["user", "tom", "don"], settings={}, created_by="user",
    )
    await store.mute_agent(ch_id, "tom")
    ch = await store.get_channel(ch_id)
    assert "tom" in ch["settings"]["muted"]
    await store.unmute_agent(ch_id, "tom")
    ch = await store.get_channel(ch_id)
    assert "tom" not in ch["settings"]["muted"]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_chat_channel_settings.py -v`
Expected: FAIL — `PHASE1_DEFAULT_SETTINGS`, `set_response_mode`, `mute_agent` not defined.

- [ ] **Step 3: Implement defaults and helpers**

In `tinyagentos/chat/channel_store.py`, add near the top (after imports):

```python
PHASE1_DEFAULT_SETTINGS = {
    "response_mode": "quiet",
    "max_hops": 3,
    "cooldown_seconds": 5,
    "rate_cap_per_minute": 20,
    "muted": [],
}
```

Modify `_parse_channel` to backfill defaults:

```python
def _parse_channel(row: tuple, description) -> dict:
    # ... existing parse ...
    ch = {...}
    settings = ch.get("settings") or {}
    for key, default in PHASE1_DEFAULT_SETTINGS.items():
        settings.setdefault(key, default if not isinstance(default, list) else list(default))
    ch["settings"] = settings
    return ch
```

Add methods inside the `ChannelStore` class (after `set_settings`):

```python
async def set_response_mode(self, channel_id: str, mode: str) -> None:
    if mode not in ("quiet", "lively"):
        raise ValueError(f"invalid response_mode: {mode}")
    await self.set_settings(channel_id, {"response_mode": mode})

async def set_max_hops(self, channel_id: str, hops: int) -> None:
    if not 1 <= hops <= 10:
        raise ValueError("max_hops must be 1..10")
    await self.set_settings(channel_id, {"max_hops": hops})

async def set_cooldown_seconds(self, channel_id: str, seconds: int) -> None:
    if not 0 <= seconds <= 60:
        raise ValueError("cooldown_seconds must be 0..60")
    await self.set_settings(channel_id, {"cooldown_seconds": seconds})

async def mute_agent(self, channel_id: str, slug: str) -> None:
    ch = await self.get_channel(channel_id)
    if ch is None:
        return
    muted = set(ch["settings"].get("muted") or [])
    muted.add(slug)
    await self.set_settings(channel_id, {"muted": sorted(muted)})

async def unmute_agent(self, channel_id: str, slug: str) -> None:
    ch = await self.get_channel(channel_id)
    if ch is None:
        return
    muted = set(ch["settings"].get("muted") or [])
    muted.discard(slug)
    await self.set_settings(channel_id, {"muted": sorted(muted)})
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_chat_channel_settings.py -v`
Expected: 3 passed.

- [ ] **Step 5: Commit**

```bash
git add tinyagentos/chat/channel_store.py tests/test_chat_channel_settings.py
git commit -m "feat(chat): channel settings defaults + mutation helpers for Phase 1 routing"
```

---

## Task 2: Mentions parser

**Files:**
- Create: `tinyagentos/chat/mentions.py`
- Test: `tests/test_chat_mentions.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_chat_mentions.py
from tinyagentos.chat.mentions import parse_mentions, MentionSet


def test_single_slug():
    assert parse_mentions("hey @tom what's up", ["tom", "don"]) == MentionSet(
        explicit=("tom",), all=False, humans=False
    )


def test_multiple_slugs_sorted_and_deduped():
    m = parse_mentions("@tom @don @tom please", ["tom", "don"])
    assert m.explicit == ("don", "tom")


def test_at_all():
    m = parse_mentions("@all please respond", ["tom"])
    assert m.all is True
    assert m.explicit == ()


def test_at_humans():
    m = parse_mentions("@humans heads up", ["tom"])
    assert m.humans is True


def test_non_member_slug_ignored():
    m = parse_mentions("@unknown help", ["tom"])
    assert m.explicit == ()


def test_word_boundary_email_not_mention():
    m = parse_mentions("email@tom.com send", ["tom"])
    assert m.explicit == ()


def test_case_insensitive():
    m = parse_mentions("@TOM stand up", ["tom"])
    assert m.explicit == ("tom",)


def test_empty_text():
    m = parse_mentions("", ["tom"])
    assert m.explicit == ()
    assert m.all is False
    assert m.humans is False
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_chat_mentions.py -v`
Expected: FAIL — module doesn't exist.

- [ ] **Step 3: Implement the parser**

```python
# tinyagentos/chat/mentions.py
"""@mention parser for multi-agent chat routing.

Produces a MentionSet describing which agents a message directly addresses.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

_MENTION_RE = re.compile(r"(?<![A-Za-z0-9_])@([A-Za-z0-9_-]+)(?![A-Za-z0-9_])")
_SPECIAL_ALL = "all"
_SPECIAL_HUMANS = "humans"


@dataclass(frozen=True)
class MentionSet:
    explicit: tuple[str, ...]
    all: bool
    humans: bool


def parse_mentions(text: str, members: list[str]) -> MentionSet:
    if not text:
        return MentionSet(explicit=(), all=False, humans=False)
    canonical_members = {m.lower() for m in members}
    raw = [m.group(1).lower() for m in _MENTION_RE.finditer(text)]
    has_all = _SPECIAL_ALL in raw
    has_humans = _SPECIAL_HUMANS in raw
    explicit = {m for m in raw if m not in (_SPECIAL_ALL, _SPECIAL_HUMANS) and m in canonical_members}
    return MentionSet(
        explicit=tuple(sorted(explicit)),
        all=has_all,
        humans=has_humans,
    )
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_chat_mentions.py -v`
Expected: 8 passed.

- [ ] **Step 5: Commit**

```bash
git add tinyagentos/chat/mentions.py tests/test_chat_mentions.py
git commit -m "feat(chat): mention parser (@slug/@all/@humans) for router fanout"
```

---

## Task 3: Group policy (cooldown + rate cap)

**Files:**
- Create: `tinyagentos/chat/group_policy.py`
- Test: `tests/test_chat_group_policy.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_chat_group_policy.py
import time
import pytest
from tinyagentos.chat.group_policy import GroupPolicy

SETTINGS = {"cooldown_seconds": 5, "rate_cap_per_minute": 20}


def test_first_send_allowed():
    p = GroupPolicy()
    assert p.may_send("ch1", "tom", SETTINGS) is True


def test_cooldown_blocks_same_agent():
    p = GroupPolicy()
    p.record_send("ch1", "tom")
    assert p.may_send("ch1", "tom", SETTINGS) is False


def test_cooldown_different_agents_independent():
    p = GroupPolicy()
    p.record_send("ch1", "tom")
    assert p.may_send("ch1", "don", SETTINGS) is True


def test_cooldown_different_channels_independent():
    p = GroupPolicy()
    p.record_send("ch1", "tom")
    assert p.may_send("ch2", "tom", SETTINGS) is True


def test_cooldown_elapses(monkeypatch):
    p = GroupPolicy()
    t = [1000.0]
    monkeypatch.setattr("tinyagentos.chat.group_policy._now", lambda: t[0])
    p.record_send("ch1", "tom")
    t[0] = 1004.9
    assert p.may_send("ch1", "tom", SETTINGS) is False
    t[0] = 1005.1
    assert p.may_send("ch1", "tom", SETTINGS) is True


def test_rate_cap_blocks_channel(monkeypatch):
    p = GroupPolicy()
    t = [1000.0]
    monkeypatch.setattr("tinyagentos.chat.group_policy._now", lambda: t[0])
    for i in range(20):
        t[0] += 0.1
        p.record_send("ch1", f"agent{i}")
    t[0] += 0.1
    assert p.may_send("ch1", "agent_new", SETTINGS) is False


def test_rate_cap_window_slides(monkeypatch):
    p = GroupPolicy()
    t = [1000.0]
    monkeypatch.setattr("tinyagentos.chat.group_policy._now", lambda: t[0])
    for i in range(20):
        p.record_send("ch1", f"agent{i}")
        t[0] += 0.1
    t[0] += 61.0
    assert p.may_send("ch1", "agent_new", SETTINGS) is True
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_chat_group_policy.py -v`
Expected: FAIL — module doesn't exist.

- [ ] **Step 3: Implement the policy**

```python
# tinyagentos/chat/group_policy.py
"""Per-channel per-agent cooldown + channel-wide rate cap.

In-memory, single-process (matches current taOS router architecture).
Thread-safe enough for asyncio single-threaded use.
"""
from __future__ import annotations

import time
from collections import deque


def _now() -> float:
    return time.monotonic()


class GroupPolicy:
    def __init__(self) -> None:
        self._last_send_at: dict[tuple[str, str], float] = {}
        self._recent_sends: dict[str, deque[float]] = {}

    def may_send(self, channel_id: str, agent: str, settings: dict) -> bool:
        now = _now()
        cooldown = int(settings.get("cooldown_seconds", 5))
        cap = int(settings.get("rate_cap_per_minute", 20))

        last = self._last_send_at.get((channel_id, agent))
        if last is not None and (now - last) < cooldown:
            return False

        window = self._recent_sends.get(channel_id)
        if window:
            while window and (now - window[0]) > 60.0:
                window.popleft()
            if len(window) >= cap:
                return False
        return True

    def record_send(self, channel_id: str, agent: str) -> None:
        now = _now()
        self._last_send_at[(channel_id, agent)] = now
        window = self._recent_sends.setdefault(channel_id, deque(maxlen=256))
        window.append(now)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_chat_group_policy.py -v`
Expected: 7 passed.

- [ ] **Step 5: Commit**

```bash
git add tinyagentos/chat/group_policy.py tests/test_chat_group_policy.py
git commit -m "feat(chat): GroupPolicy — per-agent cooldown + per-channel rate cap"
```

---

## Task 4: Context window builder

**Files:**
- Create: `tinyagentos/chat/context_window.py`
- Test: `tests/test_chat_context_window.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_chat_context_window.py
from tinyagentos.chat.context_window import build_context_window, estimate_tokens


def _msg(author, content, kind="user"):
    return {"author_id": author, "author_type": kind, "content": content}


def test_build_preserves_order_oldest_first():
    msgs = [_msg("user", "a"), _msg("tom", "b", "agent"), _msg("user", "c")]
    ctx = build_context_window(msgs, limit=20, max_tokens=1000)
    assert [m["content"] for m in ctx] == ["a", "b", "c"]


def test_build_skips_system_messages():
    msgs = [_msg("user", "hi"), _msg("system", "/lively enabled", "system"),
            _msg("tom", "yo", "agent")]
    ctx = build_context_window(msgs, limit=20, max_tokens=1000)
    assert [m["content"] for m in ctx] == ["hi", "yo"]


def test_build_applies_limit_dropping_oldest():
    msgs = [_msg("user", str(i)) for i in range(30)]
    ctx = build_context_window(msgs, limit=20, max_tokens=100000)
    assert len(ctx) == 20
    assert ctx[0]["content"] == "10"
    assert ctx[-1]["content"] == "29"


def test_build_applies_token_budget():
    long = "x" * 2000
    msgs = [_msg("user", long), _msg("tom", long, "agent"), _msg("user", long)]
    ctx = build_context_window(msgs, limit=20, max_tokens=800)
    assert sum(estimate_tokens(m["content"]) for m in ctx) <= 800


def test_build_empty():
    assert build_context_window([], limit=20, max_tokens=1000) == []


def test_estimate_tokens_4chars_per_token():
    assert estimate_tokens("") == 0
    assert estimate_tokens("abcd") == 1
    assert estimate_tokens("a" * 100) == 25
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_chat_context_window.py -v`
Expected: FAIL — module doesn't exist.

- [ ] **Step 3: Implement the helper**

```python
# tinyagentos/chat/context_window.py
"""Rolling context-window builder for per-bridge chat context.

Takes a list of channel messages (oldest-first) and returns a trimmed window
respecting both a message count limit and a token budget. Drops oldest
messages first when trimming. System messages (slash-command echoes) are
excluded entirely.
"""
from __future__ import annotations


def estimate_tokens(text: str) -> int:
    if not text:
        return 0
    return len(text) // 4


def build_context_window(messages: list[dict], *, limit: int, max_tokens: int) -> list[dict]:
    eligible = [m for m in messages if m.get("author_type") != "system"]
    if len(eligible) > limit:
        eligible = eligible[-limit:]
    while eligible and sum(estimate_tokens(m.get("content", "")) for m in eligible) > max_tokens:
        eligible = eligible[1:]
    return [
        {
            "author_id": m.get("author_id"),
            "author_type": m.get("author_type"),
            "content": m.get("content") or "",
        }
        for m in eligible
    ]
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_chat_context_window.py -v`
Expected: 6 passed.

- [ ] **Step 5: Commit**

```bash
git add tinyagentos/chat/context_window.py tests/test_chat_context_window.py
git commit -m "feat(chat): context-window builder for per-bridge rolling history"
```

---

## Task 5: Message metadata hops_since_user propagation

**Files:**
- Modify: `tinyagentos/chat/message_store.py`
- Test: `tests/test_chat_messages.py` (extend)

- [ ] **Step 1: Write the failing test**

Add to `tests/test_chat_messages.py`:

```python
@pytest.mark.asyncio
async def test_send_message_persists_hops_metadata(tmp_path):
    from tinyagentos.chat.message_store import ChatMessageStore
    store = ChatMessageStore(tmp_path / "msgs.db")
    await store.init()
    msg = await store.send_message(
        channel_id="c1", author_id="tom", author_type="agent",
        content="yo", content_type="text", state="complete",
        metadata={"hops_since_user": 2, "other": "x"},
    )
    assert msg["metadata"]["hops_since_user"] == 2
    assert msg["metadata"]["other"] == "x"


@pytest.mark.asyncio
async def test_send_message_defaults_hops_zero_when_absent(tmp_path):
    from tinyagentos.chat.message_store import ChatMessageStore
    store = ChatMessageStore(tmp_path / "msgs.db")
    await store.init()
    msg = await store.send_message(
        channel_id="c1", author_id="user", author_type="user",
        content="hi", content_type="text", state="complete",
        metadata=None,
    )
    assert msg["metadata"].get("hops_since_user", 0) == 0
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_chat_messages.py -v -k "hops"`
Expected: the first test may already pass if `metadata` is jsonb; second may fail if `metadata is None` returns `None`.

- [ ] **Step 3: Ensure message_store.send_message preserves metadata and defaults**

In `tinyagentos/chat/message_store.py`, inside `send_message`, ensure `metadata` is stored verbatim AND that retrieval defaults missing metadata to `{}`. Locate the insert block and the `_parse` function; adjust `_parse` so a NULL metadata column becomes `{}` rather than `None`. Example patch:

```python
def _parse(row: tuple, description) -> dict:
    d = {col[0]: row[i] for i, col in enumerate(description)}
    if isinstance(d.get("metadata"), str):
        try:
            d["metadata"] = json.loads(d["metadata"]) or {}
        except Exception:
            d["metadata"] = {}
    elif d.get("metadata") is None:
        d["metadata"] = {}
    # other existing jsonb unpacks ...
    return d
```

No schema change — `metadata` is already a json-serialised TEXT column.

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_chat_messages.py -v -k "hops"`
Expected: 2 passed (plus other existing tests still green).

Run: `pytest tests/test_chat_messages.py -v`
Expected: all green.

- [ ] **Step 5: Commit**

```bash
git add tinyagentos/chat/message_store.py tests/test_chat_messages.py
git commit -m "feat(chat): normalise message.metadata to dict; support hops_since_user field"
```

---

## Task 6: Router fanout rewrite (mentions + mode + policy)

**Files:**
- Modify: `tinyagentos/agent_chat_router.py`
- Test: `tests/test_agent_chat_router.py` (extend)

- [ ] **Step 1: Write failing tests for the new routing rules**

Add these tests to `tests/test_agent_chat_router.py`:

```python
def _channel(members, mode="quiet", muted=None, ctype="group"):
    return {
        "id": "c1",
        "type": ctype,
        "members": members,
        "settings": {
            "response_mode": mode,
            "max_hops": 3,
            "cooldown_seconds": 5,
            "rate_cap_per_minute": 20,
            "muted": muted or [],
        },
    }


@pytest.mark.asyncio
async def test_quiet_no_mention_no_fanout():
    bridge = _FakeBridge()
    state = _state_for({"name": "tom", "status": "running"}, bridge=bridge)
    state.config.agents = [
        {"name": "tom", "status": "running"},
        {"name": "don", "status": "running"},
    ]
    from tinyagentos.chat.group_policy import GroupPolicy
    state.group_policy = GroupPolicy()
    router = AgentChatRouter(state)
    message = {"id": "m1", "author_id": "user", "author_type": "user",
               "content": "hi folks", "metadata": {"hops_since_user": 0}}
    await router._route(message, _channel(["user", "tom", "don"], "quiet"))
    assert bridge.calls == []


@pytest.mark.asyncio
async def test_quiet_with_mention_routes_only_to_mentioned():
    bridge = _FakeBridge()
    state = _state_for({"name": "tom", "status": "running"}, bridge=bridge)
    state.config.agents = [
        {"name": "tom", "status": "running"},
        {"name": "don", "status": "running"},
    ]
    from tinyagentos.chat.group_policy import GroupPolicy
    state.group_policy = GroupPolicy()
    router = AgentChatRouter(state)
    message = {"id": "m2", "author_id": "user", "author_type": "user",
               "content": "@tom ping", "metadata": {"hops_since_user": 0}}
    await router._route(message, _channel(["user", "tom", "don"], "quiet"))
    slugs = sorted(c[0] for c in bridge.calls)
    assert slugs == ["tom"]
    assert bridge.calls[0][1]["force_respond"] is True


@pytest.mark.asyncio
async def test_lively_fans_out_to_all_others_without_force():
    bridge = _FakeBridge()
    state = _state_for({"name": "tom", "status": "running"}, bridge=bridge)
    state.config.agents = [
        {"name": "tom", "status": "running"},
        {"name": "don", "status": "running"},
    ]
    from tinyagentos.chat.group_policy import GroupPolicy
    state.group_policy = GroupPolicy()
    router = AgentChatRouter(state)
    message = {"id": "m3", "author_id": "user", "author_type": "user",
               "content": "anyone there?", "metadata": {"hops_since_user": 0}}
    await router._route(message, _channel(["user", "tom", "don"], "lively"))
    slugs = sorted(c[0] for c in bridge.calls)
    assert slugs == ["don", "tom"]
    assert all(c[1]["force_respond"] is False for c in bridge.calls)


@pytest.mark.asyncio
async def test_muted_agent_skipped():
    bridge = _FakeBridge()
    state = _state_for({"name": "tom", "status": "running"}, bridge=bridge)
    state.config.agents = [
        {"name": "tom", "status": "running"},
        {"name": "don", "status": "running"},
    ]
    from tinyagentos.chat.group_policy import GroupPolicy
    state.group_policy = GroupPolicy()
    router = AgentChatRouter(state)
    message = {"id": "m4", "author_id": "user", "author_type": "user",
               "content": "hi", "metadata": {"hops_since_user": 0}}
    ch = _channel(["user", "tom", "don"], "lively", muted=["tom"])
    await router._route(message, ch)
    slugs = sorted(c[0] for c in bridge.calls)
    assert slugs == ["don"]


@pytest.mark.asyncio
async def test_hop_cap_stops_chain():
    bridge = _FakeBridge()
    state = _state_for({"name": "tom", "status": "running"}, bridge=bridge)
    state.config.agents = [
        {"name": "tom", "status": "running"},
        {"name": "don", "status": "running"},
    ]
    from tinyagentos.chat.group_policy import GroupPolicy
    state.group_policy = GroupPolicy()
    router = AgentChatRouter(state)
    # Agent-authored message already at hops=3 should fan out only to
    # candidates whose next_hops <= max_hops (3). next_hops = 4, so drop.
    message = {"id": "m5", "author_id": "tom", "author_type": "agent",
               "content": "still there", "metadata": {"hops_since_user": 3}}
    await router._route(message, _channel(["user", "tom", "don"], "lively"))
    assert bridge.calls == []


@pytest.mark.asyncio
async def test_hop_cap_overridden_by_mention():
    bridge = _FakeBridge()
    state = _state_for({"name": "don", "status": "running"}, bridge=bridge)
    state.config.agents = [
        {"name": "tom", "status": "running"},
        {"name": "don", "status": "running"},
    ]
    from tinyagentos.chat.group_policy import GroupPolicy
    state.group_policy = GroupPolicy()
    router = AgentChatRouter(state)
    message = {"id": "m6", "author_id": "tom", "author_type": "agent",
               "content": "@don please chime in", "metadata": {"hops_since_user": 5}}
    await router._route(message, _channel(["user", "tom", "don"], "lively"))
    slugs = sorted(c[0] for c in bridge.calls)
    assert slugs == ["don"]
    assert bridge.calls[0][1]["force_respond"] is True
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_agent_chat_router.py -v`
Expected: 6 new tests FAIL; existing tests may also fail on router signature changes.

- [ ] **Step 3: Rewrite `_route_inner`**

Replace the entirety of `_route_inner` in `tinyagentos/agent_chat_router.py` with:

```python
async def _route_inner(self, message: dict, channel: dict) -> None:
    from tinyagentos.agent_db import find_agent
    from tinyagentos.chat.mentions import parse_mentions

    if message.get("content_type") == "system":
        return

    author = message.get("author_id")
    author_type = message.get("author_type")
    members = list(channel.get("members") or [])
    settings = channel.get("settings") or {}
    muted = set(settings.get("muted") or [])

    channel_type = channel.get("type")
    effective_mode = "lively" if channel_type == "dm" else settings.get("response_mode", "quiet")

    mentions = parse_mentions(message.get("content") or "", members)

    candidates = [m for m in members if m and m != author and m != "user" and m not in muted]
    if not candidates:
        return

    force_by_slug: dict[str, bool] = {}
    if mentions.all:
        for m in candidates: force_by_slug[m] = True
        recipients = list(candidates)
    elif mentions.explicit:
        recipients = [m for m in candidates if m in mentions.explicit]
        for m in recipients: force_by_slug[m] = True
    elif channel_type == "dm":
        recipients = list(candidates)
        for m in recipients: force_by_slug[m] = True
    elif effective_mode == "quiet":
        recipients = []
    else:
        recipients = list(candidates)

    if not recipients:
        return

    current_hops = (message.get("metadata") or {}).get("hops_since_user", 0)
    next_hops = current_hops + 1
    max_hops = int(settings.get("max_hops", 3))

    config = self._state.config
    bridge = getattr(self._state, "bridge_sessions", None)
    policy = getattr(self._state, "group_policy", None)

    for agent_name in recipients:
        forced = force_by_slug.get(agent_name, False)
        if not forced:
            if next_hops > max_hops:
                continue
            if policy is not None and not policy.may_send(channel["id"], agent_name, settings):
                continue
        agent = find_agent(config, agent_name)
        if agent is None:
            continue
        if agent.get("status") != "running":
            await self._post_system_reply(
                agent_name, channel["id"],
                f"[router] agent '{agent_name}' is not running (status={agent.get('status') or 'unknown'}).",
            )
            continue
        if bridge is None:
            await self._post_system_reply(
                agent_name, channel["id"],
                "[router] bridge registry not configured on this host.",
            )
            continue

        context = []
        if hasattr(self._state, "chat_messages"):
            try:
                from tinyagentos.chat.context_window import build_context_window
                recent = await self._state.chat_messages.get_messages(
                    channel_id=channel["id"], limit=30,
                )
                context = build_context_window(recent, limit=20, max_tokens=4000)
            except Exception:
                context = []

        await bridge.enqueue_user_message(
            agent_name,
            {
                "id": message.get("id"),
                "trace_id": message.get("id"),
                "channel_id": message.get("channel_id"),
                "from": message.get("author_id", "user"),
                "text": message.get("content", ""),
                "created_at": message.get("created_at"),
                "hops_since_user": next_hops,
                "force_respond": forced,
                "context": context,
            },
        )
        if policy is not None:
            policy.record_send(channel["id"], agent_name)
```

Also, update `dispatch` at top of class:

```python
def dispatch(self, message: dict, channel: dict) -> None:
    if message.get("content_type") == "system":
        return
    if message.get("state") == "streaming":
        return
    asyncio.create_task(self._route(message, channel))
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_agent_chat_router.py -v`
Expected: all tests green (new + existing).

Run: `pytest tests/test_chat_*.py tests/test_agent_chat_router.py -v`
Expected: all green.

- [ ] **Step 5: Commit**

```bash
git add tinyagentos/agent_chat_router.py tests/test_agent_chat_router.py
git commit -m "feat(chat): router fanout — mentions + quiet/lively modes + hop cap + policy gates"
```

---

## Task 7: Router wire-up in `app.py` + state attributes

**Files:**
- Modify: `tinyagentos/app.py`

- [ ] **Step 1: Inspect current wiring**

Run: `grep -n 'AgentChatRouter\|group_policy' tinyagentos/app.py`
Expected: `AgentChatRouter` initialised somewhere in `create_app`.

- [ ] **Step 2: Add `GroupPolicy` instantiation**

In the `create_app` function of `tinyagentos/app.py`, next to where `AgentChatRouter` is created, add:

```python
from tinyagentos.chat.group_policy import GroupPolicy

# ... inside create_app where app.state attributes are set ...
app.state.group_policy = GroupPolicy()
```

Place it immediately before the line that assigns `app.state.agent_chat_router` so the router finds it on first call.

- [ ] **Step 3: Smoke test the app starts**

Run: `python -c "from tinyagentos.app import create_app; create_app()"`
Expected: no exception.

- [ ] **Step 4: Run the full test suite**

Run: `pytest tests/ -x -q`
Expected: all green.

- [ ] **Step 5: Commit**

```bash
git add tinyagentos/app.py
git commit -m "feat(chat): wire GroupPolicy onto app.state for router"
```

---

## Task 8: Slash command module + dispatcher

**Files:**
- Create: `tinyagentos/chat/slash_commands.py`
- Test: `tests/test_chat_slash_commands.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_chat_slash_commands.py
import pytest
from unittest.mock import AsyncMock, MagicMock

from tinyagentos.chat.slash_commands import (
    parse_slash,
    dispatch,
    SlashResult,
)


def test_parse_slash_recognises_command():
    cmd, args = parse_slash("/lively")
    assert cmd == "lively" and args == ""


def test_parse_slash_with_args():
    cmd, args = parse_slash("/mute @tom please")
    assert cmd == "mute" and args == "@tom please"


def test_parse_slash_multiline_only_first_line():
    cmd, args = parse_slash("/topic New topic\nline two")
    assert cmd == "topic" and args == "New topic"


def test_parse_slash_non_command_returns_none():
    assert parse_slash("hello /lively there") == (None, None)
    assert parse_slash("/path/to/file") == (None, None)
    assert parse_slash("/   space") == (None, None)


@pytest.mark.asyncio
async def test_dispatch_lively_sets_mode():
    chs = MagicMock()
    chs.set_response_mode = AsyncMock()
    chs.get_channel = AsyncMock(return_value={"id": "c1", "settings": {}})
    state = MagicMock(chat_channels=chs)
    r = await dispatch("lively", "", "c1", "user", "user", state)
    assert isinstance(r, SlashResult)
    chs.set_response_mode.assert_awaited_once_with("c1", "lively")
    assert "lively" in r.system_text.lower()


@pytest.mark.asyncio
async def test_dispatch_hops_bad_arg():
    state = MagicMock()
    r = await dispatch("hops", "abc", "c1", "user", "user", state)
    assert "1..10" in r.system_text


@pytest.mark.asyncio
async def test_dispatch_mute_unknown_agent_errors():
    state = MagicMock()
    state.config = MagicMock()
    state.config.agents = [{"name": "tom"}]
    chs = MagicMock(); chs.mute_agent = AsyncMock()
    state.chat_channels = chs
    r = await dispatch("mute", "@unknown", "c1", "user", "user", state)
    assert "unknown" in r.system_text.lower() or "not found" in r.system_text.lower()
    chs.mute_agent.assert_not_awaited()


@pytest.mark.asyncio
async def test_dispatch_help_lists_all_commands():
    state = MagicMock()
    r = await dispatch("help", "", "c1", "user", "user", state)
    for cmd in ["mute", "unmute", "leave", "summon", "quiet", "lively",
                "hops", "cooldown", "topic", "rename", "help"]:
        assert f"/{cmd}" in r.system_text
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_chat_slash_commands.py -v`
Expected: FAIL — module doesn't exist.

- [ ] **Step 3: Implement the module**

```python
# tinyagentos/chat/slash_commands.py
"""Slash-command registry for chat channels.

Commands: mute, unmute, leave, summon, quiet, lively, hops, cooldown,
topic, rename, help. Unknown commands fall through (parse_slash returns
None). Bad arguments return a SlashResult with a user-facing error message
and no mutation.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

_SLASH_RE = re.compile(r"^/([A-Za-z][A-Za-z_-]*)(?:\s+(.*))?$")


@dataclass(frozen=True)
class SlashResult:
    system_text: str


def parse_slash(content: str) -> tuple[str | None, str | None]:
    if not content or not content.startswith("/"):
        return None, None
    first_line = content.split("\n", 1)[0].strip()
    m = _SLASH_RE.match(first_line)
    if not m:
        return None, None
    cmd = m.group(1).lower()
    args = (m.group(2) or "").strip()
    if cmd not in _COMMANDS:
        return None, None
    return cmd, args


def _strip_at(s: str) -> str:
    s = s.strip()
    if s.startswith("@"):
        s = s[1:]
    return s


async def _cmd_lively(args, channel_id, author_id, author_type, state):
    await state.chat_channels.set_response_mode(channel_id, "lively")
    return SlashResult(f"lively mode enabled by @{author_id}")


async def _cmd_quiet(args, channel_id, author_id, author_type, state):
    await state.chat_channels.set_response_mode(channel_id, "quiet")
    return SlashResult(f"quiet mode enabled by @{author_id}")


async def _cmd_mute(args, channel_id, author_id, author_type, state):
    slug = _strip_at(args)
    if not slug:
        return SlashResult("/mute @<slug> — missing agent slug")
    known = {a.get("name") for a in getattr(state.config, "agents", []) or []}
    if slug not in known:
        return SlashResult(f"unknown agent: {slug}")
    await state.chat_channels.mute_agent(channel_id, slug)
    return SlashResult(f"@{slug} muted in this channel by @{author_id}")


async def _cmd_unmute(args, channel_id, author_id, author_type, state):
    slug = _strip_at(args)
    if not slug:
        return SlashResult("/unmute @<slug> — missing agent slug")
    await state.chat_channels.unmute_agent(channel_id, slug)
    return SlashResult(f"@{slug} unmuted in this channel by @{author_id}")


async def _cmd_leave(args, channel_id, author_id, author_type, state):
    await state.chat_channels.remove_member(channel_id, author_id)
    return SlashResult(f"@{author_id} left this channel")


async def _cmd_summon(args, channel_id, author_id, author_type, state):
    slug = _strip_at(args)
    known = {a.get("name") for a in getattr(state.config, "agents", []) or []}
    if slug not in known:
        return SlashResult(f"unknown agent: {slug}")
    await state.chat_channels.add_member(channel_id, slug)
    return SlashResult(f"@{slug} summoned to this channel by @{author_id}")


async def _cmd_hops(args, channel_id, author_id, author_type, state):
    try:
        n = int(args.strip())
        if not 1 <= n <= 10:
            raise ValueError
    except ValueError:
        return SlashResult("/hops N — N must be 1..10")
    await state.chat_channels.set_max_hops(channel_id, n)
    return SlashResult(f"max_hops set to {n} by @{author_id}")


async def _cmd_cooldown(args, channel_id, author_id, author_type, state):
    raw = args.strip().rstrip("s")
    try:
        n = int(raw)
        if not 0 <= n <= 60:
            raise ValueError
    except ValueError:
        return SlashResult("/cooldown Ns — N must be 0..60")
    await state.chat_channels.set_cooldown_seconds(channel_id, n)
    return SlashResult(f"cooldown set to {n}s by @{author_id}")


async def _cmd_topic(args, channel_id, author_id, author_type, state):
    await state.chat_channels.update_channel(channel_id, topic=args)
    return SlashResult(f"topic updated by @{author_id}")


async def _cmd_rename(args, channel_id, author_id, author_type, state):
    name = args.strip()
    if not name:
        return SlashResult("/rename <name> — missing name")
    await state.chat_channels.update_channel(channel_id, name=name)
    return SlashResult(f"channel renamed to '{name}' by @{author_id}")


async def _cmd_help(args, channel_id, author_id, author_type, state):
    lines = [
        "Slash commands in this channel:",
        "  /mute @<slug>      mute an agent",
        "  /unmute @<slug>    unmute an agent",
        "  /leave             leave this channel",
        "  /summon @<slug>    add an agent to this channel",
        "  /quiet             switch to quiet mode (respond only when @mentioned)",
        "  /lively            switch to lively mode (agents decide per message)",
        "  /hops N            set max hops-since-user (1..10)",
        "  /cooldown Ns       set per-agent cooldown (0..60s)",
        "  /topic <text>      set channel topic",
        "  /rename <name>     rename channel",
        "  /help              show this list",
    ]
    return SlashResult("\n".join(lines))


_COMMANDS = {
    "mute":     _cmd_mute,
    "unmute":   _cmd_unmute,
    "leave":    _cmd_leave,
    "summon":   _cmd_summon,
    "quiet":    _cmd_quiet,
    "lively":   _cmd_lively,
    "hops":     _cmd_hops,
    "cooldown": _cmd_cooldown,
    "topic":    _cmd_topic,
    "rename":   _cmd_rename,
    "help":     _cmd_help,
}


async def dispatch(
    command: str, args: str, channel_id: str,
    author_id: str, author_type: str, state,
) -> SlashResult:
    handler = _COMMANDS.get(command)
    if handler is None:
        return SlashResult(f"unknown command: /{command}")
    return await handler(args, channel_id, author_id, author_type, state)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_chat_slash_commands.py -v`
Expected: 8 passed.

- [ ] **Step 5: Commit**

```bash
git add tinyagentos/chat/slash_commands.py tests/test_chat_slash_commands.py
git commit -m "feat(chat): slash-command registry with 11 commands (mute/unmute/leave/summon/quiet/lively/hops/cooldown/topic/rename/help)"
```

---

## Task 9: Slash command interception in `routes/chat.py`

**Files:**
- Modify: `tinyagentos/routes/chat.py`
- Test: `tests/test_routes_chat_slash.py` (new)

- [ ] **Step 1: Write the failing test**

```python
# tests/test_routes_chat_slash.py
import pytest
from httpx import AsyncClient, ASGITransport

from tinyagentos.app import create_app


@pytest.mark.asyncio
async def test_slash_lively_emits_system_message_not_agent_route():
    app = create_app()
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        # Assume a test channel exists; use the test fixture convention — skip if not
        # Minimal: POST /api/chat/messages with a slash command and verify
        # response marks it as handled as slash.
        resp = await client.post(
            "/api/chat/messages",
            json={"channel_id": "demo", "author_id": "user",
                  "author_type": "user", "content": "/lively",
                  "content_type": "text"},
        )
        assert resp.status_code in (200, 202)


@pytest.mark.asyncio
async def test_unknown_slash_passes_through_as_text():
    # /foo is not a known command; must be persisted as a normal message.
    pass  # placeholder; exercised in integration tests once channel fixture is wired.
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_routes_chat_slash.py -v`
Expected: FAIL until the slash-intercept path is implemented.

- [ ] **Step 3: Implement interception**

In `tinyagentos/routes/chat.py`, locate the `POST /api/chat/messages` handler (likely `send_message_endpoint` or `send_message`). Before `message_store.send_message` is called:

```python
from tinyagentos.chat.slash_commands import parse_slash, dispatch as slash_dispatch

# inside the endpoint, after basic validation, before persistence:
content = body.get("content") or ""
cmd, args = parse_slash(content)
if cmd is not None:
    result = await slash_dispatch(
        cmd, args or "", channel_id,
        body.get("author_id", "user"),
        body.get("author_type", "user"),
        request.app.state,
    )
    sys_msg = await request.app.state.chat_messages.send_message(
        channel_id=channel_id,
        author_id="system",
        author_type="system",
        content=result.system_text,
        content_type="text",
        state="complete",
        metadata=None,
    )
    await request.app.state.chat_channels.update_last_message_at(channel_id)
    await request.app.state.chat_hub.broadcast(
        channel_id,
        {"type": "message", "seq": request.app.state.chat_hub.next_seq(), **sys_msg},
    )
    return JSONResponse({"ok": True, "handled": "slash", "system_message": sys_msg}, status_code=200)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_routes_chat_slash.py tests/test_chat_slash_commands.py -v`
Expected: green.

Run: `pytest tests/ -x -q`
Expected: all green.

- [ ] **Step 5: Commit**

```bash
git add tinyagentos/routes/chat.py tests/test_routes_chat_slash.py
git commit -m "feat(chat): intercept slash commands in POST /api/chat/messages"
```

---

## Task 10: Reactions semantic dispatcher

**Files:**
- Create: `tinyagentos/chat/reactions.py`
- Test: `tests/test_chat_reactions.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_chat_reactions.py
import pytest
from unittest.mock import AsyncMock, MagicMock

from tinyagentos.chat.reactions import maybe_trigger_semantic, WantsReplyRegistry


@pytest.mark.asyncio
async def test_regenerate_triggered_for_thumbs_down_on_agent_reply():
    bridge = MagicMock(); bridge.enqueue_user_message = AsyncMock()
    state = MagicMock(bridge_sessions=bridge)
    state.wants_reply = WantsReplyRegistry()
    message = {"id": "m1", "channel_id": "c1", "author_id": "tom",
               "author_type": "agent", "content": "bad answer"}
    channel = {"id": "c1", "members": ["user", "tom"], "type": "dm",
               "settings": {}}
    await maybe_trigger_semantic(
        emoji="👎", message=message, reactor_id="user", reactor_type="user",
        channel=channel, state=state,
    )
    bridge.enqueue_user_message.assert_awaited_once()
    call = bridge.enqueue_user_message.await_args
    assert call.args[0] == "tom"
    assert call.args[1]["force_respond"] is True
    assert call.args[1].get("regenerate") is True


@pytest.mark.asyncio
async def test_thumbs_down_from_agent_is_noop():
    bridge = MagicMock(); bridge.enqueue_user_message = AsyncMock()
    state = MagicMock(bridge_sessions=bridge)
    state.wants_reply = WantsReplyRegistry()
    message = {"id": "m1", "channel_id": "c1", "author_id": "tom",
               "author_type": "agent", "content": "x"}
    channel = {"id": "c1", "members": ["user", "tom", "don"], "type": "group", "settings": {}}
    await maybe_trigger_semantic(
        emoji="👎", message=message, reactor_id="don", reactor_type="agent",
        channel=channel, state=state,
    )
    bridge.enqueue_user_message.assert_not_awaited()


@pytest.mark.asyncio
async def test_hand_raise_sets_wants_reply():
    state = MagicMock()
    state.wants_reply = WantsReplyRegistry()
    message = {"id": "m1", "channel_id": "c1", "author_id": "tom",
               "author_type": "agent", "content": "x"}
    channel = {"id": "c1", "members": ["user", "tom", "don"], "type": "group", "settings": {}}
    await maybe_trigger_semantic(
        emoji="🙋", message=message, reactor_id="don", reactor_type="agent",
        channel=channel, state=state,
    )
    assert "don" in state.wants_reply.list("c1")


def test_wants_reply_expires_after_ttl(monkeypatch):
    r = WantsReplyRegistry(ttl_seconds=60)
    t = [1000.0]
    monkeypatch.setattr("tinyagentos.chat.reactions._now", lambda: t[0])
    r.add("c1", "don")
    assert "don" in r.list("c1")
    t[0] = 1061.0
    assert "don" not in r.list("c1")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_chat_reactions.py -v`
Expected: FAIL — module doesn't exist.

- [ ] **Step 3: Implement the dispatcher**

```python
# tinyagentos/chat/reactions.py
"""Semantic reactions for multi-agent chat.

👎 by a human on an agent-authored message → regenerate the reply.
🙋 by an agent → "wants to reply" ephemeral flag with TTL.
Everything else is purely decorative.
"""
from __future__ import annotations

import time


def _now() -> float:
    return time.monotonic()


class WantsReplyRegistry:
    def __init__(self, ttl_seconds: int = 300) -> None:
        self._ttl = ttl_seconds
        self._entries: dict[str, dict[str, float]] = {}

    def add(self, channel_id: str, slug: str) -> None:
        self._entries.setdefault(channel_id, {})[slug] = _now()

    def list(self, channel_id: str) -> list[str]:
        now = _now()
        bucket = self._entries.get(channel_id) or {}
        alive = {s: t for s, t in bucket.items() if (now - t) < self._ttl}
        self._entries[channel_id] = alive
        return sorted(alive)


async def maybe_trigger_semantic(
    *, emoji: str, message: dict, reactor_id: str, reactor_type: str,
    channel: dict, state,
) -> None:
    if emoji == "👎" and reactor_type == "user" and message.get("author_type") == "agent":
        bridge = getattr(state, "bridge_sessions", None)
        if bridge is None:
            return
        await bridge.enqueue_user_message(
            message["author_id"],
            {
                "id": message.get("id"),
                "trace_id": message.get("id"),
                "channel_id": message.get("channel_id"),
                "from": reactor_id,
                "text": message.get("content", ""),
                "hops_since_user": 0,
                "force_respond": True,
                "regenerate": True,
                "context": [],
            },
        )
        return

    if emoji == "🙋" and reactor_type == "agent":
        reg = getattr(state, "wants_reply", None)
        if reg is None:
            return
        reg.add(channel["id"], reactor_id)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_chat_reactions.py -v`
Expected: 4 passed.

- [ ] **Step 5: Commit**

```bash
git add tinyagentos/chat/reactions.py tests/test_chat_reactions.py
git commit -m "feat(chat): semantic reactions — 👎 regenerate, 🙋 wants-reply registry"
```

---

## Task 11: Reactions HTTP endpoints + wants-reply listing

**Files:**
- Modify: `tinyagentos/routes/chat.py`
- Modify: `tinyagentos/app.py` (add `state.wants_reply`)

- [ ] **Step 1: Wire WantsReplyRegistry on app.state**

In `tinyagentos/app.py`, next to the GroupPolicy line from Task 7:

```python
from tinyagentos.chat.reactions import WantsReplyRegistry
app.state.wants_reply = WantsReplyRegistry()
```

- [ ] **Step 2: Add endpoints to `tinyagentos/routes/chat.py`**

Add imports at the top:

```python
from tinyagentos.chat.reactions import maybe_trigger_semantic
```

Add these handlers (placement: near the existing chat router endpoints):

```python
@router.post("/api/chat/messages/{message_id}/reactions")
async def add_reaction(message_id: str, body: dict, request: Request):
    emoji = body.get("emoji")
    author_id = body.get("author_id")
    author_type = body.get("author_type", "user")
    if not emoji or not author_id:
        return JSONResponse({"error": "emoji and author_id required"}, status_code=400)
    state = request.app.state
    msg = await state.chat_messages.get_message(message_id)
    if msg is None:
        return JSONResponse({"error": "message not found"}, status_code=404)
    await state.chat_messages.add_reaction(message_id, emoji, author_id)
    channel = await state.chat_channels.get_channel(msg["channel_id"])
    await state.chat_hub.broadcast(msg["channel_id"], {
        "type": "reaction_added",
        "message_id": message_id,
        "emoji": emoji,
        "author_id": author_id,
    })
    if channel is not None:
        await maybe_trigger_semantic(
            emoji=emoji, message=msg,
            reactor_id=author_id, reactor_type=author_type,
            channel=channel, state=state,
        )
    return JSONResponse({"ok": True}, status_code=200)


@router.delete("/api/chat/messages/{message_id}/reactions/{emoji}")
async def remove_reaction(message_id: str, emoji: str, author_id: str, request: Request):
    state = request.app.state
    await state.chat_messages.remove_reaction(message_id, emoji, author_id)
    msg = await state.chat_messages.get_message(message_id)
    if msg:
        await state.chat_hub.broadcast(msg["channel_id"], {
            "type": "reaction_removed",
            "message_id": message_id,
            "emoji": emoji,
            "author_id": author_id,
        })
    return JSONResponse({"ok": True}, status_code=200)


@router.get("/api/chat/channels/{channel_id}/wants_reply")
async def list_wants_reply(channel_id: str, request: Request):
    reg = getattr(request.app.state, "wants_reply", None)
    if reg is None:
        return JSONResponse({"slugs": []})
    return JSONResponse({"slugs": reg.list(channel_id)})
```

- [ ] **Step 3: Smoke test**

Run: `python -c "from tinyagentos.app import create_app; app = create_app(); print(sorted(r.path for r in app.routes if '/reactions' in getattr(r, 'path', '') or 'wants_reply' in getattr(r, 'path', '')))"`
Expected: prints the three new routes.

Run: `pytest tests/ -x -q`
Expected: all green.

- [ ] **Step 4: Commit**

```bash
git add tinyagentos/app.py tinyagentos/routes/chat.py
git commit -m "feat(chat): reactions POST/DELETE + wants_reply GET endpoints"
```

---

## Task 12: Bridge event payload changes (hops, force_respond, context)

**Files:**
- Modify: `tinyagentos/bridge_session.py`
- Modify: `tinyagentos/routes/openclaw.py`
- Test: `tests/test_bridge_session.py` (extend if present, else create `tests/test_bridge_session_phase1.py`)

- [ ] **Step 1: Write the failing test**

Create `tests/test_bridge_session_phase1.py`:

```python
import pytest
from unittest.mock import AsyncMock, MagicMock

from tinyagentos.bridge_session import BridgeSessionRegistry


@pytest.mark.asyncio
async def test_enqueue_passes_through_force_respond_and_context():
    reg = BridgeSessionRegistry()
    await reg.enqueue_user_message("tom", {
        "id": "m1", "trace_id": "m1", "channel_id": "c1",
        "from": "user", "text": "hi", "hops_since_user": 1,
        "force_respond": True, "context": [{"author_id": "user", "author_type": "user", "content": "prev"}],
    })
    frames = []
    async for frame in reg.subscribe("tom"):
        frames.append(frame)
        break
    assert "force_respond" in frames[0]
    assert "hops_since_user" in frames[0]
    assert "prev" in frames[0]  # context serialised into event data


@pytest.mark.asyncio
async def test_handle_reply_sets_hops_on_persisted_reply_metadata():
    store = MagicMock()
    store.get_message = AsyncMock(return_value={"channel_id": "c1"})
    store.send_message = AsyncMock(return_value={
        "id": "r1", "channel_id": "c1", "author_id": "tom",
        "author_type": "agent", "content": "yo", "created_at": 1.0,
        "metadata": {"hops_since_user": 1},
    })
    chans = MagicMock(); chans.update_last_message_at = AsyncMock()
    hub = MagicMock(); hub.broadcast = AsyncMock(); hub.next_seq = MagicMock(return_value=1)
    reg = BridgeSessionRegistry(chat_messages=store, chat_channels=chans, chat_hub=hub)
    # Prime the pending-hops map by simulating enqueue
    await reg.enqueue_user_message("tom", {
        "id": "m1", "trace_id": "m1", "channel_id": "c1", "from": "user",
        "text": "hi", "hops_since_user": 1, "force_respond": False, "context": [],
    })
    # Now post a reply
    await reg.record_reply("tom", {"kind": "final", "id": "r1", "trace_id": "m1", "content": "yo"})
    # send_message should have been called with metadata including hops_since_user=1
    call = store.send_message.await_args
    assert call.kwargs["metadata"]["hops_since_user"] == 1
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_bridge_session_phase1.py -v`
Expected: FAIL — event data doesn't yet carry new fields, hops not threaded.

- [ ] **Step 3: Update `BridgeSessionRegistry.enqueue_user_message`**

In `tinyagentos/bridge_session.py`, modify `enqueue_user_message` so the event JSON includes all new keys. Find the block where it builds the SSE frame (current shape: `{"event": "user_message", "data": msg}`). Update to ensure the `data` dict preserves all keys (it already does via `msg`), but **also** record the hops-per-trace in a session-local map:

```python
async def enqueue_user_message(self, slug: str, msg: dict) -> None:
    if not slug or slug == "_unknown_":
        return
    async with self._lock:
        session = self._get_or_create(slug)
    session.pending_hops[msg.get("trace_id") or msg.get("id")] = int(msg.get("hops_since_user", 0))
    await session.queue.put({"event": "user_message", "data": msg})
    # ... existing trace write ...
```

Add `pending_hops: dict[str, int]` to `_AgentSession.__init__`.

- [ ] **Step 4: Update `_handle_reply` to set hops on persisted reply**

In `bridge_session._handle_reply`, find the place it persists the reply message (the `delta`/`final` path where `chat_messages.send_message` is called). Before that call, compute:

```python
# existing: trace_id = body.get("trace_id") or _new_id()
hops = 0
async with self._lock:
    session = self._sessions.get(slug)
if session is not None:
    hops = session.pending_hops.pop(trace_id, 0)
# when persisting:
await self._chat_messages.send_message(
    # ... existing args ...
    metadata={"trace_id": trace_id, "openclaw_msg_id": msg_id, "hops_since_user": hops},
)
```

Apply to every `send_message` call inside `_handle_reply` (delta path and final path).

- [ ] **Step 5: Re-dispatch on final reply**

At the very end of the `final` branch of `_handle_reply`, after the broadcast:

```python
router = getattr(self, "_router", None)
if router is not None and persisted is not None:
    channel = await self._chat_channels.get_channel(persisted["channel_id"])
    if channel is not None:
        router.dispatch(persisted, channel)
```

Add `_router` as an optional constructor arg on `BridgeSessionRegistry` and set `app.state.bridge_sessions._router = app.state.agent_chat_router` in `app.py` AFTER both are constructed.

- [ ] **Step 6: Routes/openclaw unchanged — verify it passes through**

In `tinyagentos/routes/openclaw.py`, the `/events` SSE generator already serialises `event.data` as JSON — new keys flow through automatically. No change required.

- [ ] **Step 7: Run tests to verify they pass**

Run: `pytest tests/test_bridge_session_phase1.py -v`
Expected: 2 passed.

Run: `pytest tests/ -x -q`
Expected: all green.

- [ ] **Step 8: Commit**

```bash
git add tinyagentos/bridge_session.py tinyagentos/app.py tests/test_bridge_session_phase1.py
git commit -m "feat(chat): event payload carries hops/force_respond/context; replies re-dispatch for agent-to-agent fanout"
```

---

## Task 13: Bridge scripts — NO_RESPONSE gating + context rendering

**Files (each modified — same shape):**
- Modify: `tinyagentos/scripts/install_hermes.sh`
- Modify: `tinyagentos/scripts/install_smolagents.sh`
- Modify: `tinyagentos/scripts/install_langroid.sh`
- Modify: `tinyagentos/scripts/install_pocketflow.sh`
- Modify: `tinyagentos/scripts/install_openai_agents_sdk.sh`
- Modify: `tinyagentos/scripts/install_openai-agents-sdk.sh`

- [ ] **Step 1: Add shared helper snippet to each bridge (context rendering + suppress check)**

Each bridge already defines a `handle(...)` function that reads `evt` and posts a reply. Add two helpers above `handle`:

```python
def _render_context(ctx):
    if not ctx:
        return ""
    lines = []
    for m in ctx:
        who = m.get("author_id") or "?"
        lines.append(f"{who}: {m.get('content','')}")
    return "\n".join(lines)

def _suppress(reply, force):
    if force:
        return reply
    stripped = (reply or "").strip().lower().strip(".!,;:")
    return None if stripped == "no_response" else reply
```

- [ ] **Step 2: Update each bridge's `handle` to honour `force_respond` + `context`**

In each bridge's `handle` function, replace the body roughly as follows.

**hermes** (`install_hermes.sh`) — `handle_user_message`:

```python
async def handle_user_message(client, evt, channel):
    mid = evt.get("id",""); tid = evt.get("trace_id", mid); text = evt.get("text","")
    force = bool(evt.get("force_respond"))
    ctx = _render_context(evt.get("context") or [])
    log.info("user_message id=%s text=%r force=%s", mid, text[:80], force)
    system = _SYSTEM_PROMPT + ("\n\nYou were directly addressed. Reply naturally; do not output NO_RESPONSE."
        if force else
        "\n\nIf you were not explicitly @mentioned and this message is not for you, reply with exactly: NO_RESPONSE\nOtherwise reply naturally. Keep it short in group chats.")
    messages = [{"role":"system","content":system}]
    if ctx: messages.append({"role":"user","content":f"Recent conversation:\n{ctx}"})
    messages.append({"role":"user","content":text})
    reply = await call_hermes(client, messages)
    final = _suppress(reply, force)
    if final is None:
        log.info("suppressed NO_RESPONSE for id=%s", mid)
        return
    await post_reply(client, channel["reply_url"], channel["auth_bearer"],
                     mid, tid, final, evt.get("channel_id"))
```

And modify `call_hermes` to accept a `messages` list instead of a single `text`:

```python
async def call_hermes(client, messages):
    payload = {"model": HERMES_MODEL, "messages": messages}
    # ... rest unchanged ...
```

**smolagents** (`install_smolagents.sh`) — modify `handle` (and `_run`):

```python
def _run(text, force):
    try:
        agent = _build()
        rule = (" You were directly addressed, reply normally."
                if force else
                " If this message isn't for you, reply with exactly NO_RESPONSE.")
        return str(agent.run(_FRAMEWORK_PREAMBLE + rule + "\n\nTask: " + text))
    except Exception as e:
        return f"[smolagents error: {e}]"

async def handle(c, evt, ch):
    mid = evt.get("id",""); tid = evt.get("trace_id", mid); text = evt.get("text","")
    force = bool(evt.get("force_respond"))
    ctx = _render_context(evt.get("context") or [])
    full = (f"Recent conversation:\n{ctx}\n\nCurrent: {text}") if ctx else text
    reply = await asyncio.get_running_loop().run_in_executor(_pool, _run, full, force)
    final = _suppress(reply, force)
    if final is None:
        log.info("suppressed id=%s", mid); return
    await post_reply(c, ch["reply_url"], ch["auth_bearer"], mid, tid, final, evt.get("channel_id"))
```

**langroid** (`install_langroid.sh`) — same pattern, build system_message dynamically:

```python
def _build(force_respond=False):
    import langroid as lr
    sysmsg = _SYSTEM_PROMPT + (" You were directly addressed; reply naturally, do not output NO_RESPONSE."
        if force_respond else
        " If this message isn't for you, reply with exactly NO_RESPONSE. Otherwise reply.")
    return lr.ChatAgent(lr.ChatAgentConfig(
        llm=lr.language_models.OpenAIGPTConfig(chat_model=MODEL),
        system_message=sysmsg,
    ))

def _run(text, force):
    try:
        a = _build(force); r = a.llm_response(text)
        return r.content if r else "(no response)"
    except Exception as e:
        return f"[langroid error: {e}]"

async def handle(c, evt, ch):
    mid = evt.get("id",""); tid = evt.get("trace_id", mid); text = evt.get("text","")
    force = bool(evt.get("force_respond"))
    ctx = _render_context(evt.get("context") or [])
    full = (f"Recent conversation:\n{ctx}\n\nCurrent: {text}") if ctx else text
    reply = await asyncio.get_running_loop().run_in_executor(_pool, _run, full, force)
    final = _suppress(reply, force)
    if final is None: return
    await post_reply(c, ch["reply_url"], ch["auth_bearer"], mid, tid, final, evt.get("channel_id"))
```

**pocketflow** (`install_pocketflow.sh`) — modify `_run` + `handle`:

```python
def _run(text, force):
    try:
        import openai
        client = openai.OpenAI()
        rule = (" You were directly addressed, reply normally."
                if force else
                " If this message isn't for you, reply with exactly NO_RESPONSE.")
        resp = client.chat.completions.create(model=MODEL, messages=[
            {"role":"system","content":_SYSTEM_PROMPT + rule},
            {"role":"user","content":text},
        ])
        return resp.choices[0].message.content or "(empty)"
    except Exception as e:
        return f"[pocketflow error: {e}]"

async def handle(c, evt, ch):
    mid = evt.get("id",""); tid = evt.get("trace_id", mid); text = evt.get("text","")
    force = bool(evt.get("force_respond"))
    ctx = _render_context(evt.get("context") or [])
    full = (f"Recent conversation:\n{ctx}\n\nCurrent: {text}") if ctx else text
    reply = await asyncio.get_running_loop().run_in_executor(_pool, _run, full, force)
    final = _suppress(reply, force)
    if final is None: return
    await post_reply(c, ch["reply_url"], ch["auth_bearer"], mid, tid, final, evt.get("channel_id"))
```

**openai-agents-sdk** (both `install_openai_agents_sdk.sh` and `install_openai-agents-sdk.sh`):

```python
def _build(force):
    from openai import AsyncOpenAI
    from agents import Agent, OpenAIChatCompletionsModel, set_tracing_disabled
    set_tracing_disabled(True)
    client = AsyncOpenAI(base_url=os.environ["OPENAI_BASE_URL"], api_key=os.environ["OPENAI_API_KEY"])
    rule = (" You were directly addressed, reply normally."
            if force else
            " If this message isn't for you, reply with exactly NO_RESPONSE.")
    return Agent(name=AGENT_NAME,
                 instructions=(_SYSTEM_INSTRUCTIONS + rule),
                 model=OpenAIChatCompletionsModel(model=MODEL, openai_client=client))

def _run(text, force):
    try:
        from agents import Runner
        a = _build(force); r = Runner.run_sync(a, text)
        return str(r.final_output)
    except Exception as e:
        return f"[openai-agents error: {e}]"

async def handle(c, evt, ch):
    mid = evt.get("id",""); tid = evt.get("trace_id", mid); text = evt.get("text","")
    force = bool(evt.get("force_respond"))
    ctx = _render_context(evt.get("context") or [])
    full = (f"Recent conversation:\n{ctx}\n\nCurrent: {text}") if ctx else text
    reply = await asyncio.get_running_loop().run_in_executor(_pool, _run, full, force)
    final = _suppress(reply, force)
    if final is None: return
    await post_reply(c, ch["reply_url"], ch["auth_bearer"], mid, tid, final, evt.get("channel_id"))
```

Also inside the openai-agents-sdk bridge's module scope, extract the big instruction string to a `_SYSTEM_INSTRUCTIONS` constant so `_build` can add the NO_RESPONSE rule.

- [ ] **Step 3: Lint the bash by running the scripts through `bash -n`**

Run: `for f in tinyagentos/scripts/install_*.sh; do bash -n "$f" && echo "$f ok" || echo "$f BAD"; done`
Expected: every script `ok`.

- [ ] **Step 4: Commit**

```bash
git add tinyagentos/scripts/install_hermes.sh tinyagentos/scripts/install_smolagents.sh \
        tinyagentos/scripts/install_langroid.sh tinyagentos/scripts/install_pocketflow.sh \
        tinyagentos/scripts/install_openai_agents_sdk.sh tinyagentos/scripts/install_openai-agents-sdk.sh
git commit -m "feat(bridges): honor force_respond, render context, suppress NO_RESPONSE"
```

---

## Task 14: Router integration tests (fanout chains, @all, cooldown, rate cap)

**Files:**
- Modify: `tests/test_agent_chat_router.py`

- [ ] **Step 1: Write the failing integration tests**

Add to `tests/test_agent_chat_router.py`:

```python
@pytest.mark.asyncio
async def test_at_all_resets_and_forces_everyone():
    bridge = _FakeBridge()
    state = _state_for({"name": "tom", "status": "running"}, bridge=bridge)
    state.config.agents = [{"name": "tom", "status": "running"},
                           {"name": "don", "status": "running"}]
    from tinyagentos.chat.group_policy import GroupPolicy
    state.group_policy = GroupPolicy()
    router = AgentChatRouter(state)
    msg = {"id": "m", "author_id": "user", "author_type": "user",
           "content": "@all wake up", "metadata": {"hops_since_user": 0}}
    ch = _channel(["user", "tom", "don"], "lively")
    await router._route(msg, ch)
    assert sorted(c[0] for c in bridge.calls) == ["don", "tom"]
    assert all(c[1]["force_respond"] is True for c in bridge.calls)


@pytest.mark.asyncio
async def test_cooldown_blocks_subsequent_unforced():
    bridge = _FakeBridge()
    state = _state_for({"name": "tom", "status": "running"}, bridge=bridge)
    state.config.agents = [{"name": "tom", "status": "running"}]
    from tinyagentos.chat.group_policy import GroupPolicy
    state.group_policy = GroupPolicy()
    router = AgentChatRouter(state)
    msg = {"id": "m1", "author_id": "user", "author_type": "user",
           "content": "hi", "metadata": {"hops_since_user": 0}}
    ch = _channel(["user", "tom"], "lively")
    await router._route(msg, ch)
    # Second message, no mention: cooldown applies
    msg2 = {**msg, "id": "m2", "content": "again"}
    await router._route(msg2, ch)
    # Only one enqueue because the second was blocked
    assert len(bridge.calls) == 1


@pytest.mark.asyncio
async def test_cooldown_skipped_when_mentioned():
    bridge = _FakeBridge()
    state = _state_for({"name": "tom", "status": "running"}, bridge=bridge)
    state.config.agents = [{"name": "tom", "status": "running"}]
    from tinyagentos.chat.group_policy import GroupPolicy
    state.group_policy = GroupPolicy()
    router = AgentChatRouter(state)
    msg = {"id": "m1", "author_id": "user", "author_type": "user",
           "content": "hi", "metadata": {"hops_since_user": 0}}
    ch = _channel(["user", "tom"], "lively")
    await router._route(msg, ch)
    msg2 = {**msg, "id": "m2", "content": "@tom still there?"}
    await router._route(msg2, ch)
    assert len(bridge.calls) == 2
    assert bridge.calls[1][1]["force_respond"] is True


@pytest.mark.asyncio
async def test_dm_always_forces_respond():
    bridge = _FakeBridge()
    state = _state_for({"name": "tom", "status": "running"}, bridge=bridge)
    state.config.agents = [{"name": "tom", "status": "running"}]
    from tinyagentos.chat.group_policy import GroupPolicy
    state.group_policy = GroupPolicy()
    router = AgentChatRouter(state)
    msg = {"id": "m", "author_id": "user", "author_type": "user",
           "content": "ping", "metadata": {"hops_since_user": 0}}
    ch = _channel(["user", "tom"], mode="quiet", ctype="dm")
    await router._route(msg, ch)
    assert len(bridge.calls) == 1
    assert bridge.calls[0][1]["force_respond"] is True
```

- [ ] **Step 2: Run tests to verify they pass**

Run: `pytest tests/test_agent_chat_router.py -v`
Expected: all tests green.

- [ ] **Step 3: Commit**

```bash
git add tests/test_agent_chat_router.py
git commit -m "test(chat): integration coverage for @all, cooldown, cooldown-override-by-mention, DM force-respond"
```

---

## Task 15: E2E roundtable test (agents reference each other)

**Files:**
- Create: `tests/e2e/test_roundtable_group_chat.py`

- [ ] **Step 1: Check existing e2e test conventions**

Run: `ls tests/e2e/ && head -40 tests/e2e/test_framework_tab.py`
Expected: Playwright-based tests using the existing conftest fixtures.

- [ ] **Step 2: Write the e2e test (skipped by default; only runs with a live Pi)**

```python
# tests/e2e/test_roundtable_group_chat.py
"""End-to-end: verify multi-agent context threading lands.

Marked `slow` and requires a Pi fixture with at least 2 live agents.
Skipped in local CI; run with `pytest -m slow` against a live stack.
"""
import os
import pytest
import httpx

PI_URL = os.environ.get("TAOS_E2E_URL")
PI_TOKEN = os.environ.get("TAOS_E2E_TOKEN")
CHANNEL_ID = os.environ.get("TAOS_E2E_CHANNEL")

pytestmark = [
    pytest.mark.slow,
    pytest.mark.skipif(not (PI_URL and PI_TOKEN and CHANNEL_ID),
                       reason="E2E requires TAOS_E2E_URL/TOKEN/CHANNEL"),
]


@pytest.mark.asyncio
async def test_agents_reference_each_other_in_lively_roundtable():
    headers = {"Authorization": f"Bearer {PI_TOKEN}"}
    async with httpx.AsyncClient(base_url=PI_URL, headers=headers, timeout=60) as c:
        # Force channel into lively mode
        r = await c.post("/api/chat/messages", json={
            "channel_id": CHANNEL_ID, "author_id": "user",
            "author_type": "user", "content": "/lively",
            "content_type": "text",
        })
        assert r.status_code < 300

        # Post a question likely to elicit multiple agents
        r = await c.post("/api/chat/messages", json={
            "channel_id": CHANNEL_ID, "author_id": "user",
            "author_type": "user",
            "content": "@all please introduce yourselves in one sentence. Mention at least one other agent by name in your reply.",
            "content_type": "text",
        })
        assert r.status_code < 300

        # Poll for ≥3 agent replies referencing at least one other agent name
        import asyncio
        for _ in range(40):
            r = await c.get(f"/api/chat/channels/{CHANNEL_ID}/messages?limit=20")
            msgs = r.json().get("messages", [])
            agent_msgs = [m for m in msgs if m.get("author_type") == "agent"]
            names = {m["author_id"] for m in agent_msgs}
            cross_refs = sum(
                1 for m in agent_msgs
                if any(other in (m.get("content") or "") for other in names if other != m["author_id"])
            )
            if len(agent_msgs) >= 3 and cross_refs >= 1:
                return
            await asyncio.sleep(3)
        pytest.fail("No agents referenced each other after 2 minutes")
```

- [ ] **Step 3: Verify the test is collected but skipped locally**

Run: `pytest tests/e2e/test_roundtable_group_chat.py -v`
Expected: SKIPPED (env vars unset).

- [ ] **Step 4: Commit**

```bash
git add tests/e2e/test_roundtable_group_chat.py
git commit -m "test(e2e): agents must reference each other in lively roundtable"
```

---

## Final verification

- [ ] **Step 1: Full test suite**

Run: `pytest tests/ -x -q`
Expected: all green.

- [ ] **Step 2: Diff against spec**

Run: `git log --oneline master..HEAD`
Verify each task has a commit. Re-read `docs/superpowers/specs/2026-04-19-chat-phase-1-conversational-core-design.md` and confirm every section maps to a committed task.

- [ ] **Step 3: Open PR**

```bash
git push -u origin feat/chat-phase-1-conversational-core
gh pr create --base master --title "Chat Phase 1 — multi-agent routing, mentions, policy, slash commands, reactions" \
  --body-file docs/superpowers/specs/2026-04-19-chat-phase-1-conversational-core-design.md
```
