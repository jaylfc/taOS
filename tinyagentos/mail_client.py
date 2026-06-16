"""IMAP/SMTP client for the Mail app.

Mirrors the stdlib imaplib/smtplib approach already used by
``tinyagentos/channel_hub/email_connector.py`` (no new third-party dependency),
but exposes a richer, request-scoped API (list folders, list a folder's
envelopes, fetch one message body + attachments, send a composed message).

All blocking socket IO runs through ``asyncio.to_thread`` so it never blocks
the event loop.
"""

from __future__ import annotations

import asyncio
import email
import imaplib
import re
import smtplib
from dataclasses import dataclass, field
from email.header import decode_header, make_header
from email.message import Message
from email.mime.base import MIMEBase
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email.utils import parseaddr

# Common IMAP special-use folder names mapped to our canonical folder ids.
# Servers vary, so the route layer also passes the raw IMAP folder name through.
CANONICAL_FOLDERS = ["INBOX", "Sent", "Drafts", "Archive", "Trash"]

# Upper bound on how many envelopes a single list call will fetch. Each id is a
# separate IMAP round trip, so an unbounded limit lets one request tie up the
# connection (and the to_thread worker) indefinitely.
MAX_MESSAGE_LIMIT = 200


class MailValidationError(ValueError):
    """Raised when an untrusted value is unsafe to use in a mail command."""


class MailFolderError(MailValidationError):
    """Raised when a folder name is unsafe to interpolate into an IMAP command."""


def _validate_folder(folder: str) -> str:
    """Reject folder names that could break out of the quoted IMAP argument.

    ``folder`` is an untrusted query-string value interpolated into
    ``conn.select(f'"{folder}"')``. A double-quote or CR/LF would let a caller
    inject extra IMAP protocol tokens on their own authenticated connection, so
    we forbid those characters outright rather than try to escape them."""
    if not folder or any(c in folder for c in ('"', "\r", "\n", "\x00")):
        raise MailFolderError(f"invalid folder name: {folder!r}")
    return folder


_UID_RE = re.compile(r"^[0-9]+$")


def _validate_uid(uid: str) -> str:
    """Reject message ids that are not a plain IMAP UID.

    ``uid`` is an untrusted path parameter passed straight to
    ``conn.uid("FETCH", uid, ...)``. A non-numeric value could carry extra IMAP
    command tokens, so we require a bare numeric UID. The ids we hand out from
    ``list_messages`` are always numeric UIDs, so this rejects nothing legitimate."""
    if not uid or not _UID_RE.fullmatch(uid):
        raise MailValidationError(f"invalid message id: {uid!r}")
    return uid


def _validate_header(value: str, field: str) -> str:
    """Reject CR/LF/NUL in a value destined for a MIME header.

    ``to``/``cc``/``subject`` come from the client and are assigned directly to
    message headers. A newline would let a caller inject extra headers (a hidden
    Bcc, a spoofed From), so we forbid the line-break characters outright."""
    if any(c in value for c in ("\r", "\n", "\x00")):
        raise MailValidationError(f"invalid characters in {field}")
    return value


@dataclass
class MailAccountConfig:
    """Connection details for a single account. The password is resolved from
    the SecretsStore by the caller and passed in here; it is never persisted by
    this module."""

    imap_host: str
    imap_port: int
    imap_security: str
    smtp_host: str
    smtp_port: int
    smtp_security: str
    username: str
    password: str
    email_address: str


@dataclass
class MessageEnvelope:
    uid: str
    from_name: str
    from_addr: str
    to: str
    subject: str
    date: str
    snippet: str
    unread: bool
    flagged: bool
    has_attachment: bool


@dataclass
class Attachment:
    filename: str
    content_type: str
    size: int


@dataclass
class MessageDetail:
    uid: str
    from_name: str
    from_addr: str
    to: str
    cc: str
    subject: str
    date: str
    body_text: str
    body_html: str
    attachments: list[Attachment] = field(default_factory=list)


def _decode(value: str | None) -> str:
    """Decode an RFC 2047 encoded header into a plain unicode string."""
    if not value:
        return ""
    try:
        return str(make_header(decode_header(value)))
    except Exception:
        return value


