"""Mail app API routes (Phase 1).

Multi-account email client: account CRUD, IMAP folder + message listing,
single-message fetch, and SMTP send. Account passwords go through the
SecretsStore (this layer never persists plaintext credentials).

Per-user scoping: every account row is keyed by the authenticated user_id and
every handler refuses accounts that belong to a different user.

Deferred to Phase 2 (see TODOs): the agent-account send-as profile switcher,
OAuth (Gmail/Outlook), the full Share sheet (task #69), and push/IDLE new-mail.
"""

from __future__ import annotations

from dataclasses import asdict

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from tinyagentos import mail_client
from tinyagentos.auth_context import CurrentUser, current_user
from tinyagentos.mail_client import MailAccountConfig
from tinyagentos.mail_store import MailAccountStore

router = APIRouter()


class AccountCreate(BaseModel):
    display_name: str = ""
    email_address: str
    imap_host: str
    imap_port: int = 993
    imap_security: str = "ssl"
    smtp_host: str
    smtp_port: int = 587
    smtp_security: str = "starttls"
    username: str
    password: str


class SendBody(BaseModel):
    to: str
    subject: str = ""
    body: str = ""
    cc: str = ""


def _public_account(account: dict) -> dict:
    """Strip the internal secret pointer from an account before returning it."""
    return {k: v for k, v in account.items() if k not in ("secret_name", "user_id")}


async def _resolve_config(
    request: Request, account: dict
) -> MailAccountConfig | None:
    """Load the account password from the SecretsStore and build a connection
    config. Returns None when the secret is missing (account misconfigured)."""
    secrets = request.app.state.secrets
    rec = await secrets.get(account["secret_name"])
    if not rec or not rec.get("value"):
        return None
    return MailAccountConfig(
        imap_host=account["imap_host"],
        imap_port=account["imap_port"],
        imap_security=account["imap_security"],
        smtp_host=account["smtp_host"],
        smtp_port=account["smtp_port"],
        smtp_security=account["smtp_security"],
        username=account["username"],
        password=rec["value"],
        email_address=account["email_address"],
    )


@router.get("/api/mail/accounts")
async def list_accounts(request: Request, user: CurrentUser = Depends(current_user)):
    store: MailAccountStore = request.app.state.mail_store
    accounts = await store.list_for_user(user.user_id)
    return [_public_account(a) for a in accounts]


@router.post("/api/mail/accounts")
async def add_account(
    request: Request,
    body: AccountCreate,
    user: CurrentUser = Depends(current_user),
):
    store: MailAccountStore = request.app.state.mail_store
    secrets = request.app.state.secrets

    # Insert metadata first so we have the account id to key the secret on, then
    # store the password in the SecretsStore. The account row only ever holds
    # the secret name, never the plaintext password.
    account = await store.add(
        user_id=user.user_id,
        display_name=body.display_name or body.email_address,
        email_address=body.email_address,
        imap_host=body.imap_host,
        imap_port=body.imap_port,
        imap_security=body.imap_security,
        smtp_host=body.smtp_host,
        smtp_port=body.smtp_port,
        smtp_security=body.smtp_security,
        username=body.username,
        secret_name=MailAccountStore.secret_name_for("pending"),
    )
    secret_name = MailAccountStore.secret_name_for(account["id"])
    # If storing the secret or re-pointing the row fails, roll back the account
    # row instead of leaving it pointing at a non-existent secret (which would
    # make every later op return "account credential missing" with no way to fix
    # it but delete and re-add).
    try:
        await secrets.add(
            name=secret_name,
            value=body.password,
            category="credentials",
            description=f"Mail account password for {body.email_address}",
        )
        await store._db.execute(  # noqa: SLF001 -- internal store connection
            "UPDATE mail_accounts SET secret_name = ? WHERE id = ? AND user_id = ?",
            (secret_name, account["id"], user.user_id),
        )
        await store._db.commit()  # noqa: SLF001
    except Exception:
        await store.delete(account["id"], user.user_id)
        try:
            await secrets.delete(secret_name)
        except Exception:
            pass
        raise
    account["secret_name"] = secret_name
    return JSONResponse(_public_account(account), status_code=201)


