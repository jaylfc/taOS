"""Smoke tests for tinyagentos.worker.browser_main.build()."""
from __future__ import annotations

from tinyagentos.worker.browser_main import build


class TestBrowserMainBuild:
    def test_build_returns_app_and_agent(self):
        app, agent = build("http://controller:6969", node_ip="10.0.0.5")
        assert app is not None
        assert agent is not None

    def test_app_has_start_route(self):
        app, _ = build("http://controller:6969", node_ip="10.0.0.5")
        paths = [route.path for route in app.routes]
        assert "/worker/browser/start" in paths

    def test_app_has_stop_route(self):
        app, _ = build("http://controller:6969", node_ip="10.0.0.5")
        paths = [route.path for route in app.routes]
        assert "/worker/browser/stop" in paths

    def test_agent_has_browser_capability(self):
        _, agent = build("http://controller:6969", node_ip="10.0.0.5")
        assert "browser" in agent.extra_capabilities

    def test_agent_advertise_url_uses_node_ip_and_port(self):
        _, agent = build("http://controller:6969", node_ip="10.0.0.5", http_api_port=7080)
        assert agent.advertise_url == "http://10.0.0.5:7080"

    def test_agent_advertise_url_custom_port(self):
        _, agent = build("http://controller:6969", node_ip="node.example", http_api_port=9090)
        assert agent.advertise_url == "http://node.example:9090"

    def test_agent_name_forwarded(self):
        _, agent = build("http://controller:6969", node_ip="10.0.0.5", name="my-browser-node")
        assert agent.name == "my-browser-node"
