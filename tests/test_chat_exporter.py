from __future__ import annotations

import json
import re
import time
from pathlib import Path

import pytest
import pytest_asyncio

from tinyagentos.chat.chat_exporter import ChatExportError, ChatExporter, flatten_body
from tinyagentos.chat.message_store import ChatMessageStore


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest_asyncio.fixture
async def store(tmp_path):
    s = ChatMessageStore(tmp_path / "chat.db")
    await s.init()
    yield s
    await s.close()


def _make_file_writer(root: Path):
    async def writer(source_id: str, content: bytes) -> str:
        dest = root / "chat-export" / f"{source_id}.txt"
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(content)
        return f"chat-export/{source_id}.txt"

    return writer


def _identity_map(user_ids: list[str], agent_ids: list[str] | None = None) -> dict[str, str]:
    m: dict[str, str] = {}
    for uid in user_ids:
        m[uid] = f"@{uid}"
    for aid in (agent_ids or []):
        m[aid] = f"@{aid}"
    return m


# ---------------------------------------------------------------------------
# flatten_body unit tests
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_flatten_empty_blocks():
    assert flatten_body([]) == ""
    assert flatten_body(None) == ""


@pytest.mark.asyncio
async def test_flatten_text_blocks():
    blocks = [
        {"type": "paragraph", "text": "Hello"},
        {"type": "code", "lang": "py", "text": "print(1)"},
    ]
    assert flatten_body(blocks) == "Hello\nprint(1)"


@pytest.mark.asyncio
async def test_flatten_skips_empty_text():
    # Updated for card rule 3: a non-text block (image) no longer vanishes
    # silently — it flattens to a descriptive placeholder. An empty-text
    # text-type block still contributes nothing.
    blocks = [
        {"type": "paragraph", "text": "Hello"},
        {"type": "image", "url": "http://x/img.png"},
        {"type": "paragraph", "text": ""},
    ]
    assert flatten_body(blocks) == "Hello\n[image: http://x/img.png]"


# ---------------------------------------------------------------------------
# Exporter integration tests
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_export_channel_produces_valid_batch(store, tmp_path):
    await store.send_message(
        channel_id="ch1",
        author_id="user1",
        author_type="user",
        content="hello",
        content_blocks=[{"type": "paragraph", "text": "hello"}],
    )
    await store.send_message(
        channel_id="ch1",
        author_id="agent-1",
        author_type="agent",
        content="hi",
        content_blocks=[{"type": "paragraph", "text": "hi"}],
    )

    exporter = ChatExporter(
        message_store=store,
        identity_map=_identity_map(["user1"], ["agent-1"]),
    )
    batch = await exporter.export_channel("ch1")

    assert len(batch) == 2
    for env in batch:
        assert env["from"]
        assert env["thread"] == "ch1"
        assert env["ts"] > 0
        assert env["source"] == "taos-chat"
        assert env["source_id"]
        assert isinstance(env["blocks"], list)


@pytest.mark.asyncio
async def test_every_message_has_non_empty_body(store):
    await store.send_message(
        channel_id="ch1",
        author_id="user1",
        author_type="user",
        content="",
        content_blocks=[{"type": "paragraph", "text": "blocks present"}],
    )
    await store.send_message(
        channel_id="ch1",
        author_id="user1",
        author_type="user",
        content="plain text",
        content_blocks=[],
    )

    exporter = ChatExporter(
        message_store=store,
        identity_map=_identity_map(["user1"]),
    )
    batch = await exporter.export_channel("ch1")
    assert len(batch) == 2
    for env in batch:
        assert env["body"] != ""


@pytest.mark.asyncio
async def test_unmapped_author_fails_whole_batch(store):
    await store.send_message(
        channel_id="ch1",
        author_id="user1",
        author_type="user",
        content="first",
    )
    await store.send_message(
        channel_id="ch1",
        author_id="unknown-user",
        author_type="user",
        content="second",
    )

    exporter = ChatExporter(
        message_store=store,
        identity_map=_identity_map(["user1"]),
    )
    with pytest.raises(ChatExportError, match="unmapped author_id 'unknown-user'"):
        await exporter.export_channel("ch1")


