from tinyagentos.chat.context_window import build_context_window, estimate_tokens


def _msg(author, content, kind="user"):
    return {"author_id": author, "author_type": kind, "content": content}


class TestEstimateTokens:
    def test_empty_string_returns_zero(self):
        assert estimate_tokens("") == 0

    def test_none_returns_zero(self):
        assert estimate_tokens(None) == 0

    def test_single_char_returns_one(self):
        assert estimate_tokens("a") == 1

    def test_three_chars_returns_one(self):
        assert estimate_tokens("abc") == 1

    def test_four_chars_returns_one(self):
        assert estimate_tokens("abcd") == 1

    def test_five_chars_returns_one(self):
        assert estimate_tokens("abcde") == 1

    def test_seven_chars_returns_one(self):
        assert estimate_tokens("abcdefg") == 1

    def test_eight_chars_returns_two(self):
        assert estimate_tokens("abcdefgh") == 2

    def test_large_text(self):
        assert estimate_tokens("x" * 1000) == 250

    def test_boundary_3_chars_is_one(self):
        assert estimate_tokens("abc") == 1

    def test_boundary_4_chars_is_one(self):
        assert estimate_tokens("abcd") == 1

    def test_boundary_5_chars_is_one(self):
        assert estimate_tokens("abcde") == 1


class TestBuildContextWindow:
    def test_empty_list(self):
        assert build_context_window([], limit=20, max_tokens=1000) == []

    def test_preserves_oldest_first_order(self):
        msgs = [_msg("u1", "a"), _msg("tom", "b", "agent"), _msg("u2", "c")]
        ctx = build_context_window(msgs, limit=20, max_tokens=1000)
        assert [m["content"] for m in ctx] == ["a", "b", "c"]

    def test_output_shape_has_three_keys(self):
        msgs = [_msg("u1", "hi")]
        ctx = build_context_window(msgs, limit=20, max_tokens=1000)
        assert len(ctx) == 1
        row = ctx[0]
        assert set(row.keys()) == {"author_id", "author_type", "content"}
        assert row["author_id"] == "u1"
        assert row["author_type"] == "user"
        assert row["content"] == "hi"

    def test_system_messages_excluded(self):
        msgs = [
            _msg("u1", "hi"),
            _msg("sys", "/lively enabled", "system"),
            _msg("tom", "yo", "agent"),
        ]
        ctx = build_context_window(msgs, limit=20, max_tokens=1000)
        assert [m["content"] for m in ctx] == ["hi", "yo"]
        assert all(m["author_type"] != "system" for m in ctx)

    def test_all_system_messages_returns_empty(self):
        msgs = [
            _msg("s1", "cmd1", "system"),
            _msg("s2", "cmd2", "system"),
        ]
        assert build_context_window(msgs, limit=20, max_tokens=1000) == []

    def test_limit_drops_oldest(self):
        msgs = [_msg("u", str(i)) for i in range(30)]
        ctx = build_context_window(msgs, limit=20, max_tokens=100000)
        assert len(ctx) == 20
        assert ctx[0]["content"] == "10"
        assert ctx[-1]["content"] == "29"

    def test_limit_of_one_keeps_last(self):
        msgs = [_msg("u", "first"), _msg("u", "second"), _msg("u", "third")]
        ctx = build_context_window(msgs, limit=1, max_tokens=10000)
        assert len(ctx) == 1
        assert ctx[0]["content"] == "third"

    def test_token_budget_trims_from_front(self):
        long = "x" * 2000
        msgs = [
            _msg("u1", long),
            _msg("tom", long, "agent"),
            _msg("u2", long),
        ]
        ctx = build_context_window(msgs, limit=20, max_tokens=800)
        total = sum(estimate_tokens(m["content"]) for m in ctx)
        assert total <= 800

    def test_token_budget_can_drop_all(self):
        long = "x" * 10000
        msgs = [_msg("u1", long)]
        ctx = build_context_window(msgs, limit=20, max_tokens=1)
        assert ctx == []

    def test_limit_applied_before_token_budget(self):
        msgs = [_msg("u", "a" * 100) for i in range(50)]
        ctx = build_context_window(msgs, limit=5, max_tokens=100000)
        assert len(ctx) == 5

    def test_none_content_becomes_empty_string(self):
        msgs = [{"author_id": "u1", "author_type": "user", "content": None}]
        ctx = build_context_window(msgs, limit=20, max_tokens=1000)
        assert ctx[0]["content"] == ""

    def test_missing_content_key_becomes_empty_string(self):
        msgs = [{"author_id": "u1", "author_type": "user"}]
        ctx = build_context_window(msgs, limit=20, max_tokens=1000)
        assert ctx[0]["content"] == ""

    def test_missing_author_id_becomes_none(self):
        msgs = [{"author_type": "user", "content": "hi"}]
        ctx = build_context_window(msgs, limit=20, max_tokens=1000)
        assert ctx[0]["author_id"] is None

    def test_missing_author_type_becomes_none(self):
        msgs = [{"author_id": "u1", "content": "hi"}]
        ctx = build_context_window(msgs, limit=20, max_tokens=1000)
        assert ctx[0]["author_type"] is None

    def test_empty_content_string(self):
        msgs = [_msg("u1", "")]
        ctx = build_context_window(msgs, limit=20, max_tokens=1000)
        assert len(ctx) == 1
        assert ctx[0]["content"] == ""

    def test_mixed_system_and_user_with_limit(self):
        msgs = [
            _msg("s1", "cmd", "system"),
            _msg("u1", "a"),
            _msg("s2", "cmd2", "system"),
            _msg("u2", "b"),
            _msg("s3", "cmd3", "system"),
            _msg("u3", "c"),
        ]
        ctx = build_context_window(msgs, limit=2, max_tokens=10000)
        assert len(ctx) == 2
        assert [m["content"] for m in ctx] == ["b", "c"]

    def test_zero_limit_keeps_all_due_to_slice_behavior(self):
        msgs = [_msg("u1", "hi")]
        ctx = build_context_window(msgs, limit=0, max_tokens=1000)
        assert len(ctx) == 1

    def test_zero_max_tokens_returns_empty(self):
        msgs = [_msg("u1", "hi")]
        ctx = build_context_window(msgs, limit=20, max_tokens=0)
        assert ctx == []

    def test_exact_token_budget_boundary(self):
        content = "x" * 8
        msgs = [_msg("u1", content)]
        ctx = build_context_window(msgs, limit=20, max_tokens=2)
        assert len(ctx) == 1
        assert ctx[0]["content"] == content

    def test_one_over_token_budget_trims(self):
        content = "x" * 9
        msgs = [
            _msg("u1", content),
            _msg("u2", "a"),
        ]
        ctx = build_context_window(msgs, limit=20, max_tokens=2)
        total = sum(estimate_tokens(m["content"]) for m in ctx)
        assert total <= 2

    def test_single_message_under_budget(self):
        msgs = [_msg("u1", "hello")]
        ctx = build_context_window(msgs, limit=20, max_tokens=100)
        assert len(ctx) == 1
        assert ctx[0]["content"] == "hello"

    def test_preserves_author_fields(self):
        msgs = [
            {"author_id": "agent-1", "author_type": "agent", "content": "reply"},
        ]
        ctx = build_context_window(msgs, limit=20, max_tokens=1000)
        assert ctx[0]["author_id"] == "agent-1"
        assert ctx[0]["author_type"] == "agent"