@router.delete("/api/mail/accounts/{account_id}")
async def delete_account(
    request: Request,
    account_id: str,
    user: CurrentUser = Depends(current_user),
):
    store: MailAccountStore = request.app.state.mail_store
    secrets = request.app.state.secrets
    account = await store.get(account_id, user.user_id)
    if not account:
        return JSONResponse({"error": "account not found"}, status_code=404)
    await secrets.delete(account["secret_name"])
    await store.delete(account_id, user.user_id)
    return {"status": "deleted"}


@router.get("/api/mail/accounts/{account_id}/folders")
async def list_folders(
    request: Request,
    account_id: str,
    user: CurrentUser = Depends(current_user),
):
    store: MailAccountStore = request.app.state.mail_store
    account = await store.get(account_id, user.user_id)
    if not account:
        return JSONResponse({"error": "account not found"}, status_code=404)
    cfg = await _resolve_config(request, account)
    if cfg is None:
        return JSONResponse({"error": "account credential missing"}, status_code=400)
    try:
        folders = await mail_client.list_folders(cfg)
    except Exception as exc:
        return JSONResponse({"error": f"imap error: {exc}"}, status_code=502)
    return {"folders": folders}


@router.get("/api/mail/accounts/{account_id}/messages")
async def list_messages(
    request: Request,
    account_id: str,
    folder: str = "INBOX",
    limit: int = 50,
    user: CurrentUser = Depends(current_user),
):
    store: MailAccountStore = request.app.state.mail_store
    account = await store.get(account_id, user.user_id)
    if not account:
        return JSONResponse({"error": "account not found"}, status_code=404)
    cfg = await _resolve_config(request, account)
    if cfg is None:
        return JSONResponse({"error": "account credential missing"}, status_code=400)
    try:
        envelopes = await mail_client.list_messages(cfg, folder, limit=limit)
    except mail_client.MailFolderError as exc:
        return JSONResponse({"error": str(exc)}, status_code=400)
    except Exception as exc:
        return JSONResponse({"error": f"imap error: {exc}"}, status_code=502)
    return {"messages": [asdict(e) for e in envelopes]}


@router.get("/api/mail/accounts/{account_id}/messages/{uid}")
async def get_message(
    request: Request,
    account_id: str,
    uid: str,
    folder: str = "INBOX",
    user: CurrentUser = Depends(current_user),
):
    store: MailAccountStore = request.app.state.mail_store
    account = await store.get(account_id, user.user_id)
    if not account:
        return JSONResponse({"error": "account not found"}, status_code=404)
    cfg = await _resolve_config(request, account)
    if cfg is None:
        return JSONResponse({"error": "account credential missing"}, status_code=400)
    try:
        detail = await mail_client.get_message(cfg, folder, uid)
    except mail_client.MailValidationError as exc:
        return JSONResponse({"error": str(exc)}, status_code=400)
    except Exception as exc:
        return JSONResponse({"error": f"imap error: {exc}"}, status_code=502)
    if detail is None:
        return JSONResponse({"error": "message not found"}, status_code=404)
    return asdict(detail)


@router.post("/api/mail/accounts/{account_id}/send")
async def send(
    request: Request,
    account_id: str,
    body: SendBody,
    user: CurrentUser = Depends(current_user),
):
    # TODO(phase-2): agent send-as -- allow an agent account to send on behalf
    # of the user once the consent/relationship layer lands.
    store: MailAccountStore = request.app.state.mail_store
    account = await store.get(account_id, user.user_id)
    if not account:
        return JSONResponse({"error": "account not found"}, status_code=404)
    cfg = await _resolve_config(request, account)
    if cfg is None:
        return JSONResponse({"error": "account credential missing"}, status_code=400)
    try:
        # TODO(phase-2): attachment upload pass-through (multipart) -- the
        # client supports it; the route only sends a text body for now.
        await mail_client.send_message(cfg, body.to, body.subject, body.body, cc=body.cc)
    except mail_client.MailValidationError as exc:
        return JSONResponse({"error": str(exc)}, status_code=400)
    except Exception as exc:
        return JSONResponse({"error": f"smtp error: {exc}"}, status_code=502)
    return {"status": "sent"}
