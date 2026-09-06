from __future__ import annotations
import asyncio
import email
import imaplib
import logging
import smtplib
import time
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from tinyagentos.channel_hub.message import IncomingMessage, OutgoingMessage

logger = logging.getLogger(__name__)


class EmailConnector:
    def __init__(self, agent_name: str, router, imap_host: str, imap_port: int,
                 smtp_host: str, smtp_port: int, username: str, password: str):
        self.agent_name = agent_name
        self.router = router
        self.imap_host = imap_host
        self.imap_port = imap_port
        self.smtp_host = smtp_host
        self.smtp_port = smtp_port
        self.username = username
        self.password = password
        self._running = False
        self._task = None

    async def start(self):
        self._running = True
        self._task = asyncio.create_task(self._poll_loop())
        logger.info(f"Email connector started for agent '{self.agent_name}'")

    async def stop(self):
        self._running = False
        if self._task:
            self._task.cancel()

    async def _poll_loop(self):
        while self._running:
            try:
                await asyncio.get_event_loop().run_in_executor(None, self._check_inbox)
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Email poll error: {e}")
            await asyncio.sleep(30)

    def _check_inbox(self):
        try:
            mail = imaplib.IMAP4_SSL(self.imap_host, self.imap_port)
            mail.login(self.username, self.password)
            mail.select("INBOX")
            _, data = mail.search(None, "UNSEEN")
            for num in data[0].split():
                _, msg_data = mail.fetch(num, "(RFC822)")
                raw_email = msg_data[0][1]
                msg = email.message_from_bytes(raw_email)
                self._process_email(msg)
                mail.store(num, "+FLAGS", "\\Seen")
            mail.close()
            mail.logout()
        except Exception as e:
            logger.error(f"IMAP error: {e}")

    def _process_email(self, msg):
        from_addr = msg.get("From", "")
        subject = msg.get("Subject", "")
        body = ""
        if msg.is_multipart():
            for part in msg.walk():
                if part.get_content_type() == "text/plain":
                    body = part.get_payload(decode=True).decode("utf-8", errors="replace")
                    break
        else:
            body = msg.get_payload(decode=True).decode("utf-8", errors="replace")

        incoming = IncomingMessage(
            id=msg.get("Message-ID", str(time.time())),
            from_id=from_addr,
            from_name=from_addr.split("<")[0].strip().strip('"'),
            platform="email",
            channel_id=from_addr,
            channel_name=f"Email from {from_addr}",
            text=f"Subject: {subject}\n\n{body}".strip(),
            raw={"from": from_addr, "subject": subject},
        )

        loop = asyncio.new_event_loop()
        response = loop.run_until_complete(self.router.route_message(self.agent_name, incoming))
        loop.close()

        if response:
            self._send_reply(from_addr, subject, response)

    def _send_reply(self, to_addr: str, subject: str, response: OutgoingMessage):
        try:
            msg = MIMEMultipart()
            msg["From"] = self.username
            msg["To"] = to_addr
            msg["Subject"] = f"Re: {subject}"

            html_content = response.content.replace("\n", "<br>")
            if response.buttons:
                html_content += "<br><br>"
                for b in response.buttons:
                    html_content += f'<a href="#">{b["label"]}</a> | '

            msg.attach(MIMEText(html_content, "html"))

            with smtplib.SMTP(self.smtp_host, self.smtp_port) as server:
                server.starttls()
                server.login(self.username, self.password)
                server.send_message(msg)
        except Exception as e:
            logger.error(f"SMTP send error: {e}")
