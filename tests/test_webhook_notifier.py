from unittest.mock import AsyncMock, patch

import httpx
import pytest

from tinyagentos.webhook_notifier import WebhookNotifier


def _make_config(*wh_types):
    webhooks = []
    for i, t in enumerate(wh_types):
        wh = {"url": f"https://example.com/hook/{i}", "type": t}
        if t == "telegram":
            wh["bot_token"] = "123456:ABC"
            wh["chat_id"] = "-100123"
        webhooks.append(wh)
    return {"webhooks": webhooks}


# --- notify: no webhooks configured ---

@pytest.mark.asyncio
async def test_notify_no_webhooks_returns_immediately():
    notifier = WebhookNotifier({"webhooks": []})
    # Should not raise, should return without doing anything
    await notifier.notify("title", "msg")


@pytest.mark.asyncio
async def test_notify_missing_webhooks_key():
    notifier = WebhookNotifier({})
    await notifier.notify("title", "msg")


# --- notify: generic webhook ---

@pytest.mark.asyncio
async def test_notify_generic_posts_correct_payload():
    config = _make_config("generic")
    notifier = WebhookNotifier(config)
    wh = config["webhooks"][0]

    with patch("tinyagentos.webhook_notifier.httpx.AsyncClient") as mock_client_cls:
        mock_client = AsyncMock()
        mock_client_cls.return_value.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client_cls.return_value.__aexit__ = AsyncMock(return_value=False)

        await notifier.notify("Hello", "World", "info")

    mock_client.post.assert_called_once_with(
        wh["url"],
        json={"title": "Hello", "message": "World", "level": "info"},
    )


# --- notify: slack webhook ---

@pytest.mark.asyncio
async def test_notify_slack_posts_correct_payload():
    config = _make_config("slack")
    notifier = WebhookNotifier(config)
    wh = config["webhooks"][0]

    with patch("tinyagentos.webhook_notifier.httpx.AsyncClient") as mock_client_cls:
        mock_client = AsyncMock()
        mock_client_cls.return_value.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client_cls.return_value.__aexit__ = AsyncMock(return_value=False)

        await notifier.notify("Alert", "Something happened", "warning")

    mock_client.post.assert_called_once_with(
        wh["url"],
        json={"text": "*Alert*\nSomething happened"},
    )


# --- notify: discord webhook ---

@pytest.mark.asyncio
async def test_notify_discord_info_color():
    config = _make_config("discord")
    notifier = WebhookNotifier(config)
    wh = config["webhooks"][0]

    with patch("tinyagentos.webhook_notifier.httpx.AsyncClient") as mock_client_cls:
        mock_client = AsyncMock()
        mock_client_cls.return_value.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client_cls.return_value.__aexit__ = AsyncMock(return_value=False)

        await notifier.notify("T", "M", "info")

    call_kwargs = mock_client.post.call_args
    assert call_kwargs[0][0] == wh["url"]
    body = call_kwargs[1]["json"]
    assert body["embeds"][0]["color"] == 3066993


@pytest.mark.asyncio
async def test_notify_discord_warning_color():
    config = _make_config("discord")
    notifier = WebhookNotifier(config)

    with patch("tinyagentos.webhook_notifier.httpx.AsyncClient") as mock_client_cls:
        mock_client = AsyncMock()
        mock_client_cls.return_value.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client_cls.return_value.__aexit__ = AsyncMock(return_value=False)

        await notifier.notify("T", "M", "warning")

    body = mock_client.post.call_args[1]["json"]
    assert body["embeds"][0]["color"] == 16776960


@pytest.mark.asyncio
async def test_notify_discord_error_color():
    config = _make_config("discord")
    notifier = WebhookNotifier(config)

    with patch("tinyagentos.webhook_notifier.httpx.AsyncClient") as mock_client_cls:
        mock_client = AsyncMock()
        mock_client_cls.return_value.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client_cls.return_value.__aexit__ = AsyncMock(return_value=False)

        await notifier.notify("T", "M", "error")

    body = mock_client.post.call_args[1]["json"]
    assert body["embeds"][0]["color"] == 15158332