@pytest.mark.asyncio
async def test_reexport_produces_byte_identical_output(store, tmp_path):
    for i in range(3):
        await store.send_message(
            channel_id="ch1",
            author_id="user1",
            author_type="user",
            content=f"msg{i}",
            content_blocks=[{"type": "paragraph", "text": f"msg{i}"}],
        )

    exporter = ChatExporter(
        message_store=store,
        identity_map=_identity_map(["user1"]),
    )
    batch1 = await exporter.export_channel("ch1")
    batch2 = await exporter.export_channel("ch1")

    assert json.dumps(batch1, sort_keys=True, ensure_ascii=False) == json.dumps(
        batch2, sort_keys=True, ensure_ascii=False
    )


@pytest.mark.asyncio
async def test_oversized_content_becomes_ref(store, tmp_path):
    # Updated for card rule 5: the oversized envelope's blocks are no
    # longer destroyed. The full original envelope (blocks intact) is
    # written through file_writer, and the emitted body keeps the
    # (possibly truncated) flattened text plus a note carrying the ref,
    # rather than the body being replaced by the bare ref.
    big_text = "x" * 100_000
    blocks = [{"type": "paragraph", "text": big_text}]
    await store.send_message(
        channel_id="ch1",
        author_id="user1",
        author_type="user",
        content=big_text,
        content_blocks=blocks,
    )

    writer = _make_file_writer(tmp_path)
    exporter = ChatExporter(
        message_store=store,
        identity_map=_identity_map(["user1"]),
        file_writer=writer,
    )
    batch = await exporter.export_channel("ch1")
    assert len(batch) == 1
    env = batch[0]
    assert env["blocks"] == []
    ref_match = re.search(r"chat-export/[^\s\]]+\.txt", env["body"])
    assert ref_match, f"no ref found in body: {env['body']!r}"
    assert env["body"].endswith(f"[oversized content exported to: {ref_match.group(0)}]")
    written = (tmp_path / ref_match.group(0)).read_bytes()
    full_envelope = json.loads(written.decode("utf-8"))
    assert full_envelope["blocks"] == blocks
    assert full_envelope["body"] == big_text


@pytest.mark.asyncio
async def test_thread_ordering_preserved(store):
    ts = time.time()
    for i in range(3):
        await store.send_message(
            channel_id="ch1",
            author_id="user1",
            author_type="user",
            content=f"msg{i}",
            content_blocks=[{"type": "paragraph", "text": f"msg{i}"}],
        )
        await store._db.execute(
            "UPDATE chat_messages SET created_at = ? WHERE id = (SELECT id FROM chat_messages ORDER BY created_at DESC LIMIT 1)",
            (ts,),
        )
        await store._db.commit()

    exporter = ChatExporter(
        message_store=store,
        identity_map=_identity_map(["user1"]),
    )
    batch1 = await exporter.export_channel("ch1")
    batch2 = await exporter.export_channel("ch1")
    ids1 = [e["source_id"] for e in batch1]
    ids2 = [e["source_id"] for e in batch2]
    assert ids1 == ids2
    assert len(ids1) == 3


@pytest.mark.asyncio
async def test_reply_to_relationships_preserved(store):
    parent = await store.send_message(
        channel_id="ch1",
        author_id="user1",
        author_type="user",
        content="parent",
        content_blocks=[{"type": "paragraph", "text": "parent"}],
    )
    reply = await store.send_message(
        channel_id="ch1",
        author_id="user2",
        author_type="user",
        content="reply",
        content_blocks=[{"type": "paragraph", "text": "reply"}],
        thread_id=parent["id"],
    )

    exporter = ChatExporter(
        message_store=store,
        identity_map=_identity_map(["user1", "user2"]),
    )
    batch = await exporter.export_channel("ch1")
    reply_env = next(e for e in batch if e["source_id"] == reply["id"])
    assert reply_env.get("reply_to") == parent["id"]
    parent_env = next(e for e in batch if e["source_id"] == parent["id"])
    assert "reply_to" not in parent_env