def parse_envelope(uid: str, msg: Message, flags: bytes | str = b"") -> MessageEnvelope:
    """Parse an ``email.message.Message`` (header-only fetch is enough) plus the
    IMAP FLAGS blob into a MessageEnvelope. Pure and synchronous so it can be
    unit-tested without a live server."""
    flags_str = flags.decode("utf-8", "replace") if isinstance(flags, bytes) else (flags or "")
    raw_from = _decode(msg.get("From", ""))
    from_name, from_addr = parseaddr(raw_from)
    from_name = from_name or from_addr

    snippet = ""
    has_attachment = False
    if msg.is_multipart():
        for part in msg.walk():
            disp = str(part.get("Content-Disposition", ""))
            if "attachment" in disp.lower() or part.get_filename():
                has_attachment = True
            if not snippet and part.get_content_type() == "text/plain":
                snippet = _payload_text(part)
    else:
        snippet = _payload_text(msg)

    snippet = " ".join(snippet.split())[:200]

    return MessageEnvelope(
        uid=uid,
        from_name=from_name,
        from_addr=from_addr,
        to=_decode(msg.get("To", "")),
        subject=_decode(msg.get("Subject", "")),
        date=_decode(msg.get("Date", "")),
        snippet=snippet,
        unread="\\Seen" not in flags_str,
        flagged="\\Flagged" in flags_str,
        has_attachment=has_attachment,
    )


def parse_detail(uid: str, msg: Message) -> MessageDetail:
    """Parse a full RFC822 message into a MessageDetail (body + attachments)."""
    body_text = ""
    body_html = ""
    attachments: list[Attachment] = []

    if msg.is_multipart():
        for part in msg.walk():
            ctype = part.get_content_type()
            disp = str(part.get("Content-Disposition", "")).lower()
            filename = part.get_filename()
            if filename or "attachment" in disp:
                payload = part.get_payload(decode=True) or b""
                attachments.append(
                    Attachment(
                        filename=_decode(filename) or "attachment",
                        content_type=ctype,
                        size=len(payload),
                    )
                )
            elif ctype == "text/plain" and not body_text:
                body_text = _payload_text(part)
            elif ctype == "text/html" and not body_html:
                body_html = _payload_text(part)
    else:
        if msg.get_content_type() == "text/html":
            body_html = _payload_text(msg)
        else:
            body_text = _payload_text(msg)

    raw_from = _decode(msg.get("From", ""))
    from_name, from_addr = parseaddr(raw_from)
    return MessageDetail(
        uid=uid,
        from_name=from_name or from_addr,
        from_addr=from_addr,
        to=_decode(msg.get("To", "")),
        cc=_decode(msg.get("Cc", "")),
        subject=_decode(msg.get("Subject", "")),
        date=_decode(msg.get("Date", "")),
        body_text=body_text,
        body_html=body_html,
        attachments=attachments,
    )


def _payload_text(part: Message) -> str:
    payload = part.get_payload(decode=True)
    if payload is None:
        return ""
    charset = part.get_content_charset() or "utf-8"
    try:
        return payload.decode(charset, errors="replace")
    except (LookupError, UnicodeDecodeError):
        return payload.decode("utf-8", errors="replace")


# --------------------------------------------------------------------------- #
# Blocking IMAP/SMTP primitives (run via asyncio.to_thread).
# --------------------------------------------------------------------------- #


def _imap_connect(cfg: MailAccountConfig) -> imaplib.IMAP4:
    if cfg.imap_security == "ssl":
        conn: imaplib.IMAP4 = imaplib.IMAP4_SSL(cfg.imap_host, cfg.imap_port)
    else:
        conn = imaplib.IMAP4(cfg.imap_host, cfg.imap_port)
        if cfg.imap_security == "starttls":
            conn.starttls()
    conn.login(cfg.username, cfg.password)
    return conn


def _list_folders_blocking(cfg: MailAccountConfig) -> list[str]:
    conn = _imap_connect(cfg)
    try:
        typ, data = conn.list()
        folders: list[str] = []
        if typ == "OK":
            for raw in data:
                if not raw:
                    continue
                line = raw.decode("utf-8", "replace") if isinstance(raw, bytes) else str(raw)
                # Format: (\HasNoChildren) "/" "INBOX"  -- take the quoted tail.
                if '"' in line:
                    folders.append(line.split('"')[-2])
                else:
                    folders.append(line.split()[-1])
        return folders
    finally:
        _safe_logout(conn)


