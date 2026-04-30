import time
import pytest
from tinyagentos.shortcuts.tickets import (
    JtiTracker,
    Ticket,
    mint_ticket,
    validate_ticket,
)

SIGNING_KEY = b"test-signing-key-32-bytes-padded"


def test_mint_returns_ticket_and_token():
    ticket, token = mint_ticket(
        agent_id="agent-123",
        shortcut_idx=0,
        scope="dashboard",
        signing_key=SIGNING_KEY,
        worker_url="http://127.0.0.1:6969",
        ttl=30,
    )
    assert ticket.agent_id == "agent-123"
    assert ticket.shortcut_idx == 0
    assert ticket.scope == "dashboard"
    assert ticket.worker_url == "http://127.0.0.1:6969"
    assert isinstance(ticket.jti, str) and len(ticket.jti) == 32
    assert ticket.exp > int(time.time())
    assert isinstance(token, str) and len(token) > 0


def test_validate_returns_ticket_for_valid_token():
    ticket, token = mint_ticket(
        agent_id="agent-456",
        shortcut_idx=1,
        scope="terminal",
        signing_key=SIGNING_KEY,
        worker_url="http://127.0.0.1:6969",
        ttl=30,
    )
    tracker = JtiTracker()
    decoded = validate_ticket(token, signing_key=SIGNING_KEY, tracker=tracker)
    assert decoded.agent_id == "agent-456"
    assert decoded.shortcut_idx == 1
    assert decoded.scope == "terminal"


def test_validate_raises_on_bad_hmac():
    _, token = mint_ticket(
        agent_id="x",
        shortcut_idx=0,
        scope="dashboard",
        signing_key=SIGNING_KEY,
        worker_url="http://127.0.0.1:6969",
        ttl=30,
    )
    wrong_key = b"wrong-key-32-bytes-padding-paddd"
    tracker = JtiTracker()
    with pytest.raises(ValueError, match="invalid signature"):
        validate_ticket(token, signing_key=wrong_key, tracker=tracker)


def test_validate_raises_on_expired_ticket():
    _, token = mint_ticket(
        agent_id="x",
        shortcut_idx=0,
        scope="dashboard",
        signing_key=SIGNING_KEY,
        worker_url="http://127.0.0.1:6969",
        ttl=-1,  # already expired
    )
    tracker = JtiTracker()
    with pytest.raises(ValueError, match="ticket expired"):
        validate_ticket(token, signing_key=SIGNING_KEY, tracker=tracker)


def test_validate_raises_on_replayed_jti():
    ticket, token = mint_ticket(
        agent_id="x",
        shortcut_idx=0,
        scope="dashboard",
        signing_key=SIGNING_KEY,
        worker_url="http://127.0.0.1:6969",
        ttl=30,
    )
    tracker = JtiTracker()
    validate_ticket(token, signing_key=SIGNING_KEY, tracker=tracker)
    with pytest.raises(ValueError, match="replayed jti"):
        validate_ticket(token, signing_key=SIGNING_KEY, tracker=tracker)


def test_jti_tracker_seen_false_before_record():
    tracker = JtiTracker()
    assert tracker.seen("abc") is False


def test_jti_tracker_seen_true_after_record():
    tracker = JtiTracker()
    tracker.record("abc", exp=int(time.time()) + 30)
    assert tracker.seen("abc") is True


def test_ticket_dataclass_fields():
    t = Ticket(
        agent_id="a",
        shortcut_idx=2,
        scope="terminal",
        exp=9999999999,
        jti="aabbcc",
        worker_url="http://x",
    )
    assert t.agent_id == "a"
    assert t.shortcut_idx == 2
