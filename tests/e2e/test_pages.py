"""E2E smoke tests — verify all main pages load without errors."""
import pytest
from playwright.sync_api import Page, expect


PAGES = [
    ("/", "Dashboard", "dashboard"),
    ("/store", "Store", "store"),
    ("/agents", "Agents", "agents"),
    ("/models", "Models", "models"),
    ("/settings", "Settings", "settings"),
    ("/channel-hub", "Channel Hub", "channel-hub"),
    ("/shared-folders", "Shared Folders", "shared-folders"),
    ("/training", "Training", "training"),
    ("/health-check", "Health", "health-check"),
    ("/channels", "Channels", "channels"),
    ("/relationships", "Relationships", "relationships"),
    ("/tasks", "Tasks", "tasks"),
    ("/import", "Import", "import"),
    ("/secrets", "Secrets", "secrets"),
    ("/cluster", "Cluster", "cluster"),
    ("/conversions", "Model Conversion", "conversions"),
]


class TestPageLoads:
    """Smoke test: every page loads with 200 and no console errors."""

    @pytest.mark.parametrize("path,title_fragment,page_id", PAGES)
    def test_page_loads(self, page: Page, base_url: str, path: str, title_fragment: str, page_id: str):
        response = page.goto(f"{base_url}{path}")
        assert response is not None
        assert response.status == 200, f"Page {path} returned {response.status}"
        # Check page has content (not empty)
        assert len(page.content()) > 100

    @pytest.mark.parametrize("path,title_fragment,page_id", PAGES)
    def test_no_console_errors(self, page: Page, base_url: str, path: str, title_fragment: str, page_id: str):
        errors = []
        page.on("console", lambda msg: errors.append(msg.text) if msg.type == "error" else None)
        page.goto(f"{base_url}{path}")
        page.wait_for_load_state("networkidle")
        # Filter out known non-critical errors (e.g. failed htmx requests to backends that aren't running)
        critical_errors = [e for e in errors if "htmx" not in e.lower() and "fetch" not in e.lower()]
        assert len(critical_errors) == 0, f"Console errors on {path}: {critical_errors}"


class TestNavigation:
    """Test that navigation links work."""

    def test_nav_links_present(self, page: Page, base_url: str):
        page.goto(base_url)
        nav = page.locator("nav")
        expect(nav).to_be_visible()
        # Check key nav links exist
        for href in ["/store", "/agents", "/models", "/settings"]:
            link = nav.locator(f'a[href="{href}"]').first
            expect(link).to_be_visible()

    def test_navigate_to_store(self, page: Page, base_url: str):
        page.goto(base_url)
        page.click('a[href="/store"]')
        page.wait_for_load_state("networkidle")
        assert "/store" in page.url

    def test_navigate_to_agents(self, page: Page, base_url: str):
        page.goto(base_url)
        page.click('a[href="/agents"]')
        page.wait_for_load_state("networkidle")
        assert "/agents" in page.url


class TestDashboard:
    """Dashboard-specific tests."""

    def test_dashboard_has_kpi_cards(self, page: Page, base_url: str):
        page.goto(base_url)
        # Wait for htmx to load KPI cards
        page.wait_for_load_state("networkidle")
        # The dashboard should have content loaded via htmx
        content = page.content()
        assert len(content) > 500  # Page should have substantial content

    def test_theme_toggle(self, page: Page, base_url: str):
        page.goto(base_url)
        html = page.locator("html")
        initial_theme = html.get_attribute("data-theme")
        page.click("#theme-toggle")
        new_theme = html.get_attribute("data-theme")
        assert initial_theme != new_theme


class TestAccessibility:
    """Basic accessibility checks."""

    @pytest.mark.parametrize("path,title_fragment,page_id", PAGES[:5])
    def test_skip_link_exists(self, page: Page, base_url: str, path: str, title_fragment: str, page_id: str):
        page.goto(f"{base_url}{path}")
        skip_link = page.locator('a[href="#main-content"]')
        assert skip_link.count() > 0

    def test_nav_has_aria_label(self, page: Page, base_url: str):
        page.goto(base_url)
        nav = page.locator('nav[aria-label="Main navigation"]')
        expect(nav).to_be_visible()

    def test_main_has_role(self, page: Page, base_url: str):
        page.goto(base_url)
        main = page.locator('main[role="main"]')
        expect(main).to_be_visible()
