"""Tests for CSP injection helper used on proxied responses."""
from __future__ import annotations


class TestProxiedResponseCsp:
    def test_returns_a_string(self):
        from tinyagentos.routes.desktop_browser.csp import proxied_response_csp

        csp = proxied_response_csp()
        assert isinstance(csp, str)
        assert len(csp) > 0

    def test_blocks_default_src_to_self_only(self):
        """The proxied page must not be able to load resources from
        arbitrary origins (those would bypass our proxy and leak
        the user's IP). default-src 'self' enforces this."""
        from tinyagentos.routes.desktop_browser.csp import proxied_response_csp

        csp = proxied_response_csp()
        assert "default-src 'self'" in csp

    def test_blocks_inline_scripts(self):
        """Strict CSP for SCRIPTS: no unsafe-inline, no unsafe-eval in
        the script-src directive. Style-src may permit 'unsafe-inline'
        because CSS isn't a meaningful XSS vector and inline styles are
        universal in real-world HTML — blocking them would render most
        proxied pages unstyled. The check is therefore scoped to the
        script-src directive specifically."""
        from tinyagentos.routes.desktop_browser.csp import proxied_response_csp

        csp = proxied_response_csp()
        # Find the script-src directive (between its name and the next ;)
        directives = {
            d.strip().split(" ", 1)[0]: d.strip()
            for d in csp.split(";")
            if d.strip()
        }
        script_src = directives.get("script-src", "")
        assert script_src, "script-src directive must exist"
        assert "'unsafe-inline'" not in script_src
        assert "'unsafe-eval'" not in script_src

    def test_disables_form_action(self):
        """Proxied page form submissions must go to 'self' (back through
        us), not to arbitrary endpoints — otherwise the proxied site
        could submit the user's data to a third party that bypasses our
        cookie jar."""
        from tinyagentos.routes.desktop_browser.csp import proxied_response_csp

        csp = proxied_response_csp()
        assert "form-action 'self'" in csp

    def test_blocks_object_src(self):
        """object-src 'none' must be set explicitly. Browser fallback to
        default-src is inconsistent across versions, and we want to
        block all plugin embeds (Flash, Java, legacy <object> tags)
        regardless of source."""
        from tinyagentos.routes.desktop_browser.csp import proxied_response_csp

        csp = proxied_response_csp()
        assert "object-src 'none'" in csp

    def test_locks_base_uri_to_self(self):
        """base-uri 'self' prevents a malicious <base href> tag in the
        proxied page from redirecting relative URL resolution to an
        attacker-controlled origin (which would bypass our rewriter)."""
        from tinyagentos.routes.desktop_browser.csp import proxied_response_csp

        csp = proxied_response_csp()
        assert "base-uri 'self'" in csp

    def test_locks_connect_src_to_self(self):
        """connect-src 'self' so proxied JS XHR/fetch/EventSource/WebSocket
        can only reach the proxy origin (us). Browser fallback for
        connect-src has historically been inconsistent."""
        from tinyagentos.routes.desktop_browser.csp import proxied_response_csp

        csp = proxied_response_csp()
        assert "connect-src 'self'" in csp

    def test_locks_worker_src_to_self(self):
        """worker-src 'self' blocks proxied pages from registering
        service workers / web workers at third-party origins."""
        from tinyagentos.routes.desktop_browser.csp import proxied_response_csp

        csp = proxied_response_csp()
        assert "worker-src 'self'" in csp

    def test_locks_frame_src_to_self(self):
        """frame-src 'self' so proxied pages can only iframe other
        proxied content (sub-frames also routed through us)."""
        from tinyagentos.routes.desktop_browser.csp import proxied_response_csp

        csp = proxied_response_csp()
        assert "frame-src 'self'" in csp

    def test_no_dangling_directive_separator(self):
        from tinyagentos.routes.desktop_browser.csp import proxied_response_csp

        csp = proxied_response_csp()
        assert not csp.endswith("; ")
        assert not csp.endswith(";")

    @staticmethod
    def _frame_ancestors(csp: str) -> str:
        for d in csp.split(";"):
            d = d.strip()
            if d.startswith("frame-ancestors"):
                return d
        return ""

    def test_frame_ancestors_self_only_without_shell_origin(self):
        """Single-port fallback (proxy on the main origin): 'self' alone."""
        from tinyagentos.routes.desktop_browser.csp import proxied_response_csp

        csp = proxied_response_csp()
        assert self._frame_ancestors(csp) == "frame-ancestors 'self'"

    def test_frame_ancestors_includes_shell_origin_when_given(self):
        """Dual-origin: the shell (main port) must be allowed to embed the
        proxy origin, else the iframe is blocked by frame-ancestors."""
        from tinyagentos.routes.desktop_browser.csp import proxied_response_csp

        csp = proxied_response_csp("http://taos.example:6969")
        assert (
            self._frame_ancestors(csp)
            == "frame-ancestors 'self' http://taos.example:6969"
        )
