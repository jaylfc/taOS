"""Unit tests for tinyagentos/chat/threads.py."""
from unittest.mock import AsyncMock, MagicMock

import pytest

from tinyagentos.chat.threads import resolve_thread_recipients


def _ch(members, muted=None, channel_id="c1"):
    return {
        "id": channel_id,
        "type": "group",
        "members": members,
        "settings": {"muted": muted or []},
    }


def _cm(parent=None, prior=None):
    cm = MagicMock()
    cm.get_message = AsyncMock(return_value=parent)
    cm.get_thread_messages = AsyncMock(return_value=prior or [])
    return cm


def _msg(author_id, content, thread_id="t1", author_type="user"):
    return {
        "author_id": author_id,
        "author_type": author_type,
        "content": content,
        "thread_id": thread_id,
    }


@pytest.mark.asyncio
async def test_no_thread_id_returns_empty():
    cm = _cm()
    msg = {"author_id": "user", "author_type": "user", "content": "hi"}
    recipients, forced = await resolve_thread_recipients(msg, _ch(["user", "tom"]), cm)
    assert recipients == []
    assert forced == {}


@pytest.mark.asyncio
async def test_none_thread_id_returns_empty():
    cm = _cm()
    msg = {"author_id": "user", "author_type": "user", "content": "hi", "thread_id": None}
    recipients, forced = await resolve_thread_recipients(msg, _ch(["user", "tom"]), cm)
    assert recipients == []
    assert forced == {}


@pytest.mark.asyncio
async def test_empty_thread_id_returns_empty():
    cm = _cm()
    msg = {"author_id": "user", "author_type": "user", "content": "hi", "thread_id": ""}
    recipients, forced = await resolve_thread_recipients(msg, _ch(["user", "tom"]), cm)
    assert recipients == []
    assert forced == {}


@pytest.mark.asyncio
async def test_parent_agent_author_added():
    cm = _cm(parent={"id": "p1", "author_id": "tom", "author_type": "agent"})
    msg = _msg("user", "hello?")
    recipients, forced = await resolve_thread_recipients(
        msg, _ch(["user", "tom", "don"]), cm
    )
    assert "tom" in recipients
    assert "don" not in recipients
    assert forced == {}


@pytest.mark.asyncio
async def test_parent_user_author_not_added():
    cm = _cm(parent={"id": "p1", "author_id": "user", "author_type": "user"})
    msg = _msg("user", "hello?")
    recipients, forced = await resolve_thread_recipients(
        msg, _ch(["user", "tom", "don"]), cm
    )
    assert recipients == []
    assert forced == {}


@pytest.mark.asyncio
async def test_parent_none_not_added():
    cm = _cm(parent=None)
    msg = _msg("user", "hello?")
    recipients, forced = await resolve_thread_recipients(
        msg, _ch(["user", "tom", "don"]), cm
    )
    assert recipients == []
    assert forced == {}


@pytest.mark.asyncio
async def test_parent_with_none_author_id_not_added():
    cm = _cm(parent={"id": "p1", "author_id": None, "author_type": "agent"})
    msg = _msg("user", "hello?")
    recipients, forced = await resolve_thread_recipients(
        msg, _ch(["user", "tom", "don"]), cm
    )
    assert recipients == []


@pytest.mark.asyncio
async def test_parent_same_as_current_author_not_added():
    cm = _cm(parent={"id": "p1", "author_id": "tom", "author_type": "agent"})
    msg = _msg("tom", "follow-up", author_type="agent")
    recipients, forced = await resolve_thread_recipients(
        msg, _ch(["user", "tom", "don"]), cm
    )
    assert "tom" not in recipients


@pytest.mark.asyncio
async def test_parent_muted_not_added():
    cm = _cm(parent={"id": "p1", "author_id": "tom", "author_type": "agent"})
    msg = _msg("user", "hello?")
    recipients, forced = await resolve_thread_recipients(
        msg, _ch(["user", "tom", "don"], muted=["tom"]), cm
    )
    assert "tom" not in recipients


@pytest.mark.asyncio
async def test_prior_repliers_added():
    cm = _cm(
        parent={"id": "p1", "author_id": "user", "author_type": "user"},
        prior=[
            {"author_id": "don", "author_type": "agent"},
            {"author_id": "linus", "author_type": "agent"},
        ],
    )
    msg = _msg("user", "more?")
    recipients, forced = await resolve_thread_recipients(
        msg, _ch(["user", "tom", "don", "linus"]), cm
    )
    assert sorted(recipients) == ["don", "linus"]
    assert forced == {}