@pytest.mark.asyncio
async def test_deleted_parent_omits_reply_to(store):
    parent = await store.send_message(
        channel_id="ch1",
        author_id="user1",
        author_type="user",
        content="parent",
        content_blocks=[{"type": "paragraph", "text": "parent"}],
    )
    await store.soft_delete_message(parent["id"])
    reply = await store.send_message(
        channel_id="ch1",
        author_id="user2",
        author_type="user",
        content="reply",
        content_blocks=[{"type": "paragraph", "text": "reply"}],
        thread_id=parent["id"],
    )

    exporter = ChatExporter(
        message_store=store,
        identity_map=_identity_map(["user1", "user2"]),
    )
    batch = await exporter.export_channel("ch1")
    reply_env = next(e for e in batch if e["source_id"] == reply["id"])
    assert "reply_to" not in reply_env


@pytest.mark.asyncio
async def test_export_all_channels(store):
    await store.send_message(
        channel_id="ch1",
        author_id="user1",
        author_type="user",
        content="ch1-msg",
        content_blocks=[{"type": "paragraph", "text": "ch1-msg"}],
    )
    await store.send_message(
        channel_id="ch2",
        author_id="user1",
        author_type="user",
        content="ch2-msg",
        content_blocks=[{"type": "paragraph", "text": "ch2-msg"}],
    )

    exporter = ChatExporter(
        message_store=store,
        identity_map=_identity_map(["user1"]),
    )
    batch = await exporter.export_all_channels(["ch1", "ch2"])
    assert len(batch) == 2
    threads = {e["thread"] for e in batch}
    assert threads == {"ch1", "ch2"}


@pytest.mark.asyncio
async def test_blocks_preserved_in_envelope(store):
    blocks = [
        {"type": "paragraph", "text": "line1"},
        {"type": "code", "lang": "python", "text": "print(1)"},
    ]
    await store.send_message(
        channel_id="ch1",
        author_id="user1",
        author_type="user",
        content="line1\nprint(1)",
        content_blocks=blocks,
    )

    exporter = ChatExporter(
        message_store=store,
        identity_map=_identity_map(["user1"]),
    )
    batch = await exporter.export_channel("ch1")
    assert batch[0]["blocks"] == blocks


@pytest.mark.asyncio
async def test_ts_preserved(store):
    msg = await store.send_message(
        channel_id="ch1",
        author_id="user1",
        author_type="user",
        content="hello",
        content_blocks=[{"type": "paragraph", "text": "hello"}],
    )

    exporter = ChatExporter(
        message_store=store,
        identity_map=_identity_map(["user1"]),
    )
    batch = await exporter.export_channel("ch1")
    assert batch[0]["ts"] == msg["created_at"]


@pytest.mark.asyncio
async def test_source_id_is_original_message_id(store):
    msg = await store.send_message(
        channel_id="ch1",
        author_id="user1",
        author_type="user",
        content="hello",
        content_blocks=[{"type": "paragraph", "text": "hello"}],
    )

    exporter = ChatExporter(
        message_store=store,
        identity_map=_identity_map(["user1"]),
    )
    batch = await exporter.export_channel("ch1")
    assert batch[0]["source_id"] == msg["id"]


@pytest.mark.asyncio
async def test_custom_source_identifier(store):
    exporter = ChatExporter(
        message_store=store,
        identity_map=_identity_map(["user1"]),
        source="my-custom-source",
    )
    await store.send_message(
        channel_id="ch1",
        author_id="user1",
        author_type="user",
        content="hello",
        content_blocks=[{"type": "paragraph", "text": "hello"}],
    )
    batch = await exporter.export_channel("ch1")
    assert batch[0]["source"] == "my-custom-source"


