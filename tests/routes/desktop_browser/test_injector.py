"""Tests for the head injector — adds copilot.js script + meta tags."""
from __future__ import annotations


class TestInjectIntoHead:
    def test_injects_copilot_script(self):
        from tinyagentos.routes.desktop_browser.injector import inject_into_head

        html = b"<html><head><title>x</title></head><body></body></html>"
        out = inject_into_head(html, ws_url="ws://taos/copilot")

        assert b'<script src="/__taos/copilot.js"' in out

    def test_injects_meta_ws_url(self):
        from tinyagentos.routes.desktop_browser.injector import inject_into_head

        html = b"<html><head></head><body></body></html>"
        out = inject_into_head(html, ws_url="ws://taos/copilot?tab=t1")

        assert b'name="taos-copilot-ws"' in out
        assert b'ws://taos/copilot?tab=t1' in out

    def test_handles_missing_head_by_creating_one(self):
        from tinyagentos.routes.desktop_browser.injector import inject_into_head

        html = b"<html><body><p>no head</p></body></html>"
        out = inject_into_head(html, ws_url="ws://x/")

        # Either a head was created and the script injected, or the
        # script was injected at the document start. Either way the
        # script tag must be present in the output.
        assert b'<script src="/__taos/copilot.js"' in out

    def test_idempotent_does_not_double_inject(self):
        from tinyagentos.routes.desktop_browser.injector import inject_into_head

        html = b"<html><head></head><body></body></html>"
        once = inject_into_head(html, ws_url="ws://x/")
        twice = inject_into_head(once, ws_url="ws://x/")

        # The script tag should appear exactly once
        assert twice.count(b'<script src="/__taos/copilot.js"') == 1

    def test_passes_through_empty_input(self):
        from tinyagentos.routes.desktop_browser.injector import inject_into_head

        out = inject_into_head(b"", ws_url="ws://x/")
        assert out == b""

    def test_injects_sw_prime_meta(self):
        from tinyagentos.routes.desktop_browser.injector import inject_into_head

        html = b"<html><head></head><body></body></html>"
        out = inject_into_head(
            html,
            ws_url="ws://x/",
            page_base_url="https://example.com/app",
            profile_id="work",
        )

        assert b'name="taos-page-base"' in out
        assert b'https://example.com/app' in out
        assert b'name="taos-profile-id"' in out
        assert b'content="work"' in out

    def test_omits_sw_prime_meta_when_not_provided(self):
        from tinyagentos.routes.desktop_browser.injector import inject_into_head

        html = b"<html><head></head><body></body></html>"
        out = inject_into_head(html, ws_url="ws://x/")

        assert b'name="taos-page-base"' not in out
        assert b'name="taos-profile-id"' not in out