@pytest.mark.asyncio
async def test_prior_replier_same_as_author_excluded():
    cm = _cm(
        parent={"id": "p1", "author_id": "user", "author_type": "user"},
        prior=[{"author_id": "tom", "author_type": "agent"}],
    )
    msg = _msg("tom", "self-reply", author_type="agent")
    recipients, forced = await resolve_thread_recipients(
        msg, _ch(["user", "tom", "don"]), cm
    )
    assert "tom" not in recipients


@pytest.mark.asyncio
async def test_prior_replier_muted_excluded():
    cm = _cm(
        parent={"id": "p1", "author_id": "user", "author_type": "user"},
        prior=[{"author_id": "tom", "author_type": "agent"}],
    )
    msg = _msg("user", "hi")
    recipients, forced = await resolve_thread_recipients(
        msg, _ch(["user", "tom", "don"], muted=["tom"]), cm
    )
    assert "tom" not in recipients


@pytest.mark.asyncio
async def test_prior_user_replier_not_added():
    cm = _cm(
        parent={"id": "p1", "author_id": "user", "author_type": "user"},
        prior=[{"author_id": "user", "author_type": "user"}],
    )
    msg = _msg("user", "hi again")
    recipients, forced = await resolve_thread_recipients(
        msg, _ch(["user", "tom"]), cm
    )
    assert recipients == []


@pytest.mark.asyncio
async def test_prior_replier_with_none_author_id_skipped():
    cm = _cm(
        parent={"id": "p1", "author_id": "user", "author_type": "user"},
        prior=[{"author_id": None, "author_type": "agent"}],
    )
    msg = _msg("user", "hi")
    recipients, forced = await resolve_thread_recipients(
        msg, _ch(["user", "tom"]), cm
    )
    assert recipients == []


@pytest.mark.asyncio
async def test_explicit_mention_forces_respond():
    cm = _cm(parent={"id": "p1", "author_id": "user", "author_type": "user"})
    msg = _msg("user", "@tom please review")
    recipients, forced = await resolve_thread_recipients(
        msg, _ch(["user", "tom", "don"]), cm
    )
    assert recipients == ["tom"]
    assert forced == {"tom": True}


@pytest.mark.asyncio
async def test_mention_not_in_members_ignored():
    cm = _cm(parent={"id": "p1", "author_id": "user", "author_type": "user"})
    msg = _msg("user", "@unknown hello")
    recipients, forced = await resolve_thread_recipients(
        msg, _ch(["user", "tom"]), cm
    )
    assert recipients == []
    assert forced == {}


@pytest.mark.asyncio
async def test_mention_of_muted_agent_ignored():
    cm = _cm(parent={"id": "p1", "author_id": "user", "author_type": "user"})
    msg = _msg("user", "@tom hello")
    recipients, forced = await resolve_thread_recipients(
        msg, _ch(["user", "tom"], muted=["tom"]), cm
    )
    assert recipients == []
    assert forced == {}


@pytest.mark.asyncio
async def test_mention_of_author_excluded():
    cm = _cm(parent={"id": "p1", "author_id": "user", "author_type": "user"})
    msg = _msg("tom", "@tom to myself", author_type="agent")
    recipients, forced = await resolve_thread_recipients(
        msg, _ch(["user", "tom", "don"]), cm
    )
    assert "tom" not in recipients


@pytest.mark.asyncio
async def test_at_all_escalates_to_all_candidates():
    cm = _cm(parent={"id": "p1", "author_id": "user", "author_type": "user"})
    msg = _msg("user", "@all weigh in")
    recipients, forced = await resolve_thread_recipients(
        msg, _ch(["user", "tom", "don", "linus"]), cm
    )
    assert sorted(recipients) == ["don", "linus", "tom"]
    assert forced == {"tom": True, "don": True, "linus": True}


@pytest.mark.asyncio
async def test_at_all_excludes_muted():
    cm = _cm(parent={"id": "p1", "author_id": "user", "author_type": "user"})
    msg = _msg("user", "@all weigh in")
    recipients, forced = await resolve_thread_recipients(
        msg, _ch(["user", "tom", "don", "linus"], muted=["don"]), cm
    )
    assert sorted(recipients) == ["linus", "tom"]
    assert "don" not in forced


@pytest.mark.asyncio
async def test_at_all_excludes_author():
    cm = _cm(parent={"id": "p1", "author_id": "user", "author_type": "user"})
    msg = _msg("tom", "@all respond", author_type="agent")
    recipients, forced = await resolve_thread_recipients(
        msg, _ch(["user", "tom", "don"]), cm
    )
    assert sorted(recipients) == ["don"]
    assert "tom" not in recipients


