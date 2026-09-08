"""Endpoint tests for tinyagentos/routes/mail.py.

This file provides comprehensive tests for all the mail route endpoints.
"""

from __future__ import annotations

import uuid
from unittest.mock import patch

import pytest
import pytest_asyncio

from tinyagentos.mail_client import MailAccountConfig, MailFolderError, MailValidationError, MessageDetail, MessageEnvelope
from tinyagentos.mail_store import MailAccountStore


@pytest_asyncio.fixture
async def _init_mail_store(client, tmp_data_dir):
    app = client._transport.app
    store = MailAccountStore(tmp_data_dir / "mail.db")
    await store.init()
    app.state.mail_store = store
    yield store
    await store.close()


def _post_account(client, **overrides):
    kwargs = dict(
        display_name="Jay",
        email_address="jay@example.com",
        imap_host="imap.example.com",
        imap_port=993,
        imap_security="ssl",
        smtp_host="smtp.example.com",
        smtp_port=587,
        smtp_security="starttls",
        username="jay@example.com",
        password="testpass",
    )
    kwargs.update(overrides)
    return client.post("/api/mail/accounts", json=kwargs)


@pytest.mark.asyncio
async def test_list_accounts_empty(client, _init_mail_store):
    resp = await client.get("/api/mail/accounts")
    assert resp.status_code == 200
    assert resp.json() == []


@pytest.mark.asyncio
async def test_add_account_returns_201(client, _init_mail_store):
    body = {
        "display_name": "Jay",
        "email_address": "jay@example.com",
        "imap_host": "imap.example.com",
        "imap_port": 993,
        "imap_security": "ssl",
        "smtp_host": "smtp.example.com",
        "smtp_port": 587,
        "smtp_security": "starttls",
        "username": "jay@example.com",
        "password": "testpass",
    }
    resp = await client.post("/api/mail/accounts", json=body)
    assert resp.status_code == 201
    data = resp.json()
    assert data["email_address"] == "jay@example.com"
    assert "secret_name" not in data
    assert "user_id" not in data
    assert "id" in data


@pytest.mark.asyncio
async def test_list_accounts_returns_added(client, _init_mail_store):
    await _post_account(client)
    resp = await client.get("/api/mail/accounts")
    assert resp.status_code == 200
    accounts = resp.json()
    assert len(accounts) == 1
    assert accounts[0]["email_address"] == "jay@example.com"


@pytest.mark.asyncio
async def test_delete_account_not_found(client, _init_mail_store):
    resp = await client.delete("/api/mail/accounts/nonexistent")
    assert resp.status_code == 404
    assert resp.json() == {"error": "account not found"}


@pytest.mark.asyncio
async def test_delete_account_success(client, _init_mail_store):
    resp = await _post_account(client)
    account_id = resp.json()["id"]
    resp = await client.delete(f"/api/mail/accounts/{account_id}")
    assert resp.status_code == 200
    assert resp.json() == {"status": "deleted"}
    resp = await client.get("/api/mail/accounts")
    assert resp.status_code == 200
    assert resp.json() == []


@pytest.mark.asyncio
async def test_list_folders_success(client, _init_mail_store):
    resp = await _post_account(client)
    account_id = resp.json()["id"]
    with patch("tinyagentos.routes.mail.mail_client.list_folders", return_value=["INBOX", "Sent"]):
        resp = await client.get(f"/api/mail/accounts/{account_id}/folders")
    assert resp.status_code == 200
    assert resp.json() == {"folders": ["INBOX", "Sent"]}


@pytest.mark.asyncio
async def test_list_folders_account_not_found(client, _init_mail_store):
    resp = await client.get("/api/mail/accounts/nonexistent/folders")
    assert resp.status_code == 404
    assert resp.json() == {"error": "account not found"}


@pytest.mark.asyncio
async def test_list_folders_credential_missing(client, _init_mail_store):
    resp = await _post_account(client)
    account_id = resp.json()["id"]
    app = client._transport.app
    await app.state.secrets.delete(MailAccountStore.secret_name_for(account_id))
    resp = await client.get(f"/api/mail/accounts/{account_id}/folders")
    assert resp.status_code == 400
    assert resp.json() == {"error": "account credential missing"}


@pytest.mark.asyncio
async def test_list_folders_imap_error(client, _init_mail_store):
    resp = await _post_account(client)
    account_id = resp.json()["id"]
    with patch("tinyagentos.routes.mail.mail_client.list_folders", side_effect=OSError("imap down")):
        resp = await client.get(f"/api/mail/accounts/{account_id}/folders")
    assert resp.status_code == 502
    assert "imap error" in resp.json()["error"]


@pytest.mark.asyncio
async def test_list_messages_success(client, _init_mail_store):
    resp = await _post_account(client)
    account_id = resp.json()["id"]
    envelope = MessageEnvelope(
        uid="1",
        from_name="Dhaval Patel",
        from_addr="dhaval@example.com",
        to="jay@example.com",
        subject="Hello",
        date="Mon, 15 Jun 2026 09:24:00 +0000",
        snippet="Thanks",
        unread=True,
        flagged=False,
        has_attachment=False,
    )
    with patch("tinyagentos.routes.mail.mail_client.list_messages", return_value=[envelope]):
        resp = await client.get(f"/api/mail/accounts/{account_id}/messages")
    assert resp.status_code == 200
    data = resp.json()
    assert "messages" in data
    assert len(data["messages"]) == 1
    assert data["messages"][0]["uid"] == "1"


@pytest.mark.asyncio
async def test_list_messages_account_not_found(client, _init_mail_store):
    resp = await client.get("/api/mail/accounts/nonexistent/messages")
    assert resp.status_code == 404
    assert resp.json() == {"error": "account not found"}