def _list_messages_blocking(
    cfg: MailAccountConfig, folder: str, limit: int
) -> list[MessageEnvelope]:
    _validate_folder(folder)
    limit = max(1, min(limit, MAX_MESSAGE_LIMIT))
    conn = _imap_connect(cfg)
    try:
        conn.select(f'"{folder}"', readonly=True)
        # UID SEARCH/FETCH so the identifier surfaced to the client is a stable
        # IMAP UID, not a sequence number (which the server renumbers when
        # messages are expunged, breaking a later open/delete by that id).
        typ, data = conn.uid("SEARCH", None, "ALL")
        if typ != "OK" or not data or not data[0]:
            return []
        ids = data[0].split()
        ids = ids[-limit:][::-1]  # newest first
        envelopes: list[MessageEnvelope] = []
        for num in ids:
            typ, msg_data = conn.uid(
                "FETCH", num, "(FLAGS BODY.PEEK[HEADER] BODY.PEEK[TEXT]<0.4096>)"
            )
            if typ != "OK" or not msg_data:
                continue
            raw_bytes = b""
            flags = b""
            for item in msg_data:
                if isinstance(item, tuple) and len(item) == 2:
                    raw_bytes += item[1]
                    flags += item[0] if isinstance(item[0], bytes) else b""
            msg = email.message_from_bytes(raw_bytes)
            uid = num.decode() if isinstance(num, bytes) else str(num)
            envelopes.append(parse_envelope(uid, msg, flags))
        return envelopes
    finally:
        _safe_logout(conn)


def _get_message_blocking(
    cfg: MailAccountConfig, folder: str, uid: str
) -> MessageDetail | None:
    _validate_folder(folder)
    _validate_uid(uid)
    conn = _imap_connect(cfg)
    try:
        conn.select(f'"{folder}"', readonly=True)
        # Fetch by UID to match the stable id handed out by list_messages.
        typ, msg_data = conn.uid("FETCH", uid, "(RFC822)")
        if typ != "OK" or not msg_data or not msg_data[0]:
            return None
        raw_email = msg_data[0][1]
        msg = email.message_from_bytes(raw_email)
        return parse_detail(uid, msg)
    finally:
        _safe_logout(conn)


def _build_outgoing(
    cfg: MailAccountConfig,
    to: str,
    subject: str,
    body: str,
    cc: str = "",
    attachments: list[tuple[str, bytes, str]] | None = None,
) -> MIMEMultipart:
    msg = MIMEMultipart()
    msg["From"] = cfg.email_address or cfg.username
    msg["To"] = _validate_header(to, "to")
    if cc:
        msg["Cc"] = _validate_header(cc, "cc")
    msg["Subject"] = _validate_header(subject, "subject")
    msg.attach(MIMEText(body, "plain"))
    for filename, content, content_type in attachments or []:
        maintype, _, subtype = content_type.partition("/")
        part = MIMEBase(maintype or "application", subtype or "octet-stream")
        part.set_payload(content)
        from email.encoders import encode_base64

        encode_base64(part)
        part.add_header("Content-Disposition", "attachment", filename=filename)
        msg.attach(part)
    return msg


def _send_blocking(
    cfg: MailAccountConfig,
    to: str,
    subject: str,
    body: str,
    cc: str = "",
    attachments: list[tuple[str, bytes, str]] | None = None,
) -> None:
    msg = _build_outgoing(cfg, to, subject, body, cc=cc, attachments=attachments)
    recipients = [r.strip() for r in (to + ("," + cc if cc else "")).split(",") if r.strip()]
    if cfg.smtp_security == "ssl":
        server: smtplib.SMTP = smtplib.SMTP_SSL(cfg.smtp_host, cfg.smtp_port)
    else:
        server = smtplib.SMTP(cfg.smtp_host, cfg.smtp_port)
        if cfg.smtp_security == "starttls":
            server.starttls()
    try:
        server.login(cfg.username, cfg.password)
        server.sendmail(cfg.email_address or cfg.username, recipients, msg.as_string())
    finally:
        try:
            server.quit()
        except Exception:
            pass


def _safe_logout(conn: imaplib.IMAP4) -> None:
    try:
        conn.logout()
    except Exception:
        pass


# --------------------------------------------------------------------------- #
# Async wrappers.
# --------------------------------------------------------------------------- #


async def list_folders(cfg: MailAccountConfig) -> list[str]:
    return await asyncio.to_thread(_list_folders_blocking, cfg)


async def list_messages(
    cfg: MailAccountConfig, folder: str, limit: int = 50
) -> list[MessageEnvelope]:
    return await asyncio.to_thread(_list_messages_blocking, cfg, folder, limit)


async def get_message(
    cfg: MailAccountConfig, folder: str, uid: str
) -> MessageDetail | None:
    return await asyncio.to_thread(_get_message_blocking, cfg, folder, uid)


async def send_message(
    cfg: MailAccountConfig,
    to: str,
    subject: str,
    body: str,
    cc: str = "",
    attachments: list[tuple[str, bytes, str]] | None = None,
) -> None:
    await asyncio.to_thread(
        _send_blocking, cfg, to, subject, body, cc, attachments
    )