@pytest.mark.asyncio
async def test_dropped_fields_are_not_exported(store):
    """Card rule 5 (documented drops): a message carrying content_type,
    embeds, components, attachments, reactions, metadata, edited_at,
    pinned, ephemeral, expires_at, and a non-default author_type still
    exports cleanly, with the envelope carrying only the agreed fields."""
    msg = await store.send_message(
        channel_id="ch1",
        author_id="user1",
        author_type="agent",
        content="hello",
        content_type="markdown",
        content_blocks=[{"type": "paragraph", "text": "hello"}],
        embeds=[{"kind": "link"}],
        components=[{"kind": "button"}],
        attachments=[{"filename": "x.png"}],
        metadata={"secret": "do-not-export"},
        expires_at=time.time() + 1000,
    )
    await store.pin_message("ch1", msg["id"], pinned_by="user1")
    await store.edit_message(msg["id"], "hello edited")
    await store.add_reaction(msg["id"], "\U0001F44D", "user1")

    exporter = ChatExporter(
        message_store=store,
        identity_map=_identity_map(["user1"]),
    )
    batch = await exporter.export_channel("ch1")
    assert len(batch) == 1
    env = batch[0]
    allowed_keys = {
        "from", "thread", "body", "blocks", "ts", "source", "source_id",
        "reply_to",
    }
    assert set(env.keys()) <= allowed_keys
    for dropped in (
        "content_type", "embeds", "components", "attachments", "reactions",
        "metadata", "edited_at", "pinned", "ephemeral", "expires_at",
        "author_type",
    ):
        assert dropped not in env


@pytest.mark.asyncio
async def test_empty_body_when_no_content(store):
    await store.send_message(
        channel_id="ch1",
        author_id="user1",
        author_type="user",
        content="",
        content_blocks=[],
    )

    exporter = ChatExporter(
        message_store=store,
        identity_map=_identity_map(["user1"]),
    )
    batch = await exporter.export_channel("ch1")
    assert batch[0]["body"] == ""


@pytest.mark.asyncio
async def test_file_writer_receives_utf8_body(store, tmp_path):
    text = "héllo wörld " * 10_000
    await store.send_message(
        channel_id="ch1",
        author_id="user1",
        author_type="user",
        content=text,
        content_blocks=[{"type": "paragraph", "text": text}],
    )

    writer = _make_file_writer(tmp_path)
    exporter = ChatExporter(
        message_store=store,
        identity_map=_identity_map(["user1"]),
        file_writer=writer,
    )
    batch = await exporter.export_channel("ch1")
    body = batch[0]["body"]
    ref_match = re.search(r"chat-export/[^\s\]]+\.txt", body)
    assert ref_match, f"no ref found in body: {body!r}"
    written = (tmp_path / ref_match.group(0)).read_bytes()
    full_envelope = json.loads(written.decode("utf-8"))
    assert full_envelope["body"] == text


# ---------------------------------------------------------------------------
# RED-FIRST proof tests for fix batch (card tsk-yfy5j5).
# Each test below reproduces one of the 4 blockers found in review; they are
# written and run against the UNFIXED source first (captured in
# REDPROOF.txt), then the source is fixed until these go green.
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_oversize_message_preserves_blocks_via_ref(store, tmp_path):
    """Card rule 5: an oversized message must not silently drop its blocks.
    The full original envelope (blocks intact) is written through
    file_writer, and the emitted body carries a reference to it."""
    big_text = "x" * 100_000
    blocks = [
        {"type": "paragraph", "text": big_text},
        {"type": "image", "url": "http://x/big.png"},
    ]
    await store.send_message(
        channel_id="ch1",
        author_id="user1",
        author_type="user",
        content=big_text,
        content_blocks=blocks,
    )
    writer = _make_file_writer(tmp_path)
    exporter = ChatExporter(
        message_store=store,
        identity_map=_identity_map(["user1"]),
        file_writer=writer,
    )
    batch = await exporter.export_channel("ch1")
    env = batch[0]
    assert env["blocks"] == []
    ref_match = re.search(r"chat-export/[^\s\]]+\.txt", env["body"])
    assert ref_match, f"no ref found in body: {env['body']!r}"
    written = (tmp_path / ref_match.group(0)).read_text()
    full_envelope = json.loads(written)
    assert full_envelope["blocks"] == blocks
    assert full_envelope["body"] == flatten_body(blocks)


@pytest.mark.asyncio
async def test_image_only_message_exports_with_placeholder_body(store):
    """Card rule 3: a non-text (e.g. image) block must flatten to a
    descriptive placeholder, never brick the whole channel export."""
    await store.send_message(
        channel_id="ch1",
        author_id="user1",
        author_type="user",
        content="",
        content_blocks=[{"type": "image", "url": "http://x/img.png"}],
    )
    exporter = ChatExporter(
        message_store=store,
        identity_map=_identity_map(["user1"]),
    )
    batch = await exporter.export_channel("ch1")
    assert len(batch) == 1
    assert batch[0]["body"] == "[image: http://x/img.png]"


