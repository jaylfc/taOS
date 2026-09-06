from tinyagentos.chat.context_window import (
    build_context_window,
    estimate_tokens,
    history_token_budget,
)


def _msg(author, content, kind="user"):
    return {"author_id": author, "author_type": kind, "content": content}


def test_history_token_budget_unknown_returns_default():
    # Cloud/aliased models have no local context window; keep today's 4000.
    assert history_token_budget(None) == 4000
    assert history_token_budget(0) == 4000
    assert history_token_budget(False) == 4000


def test_history_token_budget_small_window_is_smaller_than_default():
    # 8192 - 10000 system reserve - 1024 response reserve underflows,
    # so the floor applies; result must be below the unknown-model default.
    budget = history_token_budget(8192)
    assert budget < 4000
    assert budget == 512


def test_history_token_budget_floored_at_512():
    # Tiny windows still leave a usable history slice of at least 512.
    assert history_token_budget(100) == 512
    assert history_token_budget(1) == 512


def test_history_token_budget_known_small_rkllm_window():
    # rkllm manifests (e.g. qwen2.5-1.5b-rkllm) declare 4096. 4096 - 10000
    # system reserve - 1024 response reserve underflows, so the 512 floor
    # applies rather than the 4000 unknown-default (#1740).
    assert history_token_budget(4096) == 512


def test_build_context_window_budgets_for_known_small_window():
    # With a 4096-token model the history budget is only 512 tokens, so a
    # window of chatty messages must be trimmed hard and oldest-first, never
    # exceeding that small budget (the #1740 budget math with a real value).
    budget = history_token_budget(4096)
    assert budget == 512

    message = "x" * 400  # 100 tokens per message
    msgs = [_msg("user", message) for _ in range(20)]  # 2000 tokens total
    ctx = build_context_window(msgs, limit=20, max_tokens=budget)

    total = sum(estimate_tokens(m["content"]) for m in ctx)
    assert total <= budget
    assert len(ctx) < 20
    # Oldest dropped first -> the kept set is a contiguous suffix.
    assert [m["content"] for m in ctx] == [message] * len(ctx)


def test_history_token_budget_large_window_subtracts_reserves():
    # 32768 - 10000 - 1024 = 21744
    assert history_token_budget(32768) == 21744


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
    assert estimate_tokens("a") == 1
    assert estimate_tokens("abc") == 1
    assert estimate_tokens("abcd") == 1
    assert estimate_tokens("a" * 100) == 25
