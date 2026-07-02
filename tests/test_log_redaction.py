from __future__ import annotations

from tinyagentos.log_redaction import PLACEHOLDER, redact, redact_lines


class TestKeyValue:
    def test_equals_form(self):
        assert redact("api_key=sk-abc123def456ghi789") == f"api_key={PLACEHOLDER}"

    def test_colon_form(self):
        assert redact("password: hunter2secret") == f"password: {PLACEHOLDER}"

    def test_json_form(self):
        out = redact('{"secret": "topsecretvalue123"}')
        assert "topsecretvalue123" not in out
        assert PLACEHOLDER in out

    def test_flag_form(self):
        assert redact("--token deadbeefcafebabe01") == f"--token {PLACEHOLDER}"

    def test_case_insensitive(self):
        assert redact("PASSWORD=SuperSecret99") == f"PASSWORD={PLACEHOLDER}"

    def test_does_not_match_substring_key(self):
        # "monkey" must not trip the "key" rule.
        assert redact("monkey=banana") == "monkey=banana"

    def test_preserves_surrounding_text(self):
        out = redact("connecting with token=abcdef123456 to host db1")
        assert out == f"connecting with token={PLACEHOLDER} to host db1"


class TestBearer:
    def test_header(self):
        assert redact("Authorization: Bearer abc123def456ghi") == \
            f"Authorization: Bearer {PLACEHOLDER}"


class TestTokenShapes:
    def test_openai_style(self):
        assert redact("using sk-taos-abcdefghij0123456789 now").count(PLACEHOLDER) == 1
        assert "abcdefghij0123456789" not in redact("sk-taos-abcdefghij0123456789")

    def test_github_pat(self):
        assert PLACEHOLDER in redact("ghp_EXAMPLEEXAMPLEEXAMPLEEXAMPLE00")

    def test_slack(self):
        assert PLACEHOLDER in redact("xoxb-EXAMPLEEXAMPLE-EXAMPLEEXAMPLETOKEN")

    def test_aws_akia(self):
        assert PLACEHOLDER in redact("AKIAEXAMPLEEXAMPLE00")


class TestPem:
    def test_private_key_block(self):
        block = (
            "-----BEGIN OPENSSH PRIVATE KEY-----\n"
            "b3BlbnNzaC1rZXktdjEAAAAABG5vbmU\nAAAAAAAA\n"
            "-----END OPENSSH PRIVATE KEY-----"
        )
        out = redact(f"key material:\n{block}\ndone")
        assert "b3BlbnNz" not in out
        assert out.startswith("key material:")
        assert out.endswith("done")


class TestConnectionString:
    def test_masks_only_password(self):
        out = redact("dsn=postgres://taos:s3cr3tpw@db.internal:5432/app")
        assert "s3cr3tpw" not in out
        assert "postgres://taos:" in out
        assert "@db.internal:5432/app" in out


class TestKnownValues:
    def test_literal_value_masked(self):
        out = redact("the model returned plainlookingkey987", known_values=["plainlookingkey987"])
        assert "plainlookingkey987" not in out
        assert PLACEHOLDER in out

    def test_short_known_value_ignored(self):
        # too short to safely mask -> left alone (no runaway redaction)
        assert redact("abc appears here", known_values=["abc"]) == "abc appears here"

    def test_empty_known_values_noop(self):
        assert redact("nothing sensitive here", known_values=[]) == "nothing sensitive here"

    def test_none_known_values(self):
        assert redact("nothing sensitive here") == "nothing sensitive here"


class TestSafety:
    def test_empty_string(self):
        assert redact("") == ""

    def test_clean_line_untouched(self):
        line = "2026-07-02 19:00:00 INFO controller ready on port 6969"
        assert redact(line) == line

    def test_redact_lines(self):
        out = redact_lines(["password=abcdef123456", "all good here"])
        assert out[0] == f"password={PLACEHOLDER}"
        assert out[1] == "all good here"


class TestFoldedFindings:
    """Negative/regression tests for the review folds (base64 bearer, JWT,
    partial-leak known values, PEM framing, coercion)."""

    def test_base64_bearer_masked_whole(self):
        # + / = must not truncate the value (AWS/OAuth2 token shape).
        out = redact("Authorization: Bearer abc123+def/456ghi789=")
        assert "abc123" not in out
        assert out == f"Authorization: Bearer {PLACEHOLDER}"

    def test_bare_jwt_masked(self):
        # Bare in prose: no key= prefix and no Bearer word, so only the
        # standalone JWT shape rule can catch it.
        jwt = "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxMjM0NTY3ODkwIn0.SflKxwRJSMeKKF2QT4"
        out = redact(f"decoded session {jwt} from the proxy header")
        assert "SflKxwRJSMeKKF2QT4" not in out
        assert PLACEHOLDER in out

    def test_google_api_key(self):
        assert PLACEHOLDER in redact("AIzaSyA0000000000000000000000000000000X")

    def test_stripe_live_key(self):
        assert PLACEHOLDER in redact("sk_live_0000000000000000abcdef")

    def test_known_value_masks_whole_token(self):
        # secret is a prefix of a longer id: the whole token must vanish, no
        # trailing leak, no garbled placeholder.
        out = redact("request_id=abcdef1234560000", known_values=["abcdef123456"])
        assert "0000" not in out
        assert "[REDACTED]0" not in out
        assert PLACEHOLDER in out

    def test_pem_keeps_framing(self):
        block = (
            "-----BEGIN OPENSSH PRIVATE KEY-----\n"
            "b3BlbnNzaC1rZXktdjEAAAAA\n"
            "-----END OPENSSH PRIVATE KEY-----"
        )
        out = redact(block)
        assert "b3BlbnNz" not in out
        assert "-----BEGIN OPENSSH PRIVATE KEY-----" in out
        assert "-----END OPENSSH PRIVATE KEY-----" in out

    def test_redact_lines_coerces_non_str(self):
        out = redact_lines(["password=abcdef123456", None, 42])
        assert out[0] == f"password={PLACEHOLDER}"
        assert out[1] == "None"
        assert out[2] == "42"