@pytest.mark.asyncio
async def test_streaming_message_excluded_from_export(store):
    """A message whose state is 'streaming' (or 'error') is not authentic
    history and must be excluded, the same way deleted messages are."""
    await store.send_message(
        channel_id="ch1",
        author_id="user1",
        author_type="agent",
        content="",
        content_blocks=[{"type": "paragraph", "text": "partial"}],
        state="streaming",
    )
    await store.send_message(
        channel_id="ch1",
        author_id="user1",
        author_type="user",
        content="done",
        content_blocks=[{"type": "paragraph", "text": "done"}],
        state="complete",
    )
    exporter = ChatExporter(
        message_store=store,
        identity_map=_identity_map(["user1"]),
    )
    batch = await exporter.export_channel("ch1")
    assert len(batch) == 1
    assert batch[0]["body"] == "done"


@pytest.mark.asyncio
async def test_same_timestamp_reply_sorts_after_parent(store):
    """Card rule 7: sort key (created_at, id) with random ids can put a
    reply before its parent on a timestamp tie. Ids are chosen so the
    naive sort gets it wrong (\"a-reply\" < \"z-parent\" lexically)."""
    ts = time.time()
    await store.ensure_message({
        "id": "z-parent",
        "channel_id": "ch1",
        "author_id": "user1",
        "author_type": "user",
        "content": "parent",
        "content_blocks": [{"type": "paragraph", "text": "parent"}],
        "created_at": ts,
    })
    await store.ensure_message({
        "id": "a-reply",
        "channel_id": "ch1",
        "thread_id": "z-parent",
        "author_id": "user1",
        "author_type": "user",
        "content": "reply",
        "content_blocks": [{"type": "paragraph", "text": "reply"}],
        "created_at": ts,
    })
    exporter = ChatExporter(
        message_store=store,
        identity_map=_identity_map(["user1"]),
    )
    batch = await exporter.export_channel("ch1")
    ids = [e["source_id"] for e in batch]
    assert ids.index("z-parent") < ids.index("a-reply")


@pytest.mark.asyncio
async def test_thread_id_cycle_messages_are_not_dropped(store):
    """A thread_id cycle within a same-timestamp group (the store does not
    validate against crafted self/mutual references) has no topological
    order — but a migration tool must never silently drop the rows."""
    ts = 1700000000.0
    await store.ensure_message({
        "id": "msg-a", "channel_id": "ch1", "author_id": "user1",
        "author_type": "user", "content": "a",
        "content_blocks": [{"type": "paragraph", "text": "a"}],
        "created_at": ts, "thread_id": "msg-b",
    })
    await store.ensure_message({
        "id": "msg-b", "channel_id": "ch1", "author_id": "user1",
        "author_type": "user", "content": "b",
        "content_blocks": [{"type": "paragraph", "text": "b"}],
        "created_at": ts, "thread_id": "msg-a",
    })
    exporter = ChatExporter(
        message_store=store, identity_map=_identity_map(["user1"]),
    )
    batch = await exporter.export_channel("ch1")
    assert {e["source_id"] for e in batch} == {"msg-a", "msg-b"}


@pytest.mark.asyncio
async def test_oversize_envelope_never_exceeds_limit_serialized(store, tmp_path):
    """The 64KB limit applies to the SERIALIZED envelope: body truncation
    must budget for the ref note and the envelope's other fields, not just
    the raw body bytes."""
    big_text = "x" * 100_000
    await store.send_message(
        channel_id="ch1", author_id="user1", author_type="user",
        content=big_text,
        content_blocks=[{"type": "paragraph", "text": big_text}],
    )
    exporter = ChatExporter(
        message_store=store,
        identity_map=_identity_map(["user1"]),
        file_writer=_make_file_writer(tmp_path),
    )
    batch = await exporter.export_channel("ch1")
    env = batch[0]
    serialized = json.dumps(env, ensure_ascii=False).encode("utf-8")
    assert len(serialized) <= 64 * 1024, (
        f"serialized envelope is {len(serialized)} bytes"
    )
