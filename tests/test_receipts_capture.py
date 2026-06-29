import pytest
import pytest_asyncio

from tinyagentos.receipt_store import ReceiptStore
from tinyagentos.receipts import derive_io, emit_tool_receipt, hash_text, redact_args, summarize_result


@pytest_asyncio.fixture
async def store(tmp_path):
    s = ReceiptStore(tmp_path / "receipts.db")
    await s.init()
    yield s
    await s.close()


def test_redact_masks_secret_key_names():
    red, redactions = redact_args({"path": "a.py", "api_key": "whatever", "token": "abc"})
    assert red["path"] == "a.py"
    assert red["api_key"] == "[REDACTED]"
    assert red["token"] == "[REDACTED]"
    reasons = {r["field"]: r["reason"] for r in redactions}
    assert reasons == {"api_key": "secret-key-name", "token": "secret-key-name"}


def test_redact_masks_token_patterns_in_values():
    red, redactions = redact_args({"cmd": "export X=ghp_0123456789abcdefghijABCDEFGHIJ012345"})
    assert "[REDACTED]" in red["cmd"]
    assert redactions[0]["reason"] == "token-pattern"


def test_redact_leaves_benign_values_untouched():
    red, redactions = redact_args({"path": "src/main.py", "count": 3})
    assert red == {"path": "src/main.py", "count": 3}
    assert redactions == []


def test_redact_masks_non_string_secret_values():
    # A secret passed as a non-string (int, list, ...) under a secret-named key
    # must still be masked, not slip through the string-only guard.
    red, redactions = redact_args({"api_key": 12345, "token": ["a", "b"]})
    assert red == {"api_key": "[REDACTED]", "token": "[REDACTED]"}
    assert {r["field"] for r in redactions} == {"api_key", "token"}


def test_redact_does_not_over_mask_long_benign_strings():
    # Long but benign values (paths, hex digests, base64) must survive: an audit
    # ledger that corrupts legitimate data is worse than useless.
    digest = "a1b2c3d4e5f6a7b8c9d0e1f2a3b4c5d6e7f8a9b0c1d2e3f4"  # 48-char hex
    red, redactions = redact_args({"sha": digest, "path": "/very/long/" + "x" * 60 + "/file.py"})
    assert red["sha"] == digest
    assert red["path"].endswith("/file.py")
    assert redactions == []


def test_summarize_result_variants():
    assert summarize_result({"error": "boom"})[2] == "error"
    out, _summary, stop = summarize_result({"status": "written", "bytes": 12})
    assert stop == "completed" and out == ""
    assert summarize_result("plain string")[2] == "completed"


def test_derive_io_byte_count_is_utf8_not_char_count():
    # Non-ASCII content: bytes must be the UTF-8 byte length, not the char count.
    content = "h\u00e9llo"  # e-acute is 2 bytes in UTF-8: 6 bytes, 5 chars
    _, fc = derive_io("file_write", {"path": "a.py", "content": content}, {"status": "written"})
    assert fc[0]["bytes"] == len(content.encode("utf-8")) == 6
    assert fc[0]["bytes"] != len(content)


def test_derive_io_byte_count_handles_bytes_content():
    # bytes/bytearray content must count actual bytes, not len(str(b"...")) repr.
    content = b"h\xc3\xa9llo"  # UTF-8 for h-e-acute-llo: 6 bytes
    _, fc = derive_io("file_write", {"path": "a.bin", "content": content}, {"status": "written"})
    assert fc[0]["bytes"] == len(content) == 6


def test_hash_text_is_stable_and_prefixed():
    h = hash_text("hello")
    assert h.startswith("sha256:") and h == hash_text("hello")
    assert hash_text("hello") != hash_text("world")


def test_derive_io_file_write_hashes_content():
    refs, fc = derive_io("file_write", {"path": "a.py", "content": "hello"}, {"status": "written", "bytes": 5})
    assert refs[0]["name"] == "content" and refs[0]["hash"] == hash_text("hello")
    assert fc[0]["path"] == "a.py" and fc[0]["hash_after"] == hash_text("hello") and fc[0]["bytes"] == 5


def test_derive_io_file_write_error_has_no_files_changed():
    refs, fc = derive_io("file_write", {"path": "a.py", "content": "x"}, {"error": "Path outside workspace"})
    assert refs and fc == []  # input recorded, but nothing was actually written


def test_derive_io_file_write_unconfirmed_result_records_no_change():
    # A non-error result that does NOT confirm the write (no status="written")
    # must not put a false file change in the ledger.
    refs, fc = derive_io("file_write", {"path": "a.py", "content": "x"}, {"queued": True})
    assert refs and fc == []
    refs2, fc2 = derive_io("file_write", {"path": "a.py", "content": "x"}, "weird-non-dict")
    assert refs2 and fc2 == []


def test_derive_io_file_write_prefers_tool_reported_bytes():
    # When the tool reports the bytes it wrote, trust that over recomputing.
    _, fc = derive_io("file_write", {"path": "a.py", "content": "hello"}, {"status": "written", "bytes": 99})
    assert fc[0]["bytes"] == 99


def test_derive_io_code_and_read():
    refs, _ = derive_io("code_exec", {"code": "print(1)"}, {"returncode": 0})
    assert refs[0]["name"] == "code" and refs[0]["hash"] == hash_text("print(1)")
    refs2, _ = derive_io("file_read", {"path": "a.py"}, {"content": "data"})
    assert any(r.get("name") == "content_read" and r["hash"] == hash_text("data") for r in refs2)


@pytest.mark.asyncio
async def test_emit_tool_receipt_writes_a_receipt(store):
    await emit_tool_receipt(
        store, agent="taos-dev-20260629-1", tool_name="file_write",
        args={"path": "a.py", "content": "hello", "secret": "shh"},
        result={"status": "written", "bytes": 5},
    )
    rows = await store.list(agent_canonical_id="taos-dev-20260629-1")
    assert len(rows) == 1
    r = rows[0]
    assert r["tool_name"] == "file_write"
    assert r["tool_args"]["secret"] == "[REDACTED]"      # baseline redaction applied
    fc = r["files_changed"][0]
    assert fc["path"] == "a.py" and fc["bytes"] == 5 and fc["hash_after"].startswith("sha256:")
    assert any(ir.get("name") == "content" for ir in r["input_refs"])
    assert r["stop_reason"] == "completed"
    assert any(x["field"] == "secret" for x in r["redactions"])


@pytest.mark.asyncio
async def test_emit_is_fail_soft(store):
    class _Boom:
        async def record(self, *a, **k):
            raise RuntimeError("db down")
    # Must not raise even though the store errors.
    await emit_tool_receipt(_Boom(), agent="a", tool_name="x", args={}, result={})


@pytest.mark.asyncio
async def test_emit_noops_without_agent_or_store(store):
    # No agent -> nothing written; no store -> no crash.
    await emit_tool_receipt(store, agent="", tool_name="x", args={}, result={})
    await emit_tool_receipt(None, agent="a", tool_name="x", args={}, result={})
    assert await store.list() == []
