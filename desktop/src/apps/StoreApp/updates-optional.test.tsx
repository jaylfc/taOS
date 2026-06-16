/**
 * Tests for the optional app versioning integration in the Store updates tab.
 *
 * The StoreApp renders the optional catalog as a distinct section inside the
 * "updates" view. This test suite verifies:
 *   - The catalog endpoint is fetched on mount.
 *   - Installed optional apps with update_available=true appear in the updates tab.
 *   - The "all up to date" empty state is shown when nothing needs updating.
 */
import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { render, screen, fireEvent, waitFor, cleanup } from "@testing-library/react";
import { StoreApp } from "./index";

// Minimal fetch stubs for all endpoints the StoreApp calls on mount.
function makeFetch(optionalCatalogApps: Array<{
  id: string; version: string; installed: boolean; update_available: boolean;
  trust?: string; source?: string;
}>) {
  return vi.fn(async (url: string) => {
    if (url === "/api/store/catalog") {
      return new Response("[]", { status: 200, headers: { "content-type": "application/json" } });
    }
    if (url === "/api/store/installed-v2") {
      return new Response(JSON.stringify({ installed: [] }), { status: 200, headers: { "content-type": "application/json" } });
    }
    if (url === "/api/agents") {
      return new Response("[]", { status: 200, headers: { "content-type": "application/json" } });
    }
    if (url === "/api/cluster/install-targets") {
      return new Response(JSON.stringify([{ name: "local", label: "Local", type: "local" }]), { status: 200, headers: { "content-type": "application/json" } });
    }
    if (url === "/api/apps/optional/catalog") {
      return new Response(
        JSON.stringify({
          apps: optionalCatalogApps.map((a) => ({
            trust: "first-party", source: "core", ...a,
          })),
        }),
        { status: 200, headers: { "content-type": "application/json" } },
      );
    }
    // framework API + other endpoints: 404 is fine (caught silently)
    return new Response(null, { status: 404 });
  });
}

beforeEach(() => {
  // jsdom doesn't implement scrollTo
  if (!Element.prototype.scrollTo) {
    Element.prototype.scrollTo = (() => {}) as typeof Element.prototype.scrollTo;
  }
  if (!window.matchMedia) {
    Object.defineProperty(window, "matchMedia", {
      writable: true,
      value: vi.fn().mockImplementation((q: string) => ({
        matches: false, media: q, onchange: null,
        addListener: vi.fn(), removeListener: vi.fn(),
        addEventListener: vi.fn(), removeEventListener: vi.fn(), dispatchEvent: vi.fn(),
      })),
    });
  }
});

afterEach(() => { cleanup(); vi.restoreAllMocks(); });

describe("Store updates tab -- optional app versioning", () => {
  it("fetches the optional catalog on mount", async () => {
    const mockFetch = makeFetch([]);
    global.fetch = mockFetch as any;
    render(<StoreApp windowId="test" />);
    await waitFor(() =>
      expect(mockFetch.mock.calls.some(([url]: [string]) => url === "/api/apps/optional/catalog")).toBe(true)
    );
  });

  it("shows all up to date empty state when no optional apps need updates", async () => {
    global.fetch = makeFetch([
      { id: "reddit", version: "1.0.0", installed: true, update_available: false },
    ]) as any;
    render(<StoreApp windowId="test" />);
    // Navigate to updates tab
    const updatesBtn = await screen.findByRole("button", { name: /updates/i });
    fireEvent.click(updatesBtn);
    await waitFor(() => expect(screen.getByText(/all up to date/i)).toBeInTheDocument());
  });

  it("shows an installed optional app with update_available in the updates tab", async () => {
    global.fetch = makeFetch([
      { id: "reddit", version: "1.0.0", installed: true, update_available: true },
    ]) as any;
    render(<StoreApp windowId="test" />);
    const updatesBtn = await screen.findByRole("button", { name: /updates/i });
    fireEvent.click(updatesBtn);
    await waitFor(() => expect(screen.getByText("Reddit")).toBeInTheDocument());
    expect(screen.getByText(/included in next system update/i)).toBeInTheDocument();
    // Core badge should be visible
    expect(screen.getByText("Core")).toBeInTheDocument();
  });
});
