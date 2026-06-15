from email.message import EmailMessage

import pytest

from tinyagentos.mail_client import (
    MailFolderError,
    _validate_folder,
    parse_detail,
    parse_envelope,
)


class TestValidateFolder:
    def test_accepts_normal_names(self):
        assert _validate_folder("INBOX") == "INBOX"
        assert _validate_folder("[Gmail]/Sent Mail") == "[Gmail]/Sent Mail"

    @pytest.mark.parametrize("bad", ['IN"BOX', "a\r\nLOGOUT", "x\x00y", ""])
    def test_rejects_injection_chars(self, bad):
        with pytest.raises(MailFolderError):
            _validate_folder(bad)


def _plain_message():
    msg = EmailMessage()
    msg["From"] = "Dhaval Patel <dhaval@example.com>"
    msg["To"] = "jay@taos.my"
    msg["Subject"] = "AssetOpsBench integration"
    msg["Date"] = "Mon, 15 Jun 2026 09:24:00 +0000"
    msg.set_content("Thanks for the quick turnaround on the connector.\nBenchmark runs clean.")
    return msg


class TestParseEnvelope:
    def test_basic_fields(self):
        msg = _plain_message()
        env = parse_envelope("42", msg, flags=b"")
        assert env.uid == "42"
        assert env.from_name == "Dhaval Patel"
        assert env.from_addr == "dhaval@example.com"
        assert env.subject == "AssetOpsBench integration"
        assert "Thanks for the quick turnaround" in env.snippet
        # No \\Seen flag means unread.
        assert env.unread is True
        assert env.flagged is False
        assert env.has_attachment is False

    def test_seen_and_flagged_flags(self):
        env = parse_envelope("1", _plain_message(), flags=b"(\\Seen \\Flagged)")
        assert env.unread is False
        assert env.flagged is True

    def test_encoded_subject_is_decoded(self):
        msg = _plain_message()
        del msg["Subject"]
        # RFC 2047 encoded-word for "Hello"
        msg["Subject"] = "=?utf-8?q?Hello?="
        env = parse_envelope("2", msg, flags=b"")
        assert env.subject == "Hello"

    def test_detects_attachment(self):
        msg = _plain_message()
        msg.add_attachment(
            b"%PDF-1.4 fake",
            maintype="application",
            subtype="pdf",
            filename="notes.pdf",
        )
        env = parse_envelope("3", msg, flags=b"")
        assert env.has_attachment is True

    def test_missing_from_is_safe(self):
        msg = EmailMessage()
        msg["Subject"] = "no sender"
        msg.set_content("body")
        env = parse_envelope("4", msg, flags=b"")
        assert env.from_addr == ""
        assert env.subject == "no sender"


class TestParseDetail:
    def test_body_text(self):
        detail = parse_detail("42", _plain_message())
        assert "Benchmark runs clean" in detail.body_text
        assert detail.from_addr == "dhaval@example.com"
        assert detail.to == "jay@taos.my"
        assert detail.attachments == []

    def test_attachments_listed(self):
        msg = _plain_message()
        msg.add_attachment(
            b"binarydata-1234",
            maintype="application",
            subtype="pdf",
            filename="report.pdf",
        )
        detail = parse_detail("5", msg)
        assert len(detail.attachments) == 1
        att = detail.attachments[0]
        assert att.filename == "report.pdf"
        assert att.content_type == "application/pdf"
        assert att.size == len(b"binarydata-1234")

    def test_html_body(self):
        msg = EmailMessage()
        msg["From"] = "a@b.com"
        msg["Subject"] = "html"
        msg.set_content("plain fallback")
        msg.add_alternative("<p>rich</p>", subtype="html")
        detail = parse_detail("6", msg)
        assert "rich" in detail.body_html
        assert "plain fallback" in detail.body_text
