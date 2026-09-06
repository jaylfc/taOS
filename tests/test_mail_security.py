"""Security validation for untrusted mail inputs and the CSP host helper."""

import pytest

from tinyagentos import mail_client
from tinyagentos.middleware.security_headers import _strip_port


class TestValidateUid:
    def test_accepts_plain_numeric_uid(self):
        assert mail_client._validate_uid("12345") == "12345"

    @pytest.mark.parametrize(
        "bad",
        [
            "",
            "1 (RFC822)",
            "1\r\nA OK",
            "1:5",
            "abc",
            "1,2,3",
            "*",
        ],
    )
    def test_rejects_non_numeric_or_injection(self, bad):
        with pytest.raises(mail_client.MailValidationError):
            mail_client._validate_uid(bad)


class TestValidateHeader:
    def test_accepts_clean_value(self):
        assert mail_client._validate_header("Hello there", "subject") == "Hello there"
        assert mail_client._validate_header("a@b.test, c@d.test", "to") == "a@b.test, c@d.test"

    @pytest.mark.parametrize(
        "bad",
        [
            "subject\r\nBcc: victim@evil.test",
            "name\nX-Injected: 1",
            "value\rwith-cr",
            "has\x00null",
        ],
    )
    def test_rejects_crlf_injection(self, bad):
        with pytest.raises(mail_client.MailValidationError):
            mail_client._validate_header(bad, "to")


class TestStripPort:
    @pytest.mark.parametrize(
        "host,expected",
        [
            ("example.com", "example.com"),
            ("example.com:6969", "example.com"),
            ("192.168.1.5:443", "192.168.1.5"),
            ("[::1]", "[::1]"),
            ("[::1]:6969", "[::1]"),
            ("[2001:db8::1]:8080", "[2001:db8::1]"),
        ],
    )
    def test_strips_port_without_corrupting_ipv6(self, host, expected):
        assert _strip_port(host) == expected
