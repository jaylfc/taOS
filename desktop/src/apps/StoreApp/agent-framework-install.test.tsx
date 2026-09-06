/**
 * Tests for agent-framework install/open behaviour in the Store (#1582).
 *
 * Installing an agent-framework from the Store used to report "installed"
 * without the backend doing anything, and clicking "Open" was a no-op. This
 * verifies the frontend now (a) shows real base-image prefetch progress by
 * polling /api/agent-image/status instead of an instant "done", and
 * (b) routes an installed framework's action button into the Agents app
 * instead of doing nothing.
 */
import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { render, screen, fireEvent, waitFor, cleanup } from "@testing-library/react";
import { StoreApp } from "./index";

const FRAMEWORK_APP = {
  id: "hermes",
  name: "Hermes Agent Gateway",
  type: "agent-framework",
  version: "1.0.0",
  description: "Nous Research agent gateway",
  installed: false,
  compat: "green",
};

function makeFetch(opts: {
  catalogApp: Record<string, unknown>;
  installResponse?: Record<string, unknown>;
  imageStatusSequence?: Array<{ status: string }>;
}) {
  let imageStatusCalls = 0;
  const imageStatusSequence = opts.imageStatusSequence ?? [{ status: "idle" }];
  return vi.fn(async (url: string, init?: RequestInit) => {
    if (url === "/api/store/catalog") {
      return new Response(JSON.stringify([opts.catalogApp]), { status: 200, headers: { "content-type": "application/json" } });
    }
    if (url === "/api/store/installed-v2") {
      return new Response(JSON.stringify({ installed: [] }), { status: 200, headers: { "content-type": "application/json" } });
    }
    if (url === "/api/store/install-v2" && init?.method === "POST") {
      return new Response(
        JSON.stringify(opts.installResponse ?? { ok: true, app_id: "hermes", status: "installed", prefetch: "skipped" }),
        { status: 200, headers: { "content-type": "application/json" } },
      );
    }
    if (url === "/api/agent-image/status") {
      const idx = Math.min(imageStatusCalls, imageStatusSequence.length - 1);
      imageStatusCalls += 1;
      return new Response(JSON.stringify(imageStatusSequence[idx]), { status: 200, headers: { "content-type": "application/json" } });
    }
    if (url === "/api/store/install-progress/by-app/hermes") {
      return new Response(JSON.stringify({ app_id: "hermes", active: null }), { status: 200, headers: { "content-type": "application/json" } });
    }
    // Everything else (frameworks feed, agents list, install targets, optional catalog): 404 is fine.
    return new Response(null, { status: 404 });
  });
}

beforeEach(() => {
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

async function goToAgentsTab() {
  const agentsBtn = await screen.findByRole("button", { name: /^agents$/i });
  fireEvent.click(agentsBtn);
}

describe("Store agent-framework install/open (#1582)", () => {
  it("polls real base-image prefetch progress instead of instant-complete", async () => {
    global.fetch = makeFetch({
      catalogApp: FRAMEWORK_APP,
      installResponse: { ok: true, app_id: "hermes", status: "installed", prefetch: "started" },
      imageStatusSequence: [{ status: "downloading" }, { status: "importing" }, { status: "done" }],
    }) as any;

    render(<StoreApp windowId="test" />);
    await goToAgentsTab();

    const installBtn = await screen.findByRole("button", { name: /install hermes/i });
    fireEvent.click(installBtn);

    await waitFor(() => expect(screen.getByText(/downloading base image/i)).toBeInTheDocument());
  });

  it("does not show prefetch progress when the backend skips it", async () => {
    global.fetch = makeFetch({
      catalogApp: FRAMEWORK_APP,
      installResponse: { ok: true, app_id: "hermes", status: "installed", prefetch: "skipped" },
    }) as any;

    render(<StoreApp windowId="test" />);
    await goToAgentsTab();

    const installBtn = await screen.findByRole("button", { name: /install hermes/i });
    fireEvent.click(installBtn);

    // A successful install swaps this card into its "installed" layout
    // (Deploy in Agents + Uninstall) -- wait for that swap rather than the
    // stale pre-install button, which React unmounts once installed flips.
    await screen.findByRole("button", { name: /deploy hermes/i });
    expect(screen.queryByText(/downloading base image/i)).not.toBeInTheDocument();
  });

  it("routes an installed framework's action button to the Agents app instead of a no-op", async () => {
    global.fetch = makeFetch({ catalogApp: { ...FRAMEWORK_APP, installed: true } }) as any;

    const onOpenApp = vi.fn();
    window.addEventListener("taos:open-app", onOpenApp);

    render(<StoreApp windowId="test" />);
    await goToAgentsTab();

    const deployBtn = await screen.findByRole("button", { name: /deploy hermes/i });
    fireEvent.click(deployBtn);

    expect(onOpenApp).toHaveBeenCalledTimes(1);
    const detail = (onOpenApp.mock.calls[0][0] as CustomEvent).detail;
    expect(detail).toEqual({ app: "agents" });

    window.removeEventListener("taos:open-app", onOpenApp);
  });
});