@pytest.mark.asyncio
async def test_at_all_excludes_user_member():
    """'user' is never a candidate even with @all."""
    cm = _cm(parent={"id": "p1", "author_id": "user", "author_type": "user"})
    msg = _msg("don", "@all respond", author_type="agent")
    recipients, forced = await resolve_thread_recipients(
        msg, _ch(["user", "tom", "don"]), cm
    )
    assert sorted(recipients) == ["tom"]
    assert "user" not in recipients


@pytest.mark.asyncio
async def test_at_all_empty_channel():
    cm = _cm(parent={"id": "p1", "author_id": "user", "author_type": "user"})
    msg = _msg("user", "@all anyone?")
    recipients, forced = await resolve_thread_recipients(msg, _ch(["user"]), cm)
    assert recipients == []
    assert forced == {}


@pytest.mark.asyncio
async def test_combined_parent_prior_and_mentions():
    cm = _cm(
        parent={"id": "p1", "author_id": "tom", "author_type": "agent"},
        prior=[{"author_id": "don", "author_type": "agent"}],
    )
    msg = _msg("user", "@linus thoughts?")
    recipients, forced = await resolve_thread_recipients(
        msg, _ch(["user", "tom", "don", "linus"]), cm
    )
    assert sorted(recipients) == ["don", "linus", "tom"]
    assert forced == {"linus": True}


@pytest.mark.asyncio
async def test_mention_forces_even_if_already_recipient():
    """Mentioned agent already a prior replier: still forced."""
    cm = _cm(
        parent={"id": "p1", "author_id": "user", "author_type": "user"},
        prior=[{"author_id": "tom", "author_type": "agent"}],
    )
    msg = _msg("user", "@tom again")
    recipients, forced = await resolve_thread_recipients(
        msg, _ch(["user", "tom", "don"]), cm
    )
    assert recipients == ["tom"]
    assert forced == {"tom": True}


@pytest.mark.asyncio
async def test_no_content_no_mentions():
    cm = _cm(parent={"id": "p1", "author_id": "user", "author_type": "user"})
    msg = _msg("user", "")
    recipients, forced = await resolve_thread_recipients(
        msg, _ch(["user", "tom"]), cm
    )
    assert recipients == []
    assert forced == {}


@pytest.mark.asyncio
async def test_none_content_no_mentions():
    cm = _cm(parent={"id": "p1", "author_id": "user", "author_type": "user"})
    msg = _msg("user", None)
    recipients, forced = await resolve_thread_recipients(
        msg, _ch(["user", "tom"]), cm
    )
    assert recipients == []
    assert forced == {}


@pytest.mark.asyncio
async def test_recipients_are_sorted():
    cm = _cm(
        parent={"id": "p1", "author_id": "zebra", "author_type": "agent"},
        prior=[
            {"author_id": "alpha", "author_type": "agent"},
            {"author_id": "mango", "author_type": "agent"},
        ],
    )
    msg = _msg("user", "hi all")
    recipients, forced = await resolve_thread_recipients(
        msg, _ch(["user", "zebra", "alpha", "mango"]), cm
    )
    assert recipients == sorted(recipients)
    assert recipients == ["alpha", "mango", "zebra"]


@pytest.mark.asyncio
async def test_empty_members():
    """Parent-author check does not filter by members list, so tom is still added."""
    cm = _cm(parent={"id": "p1", "author_id": "tom", "author_type": "agent"})
    msg = _msg("user", "hi")
    recipients, forced = await resolve_thread_recipients(msg, _ch([]), cm)
    assert recipients == ["tom"]
    assert forced == {}


@pytest.mark.asyncio
async def test_none_members():
    """Parent-author check does not filter by members list, so tom is still added."""
    channel = {"id": "c1", "type": "group", "members": None, "settings": {"muted": []}}
    cm = _cm(parent={"id": "p1", "author_id": "tom", "author_type": "agent"})
    msg = _msg("user", "hi")
    recipients, forced = await resolve_thread_recipients(msg, channel, cm)
    assert recipients == ["tom"]


@pytest.mark.asyncio
async def test_empty_settings():
    channel = {"id": "c1", "type": "group", "members": ["user", "tom"], "settings": {}}
    cm = _cm(parent={"id": "p1", "author_id": "tom", "author_type": "agent"})
    msg = _msg("user", "hi")
    recipients, forced = await resolve_thread_recipients(msg, channel, cm)
    assert "tom" in recipients