@pytest.mark.asyncio
async def test_notify_discord_unknown_level_defaults_to_info_color():
    config = _make_config("discord")
    notifier = WebhookNotifier(config)

    with patch("tinyagentos.webhook_notifier.httpx.AsyncClient") as mock_client_cls:
        mock_client = AsyncMock()
        mock_client_cls.return_value.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client_cls.return_value.__aexit__ = AsyncMock(return_value=False)

        await notifier.notify("T", "M", "critical")

    body = mock_client.post.call_args[1]["json"]
    assert body["embeds"][0]["color"] == 3066993


# --- notify: telegram webhook ---

@pytest.mark.asyncio
async def test_notify_telegram_posts_to_correct_url():
    config = _make_config("telegram")
    notifier = WebhookNotifier(config)
    wh = config["webhooks"][0]

    with patch("tinyagentos.webhook_notifier.httpx.AsyncClient") as mock_client_cls:
        mock_client = AsyncMock()
        mock_client_cls.return_value.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client_cls.return_value.__aexit__ = AsyncMock(return_value=False)

        await notifier.notify("T", "M", "info")

    expected_url = (
        f"https://api.telegram.org/bot{wh['bot_token']}/sendMessage"
    )
    call_kwargs = mock_client.post.call_args
    assert call_kwargs[0][0] == expected_url
    body = call_kwargs[1]["json"]
    assert body["chat_id"] == wh["chat_id"]
    assert body["text"] == "*T*\nM"
    assert body["parse_mode"] == "Markdown"


# --- notify: multiple webhooks ---

@pytest.mark.asyncio
async def test_notify_sends_to_all_configured_webhooks():
    config = _make_config("generic", "slack", "discord")
    notifier = WebhookNotifier(config)

    with patch("tinyagentos.webhook_notifier.httpx.AsyncClient") as mock_client_cls:
        mock_client = AsyncMock()
        mock_client_cls.return_value.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client_cls.return_value.__aexit__ = AsyncMock(return_value=False)

        await notifier.notify("T", "M", "info")

    assert mock_client.post.call_count == 3


# --- notify: transport error is swallowed ---

@pytest.mark.asyncio
async def test_notify_swallows_transport_error():
    config = _make_config("generic")
    notifier = WebhookNotifier(config)

    with patch("tinyagentos.webhook_notifier.httpx.AsyncClient") as mock_client_cls:
        mock_client = AsyncMock()
        mock_client.post.side_effect = httpx.TransportError("connection refused")
        mock_client_cls.return_value.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client_cls.return_value.__aexit__ = AsyncMock(return_value=False)

        # Must not raise
        await notifier.notify("T", "M", "info")


@pytest.mark.asyncio
async def test_notify_swallows_error_and_continues_to_next_webhook():
    config = _make_config("generic", "generic")
    notifier = WebhookNotifier(config)

    with patch("tinyagentos.webhook_notifier.httpx.AsyncClient") as mock_client_cls:
        mock_client = AsyncMock()
        mock_client.post.side_effect = [
            httpx.TransportError("fail"),
            None,
        ]
        mock_client_cls.return_value.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client_cls.return_value.__aexit__ = AsyncMock(return_value=False)

        await notifier.notify("T", "M", "info")

    assert mock_client.post.call_count == 2


# --- _send: default type is generic ---

@pytest.mark.asyncio
async def test_send_without_type_defaults_to_generic():
    notifier = WebhookNotifier({"webhooks": []})
    webhook = {"url": "https://example.com/hook"}

    with patch("tinyagentos.webhook_notifier.httpx.AsyncClient") as mock_client_cls:
        mock_client = AsyncMock()
        mock_client_cls.return_value.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client_cls.return_value.__aexit__ = AsyncMock(return_value=False)

        await notifier._send(webhook, "T", "M", "info")

    mock_client.post.assert_called_once_with(
        "https://example.com/hook",
        json={"title": "T", "message": "M", "level": "info"},
    )


# --- _send: httpx.AsyncClient timeout ---

@pytest.mark.asyncio
async def test_send_creates_client_with_timeout():
    notifier = WebhookNotifier({"webhooks": []})
    webhook = {"url": "https://example.com/hook"}

    with patch("tinyagentos.webhook_notifier.httpx.AsyncClient") as mock_client_cls:
        mock_client = AsyncMock()
        mock_client_cls.return_value.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client_cls.return_value.__aexit__ = AsyncMock(return_value=False)

        await notifier._send(webhook, "T", "M", "info")

    mock_client_cls.assert_called_once_with(timeout=10)
