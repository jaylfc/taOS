/**
 * Tests for the non-commercial weights license accept-gate (#169).
 *
 * Installing a service whose backend pins non-commercial weights (e.g.
 * musicgen's CC-BY-NC 4.0 MusicGen weights) must not silently install --
 * the backend returns 412 needs_license_acceptance, and the Store must show
 * a license dialog and only proceed once the user agrees (re-POSTing
 * install-v2 with accepted: true).
 */
import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { render, screen, fireEvent, waitFor, cleanup } from "@testing-library/react";
import { StoreApp } from "./index";

const NC_APP = {
  id: "musicgen",
  name: "MusicGen",
  type: "service",
  category: "music",
  version: "1.3.0",
  description: "Meta's text-to-music",
  installed: false,
  compat: "green",
  license: "MIT",
  weights_license: "CC-BY-NC 4.0",
  license_class: "non-commercial",
};

function makeFetch(opts: { installCalls: Array<Record<string, unknown>> }) {
  return vi.fn(async (url: string, init?: RequestInit) => {
    if (url === "/api/store/catalog") {
      return new Response(JSON.stringify([NC_APP]), { status: 200, headers: { "content-type": "application/json" } });
    }
    if (url === "/api/store/install-v2" && init?.method === "POST") {
      const body = JSON.parse(String(init.body ?? "{}"));
      opts.installCalls.push(body);
      if (body.accepted === true) {
        return new Response(JSON.stringify({ status: "installed", app_id: "musicgen" }), { status: 200, headers: { "content-type": "application/json" } });
      }
      return new Response(
        JSON.stringify({
          needs_license_acceptance: true,
          license_id: "CC-BY-NC 4.0",
          weights_license: "CC-BY-NC 4.0",
          name: "MusicGen",
          text: "MusicGen downloads model weights licensed under CC-BY-NC 4.0, for non-commercial use only.",
        }),
        { status: 412, headers: { "content-type": "application/json" } },
      );
    }
    if (url === "/api/store/install-progress/by-app/musicgen") {
      return new Response(JSON.stringify({ app_id: "musicgen", active: null }), { status: 200, headers: { "content-type": "application/json" } });
    }
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

async function goToServicesTab() {
  const servicesBtn = await screen.findByRole("button", { name: /^services$/i });
  fireEvent.click(servicesBtn);
}

describe("Store non-commercial weights license gate (#169)", () => {
  it("shows a 'Non-commercial weights' badge on the card", async () => {
    global.fetch = makeFetch({ installCalls: [] }) as any;
    render(<StoreApp windowId="test" />);
    await goToServicesTab();
    await screen.findByRole("button", { name: /install musicgen/i });
    expect(screen.getByText(/non-commercial weights/i)).toBeInTheDocument();
  });

  it("blocks install behind a 412 and shows the license dialog", async () => {
    const installCalls: Array<Record<string, unknown>> = [];
    global.fetch = makeFetch({ installCalls }) as any;

    render(<StoreApp windowId="test" />);
    await goToServicesTab();

    const installBtn = await screen.findByRole("button", { name: /install musicgen/i });
    fireEvent.click(installBtn);

    await screen.findByRole("dialog", { name: /musicgen weights license/i });
    expect(screen.getAllByText(/CC-BY-NC 4.0/).length).toBeGreaterThan(0);
    expect(installCalls).toHaveLength(1);
    expect(installCalls[0].accepted).toBeUndefined();

    // Install must not have gone through yet -- the Install button (not
    // Uninstall) is still the card's action.
    expect(screen.queryByRole("button", { name: /uninstall musicgen/i })).not.toBeInTheDocument();
  });

  it("re-POSTs with accepted: true and installs after Agree & Install", async () => {
    const installCalls: Array<Record<string, unknown>> = [];
    global.fetch = makeFetch({ installCalls }) as any;

    render(<StoreApp windowId="test" />);
    await goToServicesTab();

    const installBtn = await screen.findByRole("button", { name: /install musicgen/i });
    fireEvent.click(installBtn);

    await screen.findByRole("dialog", { name: /musicgen weights license/i });
    const agreeBtn = screen.getByRole("button", { name: /agree & install/i });
    fireEvent.click(agreeBtn);

    await waitFor(() => expect(installCalls).toHaveLength(2));
    expect(installCalls[1].accepted).toBe(true);

    // Dialog closes and the card flips to installed (Uninstall action).
    await waitFor(() => expect(screen.queryByRole("dialog", { name: /musicgen weights license/i })).not.toBeInTheDocument());
  });

  it("Cancel closes the dialog without installing", async () => {
    const installCalls: Array<Record<string, unknown>> = [];
    global.fetch = makeFetch({ installCalls }) as any;

    render(<StoreApp windowId="test" />);
    await goToServicesTab();

    const installBtn = await screen.findByRole("button", { name: /install musicgen/i });
    fireEvent.click(installBtn);

    await screen.findByRole("dialog", { name: /musicgen weights license/i });
    fireEvent.click(screen.getByRole("button", { name: /^cancel$/i }));

    expect(screen.queryByRole("dialog", { name: /musicgen weights license/i })).not.toBeInTheDocument();
    expect(installCalls).toHaveLength(1);
  });
});