@pytest.mark.asyncio
async def test_none_settings():
    channel = {"id": "c1", "type": "group", "members": ["user", "tom"], "settings": None}
    cm = _cm(parent={"id": "p1", "author_id": "tom", "author_type": "agent"})
    msg = _msg("user", "hi")
    recipients, forced = await resolve_thread_recipients(msg, channel, cm)
    assert "tom" in recipients


@pytest.mark.asyncio
async def test_none_muted():
    channel = {
        "id": "c1",
        "type": "group",
        "members": ["user", "tom"],
        "settings": {"muted": None},
    }
    cm = _cm(parent={"id": "p1", "author_id": "tom", "author_type": "agent"})
    msg = _msg("user", "hi")
    recipients, forced = await resolve_thread_recipients(msg, channel, cm)
    assert "tom" in recipients


@pytest.mark.asyncio
async def test_empty_member_slugs_filtered():
    """Empty string members are filtered out."""
    cm = _cm(parent={"id": "p1", "author_id": "tom", "author_type": "agent"})
    msg = _msg("user", "hi")
    recipients, forced = await resolve_thread_recipients(
        msg, _ch(["user", "", "tom"]), cm
    )
    assert recipients == ["tom"]


@pytest.mark.asyncio
async def test_get_thread_messages_called_with_correct_args():
    cm = _cm(parent={"id": "p1", "author_id": "user", "author_type": "user"})
    msg = _msg("user", "hi", thread_id="p1")
    channel = _ch(["user", "tom"], channel_id="my-channel")
    await resolve_thread_recipients(msg, channel, cm)
    cm.get_thread_messages.assert_called_once_with(
        channel_id="my-channel", parent_id="p1", limit=200
    )


@pytest.mark.asyncio
async def test_get_message_called_with_thread_id():
    cm = _cm(parent={"id": "p1", "author_id": "user", "author_type": "user"})
    msg = _msg("user", "hi", thread_id="parent-msg-id")
    await resolve_thread_recipients(msg, _ch(["user", "tom"]), cm)
    cm.get_message.assert_called_once_with("parent-msg-id")


@pytest.mark.asyncio
async def test_duplicate_prior_repliers_deduplicated():
    cm = _cm(
        parent={"id": "p1", "author_id": "user", "author_type": "user"},
        prior=[
            {"author_id": "tom", "author_type": "agent"},
            {"author_id": "tom", "author_type": "agent"},
            {"author_id": "tom", "author_type": "agent"},
        ],
    )
    msg = _msg("user", "hi")
    recipients, forced = await resolve_thread_recipients(
        msg, _ch(["user", "tom", "don"]), cm
    )
    assert recipients == ["tom"]


@pytest.mark.asyncio
async def test_parent_and_prior_same_agent():
    """Same agent is parent and prior replier: appears once."""
    cm = _cm(
        parent={"id": "p1", "author_id": "tom", "author_type": "agent"},
        prior=[{"author_id": "tom", "author_type": "agent"}],
    )
    msg = _msg("user", "hi")
    recipients, forced = await resolve_thread_recipients(
        msg, _ch(["user", "tom", "don"]), cm
    )
    assert recipients == ["tom"]


@pytest.mark.asyncio
async def test_at_all_with_no_other_agents():
    """@all in a channel with only the author agent."""
    cm = _cm(parent={"id": "p1", "author_id": "user", "author_type": "user"})
    msg = _msg("tom", "@all anyone?", author_type="agent")
    recipients, forced = await resolve_thread_recipients(
        msg, _ch(["user", "tom"]), cm
    )
    assert recipients == []
    assert forced == {}


@pytest.mark.asyncio
async def test_multiple_mentions():
    cm = _cm(parent={"id": "p1", "author_id": "user", "author_type": "user"})
    msg = _msg("user", "@tom @don please")
    recipients, forced = await resolve_thread_recipients(
        msg, _ch(["user", "tom", "don", "linus"]), cm
    )
    assert sorted(recipients) == ["don", "tom"]
    assert forced == {"tom": True, "don": True}


@pytest.mark.asyncio
async def test_mention_case_insensitive():
    """Mentions are case-insensitive (parse_mentions lowercases)."""
    cm = _cm(parent={"id": "p1", "author_id": "user", "author_type": "user"})
    msg = _msg("user", "@Tom hello")
    recipients, forced = await resolve_thread_recipients(
        msg, _ch(["user", "tom", "don"]), cm
    )
    assert recipients == ["tom"]
    assert forced == {"tom": True}