@pytest.mark.asyncio
async def test_list_messages_mail_folder_error(client, _init_mail_store):
    resp = await _post_account(client)
    account_id = resp.json()["id"]
    with patch("tinyagentos.routes.mail.mail_client.list_messages", side_effect=MailFolderError("bad folder")):
        resp = await client.get(f"/api/mail/accounts/{account_id}/messages")
    assert resp.status_code == 400
    assert "bad folder" in resp.json()["error"]


@pytest.mark.asyncio
async def test_list_messages_imap_error(client, _init_mail_store):
    resp = await _post_account(client)
    account_id = resp.json()["id"]
    with patch("tinyagentos.routes.mail.mail_client.list_messages", side_effect=OSError("imap error")):
        resp = await client.get(f"/api/mail/accounts/{account_id}/messages")
    assert resp.status_code == 502
    assert "imap error" in resp.json()["error"]


@pytest.mark.asyncio
async def test_list_messages_validation_error(client, _init_mail_store):
    resp = await _post_account(client)
    account_id = resp.json()["id"]
    with patch("tinyagentos.routes.mail.mail_client.list_messages", side_effect=MailFolderError("invalid folder")):
        resp = await client.get(f"/api/mail/accounts/{account_id}/messages?folder=badfolder")
    assert resp.status_code == 400
    assert "invalid folder" in resp.json()["error"]


@pytest.mark.asyncio
async def test_get_message_success(client, _init_mail_store):
    resp = await _post_account(client)
    account_id = resp.json()["id"]
    detail = MessageDetail(
        uid="1",
        from_name="Dhaval Patel",
        from_addr="dhaval@example.com",
        to="jay@example.com",
        cc="",
        subject="Hello",
        date="Mon, 15 Jun 2026 09:24:00 +0000",
        body_text="Thanks",
        body_html="",
        attachments=[],
    )
    with patch("tinyagentos.routes.mail.mail_client.get_message", return_value=detail):
        resp = await client.get(f"/api/mail/accounts/{account_id}/messages/1")
    assert resp.status_code == 200
    data = resp.json()
    assert data["uid"] == "1"
    assert data["subject"] == "Hello"


@pytest.mark.asyncio
async def test_get_message_not_found(client, _init_mail_store):
    resp = await _post_account(client)
    account_id = resp.json()["id"]
    with patch("tinyagentos.routes.mail.mail_client.get_message", return_value=None):
        resp = await client.get(f"/api/mail/accounts/{account_id}/messages/1")
    assert resp.status_code == 404
    assert resp.json() == {"error": "message not found"}


@pytest.mark.asyncio
async def test_get_message_account_not_found(client, _init_mail_store):
    resp = await client.get("/api/mail/accounts/nonexistent/messages/1")
    assert resp.status_code == 404
    assert resp.json() == {"error": "account not found"}


@pytest.mark.asyncio
async def test_get_message_validation_error(client, _init_mail_store):
    resp = await _post_account(client)
    account_id = resp.json()["id"]
    with patch("tinyagentos.routes.mail.mail_client.get_message", side_effect=MailValidationError("invalid uid")):
        resp = await client.get(f"/api/mail/accounts/{account_id}/messages/1")
    assert resp.status_code == 400
    assert "invalid uid" in resp.json()["error"]


@pytest.mark.asyncio
async def test_get_message_imap_error(client, _init_mail_store):
    resp = await _post_account(client)
    account_id = resp.json()["id"]
    with patch("tinyagentos.routes.mail.mail_client.get_message", side_effect=OSError("imap error")):
        resp = await client.get(f"/api/mail/accounts/{account_id}/messages/1")
    assert resp.status_code == 502
    assert "imap error" in resp.json()["error"]


@pytest.mark.asyncio
async def test_send_message_success(client, _init_mail_store):
    resp = await _post_account(client)
    account_id = resp.json()["id"]
    with patch("tinyagentos.routes.mail.mail_client.send_message") as mock_send:
        resp = await client.post(
            f"/api/mail/accounts/{account_id}/send",
            json={"to": "recipient@example.com", "subject": "Hi", "body": "Hello"},
        )
    assert resp.status_code == 200
    assert resp.json() == {"status": "sent"}
    mock_send.assert_called_once()


@pytest.mark.asyncio
async def test_send_message_account_not_found(client, _init_mail_store):
    resp = await client.post(
        "/api/mail/accounts/nonexistent/send",
        json={"to": "recipient@example.com", "subject": "Hi", "body": "Hello"},
    )
    assert resp.status_code == 404
    assert resp.json() == {"error": "account not found"}


@pytest.mark.asyncio
async def test_send_message_validation_error(client, _init_mail_store):
    resp = await _post_account(client)
    account_id = resp.json()["id"]
    with patch("tinyagentos.routes.mail.mail_client.send_message", side_effect=MailValidationError("bad header")):
        resp = await client.post(
            f"/api/mail/accounts/{account_id}/send",
            json={"to": "recipient@example.com", "subject": "Hi", "body": "Hello"},
        )
    assert resp.status_code == 400
    assert "bad header" in resp.json()["error"]


@pytest.mark.asyncio
async def test_send_message_imap_error(client, _init_mail_store):
    resp = await _post_account(client)
    account_id = resp.json()["id"]
    with patch("tinyagentos.routes.mail.mail_client.send_message", side_effect=OSError("smtp error")):
        resp = await client.post(
            f"/api/mail/accounts/{account_id}/send",
            json={"to": "recipient@example.com", "subject": "Hi", "body": "Hello"},
        )
    assert resp.status_code == 502
    assert "smtp error" in resp.json()["error"]