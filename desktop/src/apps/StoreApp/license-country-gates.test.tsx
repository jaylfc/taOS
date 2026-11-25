/**
 * Licence and country steps in the Store, on the desktop card and on the phone
 * (MobileStore). The server answers 412 needs_country / needs_license_acceptance;
 * both surfaces must show the matching dialog, save the country, and only send
 * accepted: true after the person taps Agree. A 412 must never read as a
 * failed install with an endless Retry.
 */
import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { render, screen, fireEvent, waitFor, cleanup } from "@testing-library/react";
import { StoreApp } from "./index";
import { MobileStore } from "./MobileStore";
import type { CatalogApp } from "./types";

const GATED_APP = {
  id: "hunyuan",
  name: "Hunyuan Video",
  type: "service",
  category: "video",
  version: "1.0.0",
  description: "Text to video",
  installed: false,
  compat: "green",
  license: "Tencent HunyuanVideo Community License",
};

type Call = Record<string, unknown>;

/** Server script: no country -> needs_country; country given -> needs licence; accepted -> installed. */
function gateFetch(calls: Call[], catalog: unknown[]) {
  return vi.fn(async (url: string, init?: RequestInit) => {
    const json = (body: unknown, status = 200) =>
      new Response(JSON.stringify(body), { status, headers: { "content-type": "application/json" } });
    if (url === "/api/store/catalog") return json(catalog);
    if (url === "/api/store/install-v2" && init?.method === "POST") {
      const body = JSON.parse(String(init.body ?? "{}"));
      calls.push(body);
      if (body.accepted === true) return json({ status: "installed", app_id: "hunyuan" });
      if (!body.country) {
        return json({ needs_country: true, detail: "Set your country in Settings, Account, Country." }, 412);
      }
      return json({
        needs_license_acceptance: true,
        license_id: "Tencent HunyuanVideo Community License",
        license_name: "Tencent HunyuanVideo Community License",
        name: "Hunyuan Video",
        text: "Hunyuan Video uses Tencent HunyuanVideo Community License, with use limited to certain countries.",
      }, 412);
    }
    if (String(url).startsWith("/api/store/install-progress/")) return json({ app_id: "hunyuan", active: null });
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

describe("desktop Store: country then licence", () => {
  it("asks for the country, saves it, then asks for the licence, then installs on Agree", async () => {
    const calls: Call[] = [];
    global.fetch = gateFetch(calls, [GATED_APP]) as unknown as typeof fetch;
    render(<StoreApp windowId="test" />);
    fireEvent.click(await screen.findByRole("button", { name: /^services$/i }));
    fireEvent.click(await screen.findByRole("button", { name: /install hunyuan video/i }));

    await screen.findByRole("dialog", { name: /hunyuan video country/i });
    expect(screen.getByText(/set your country in settings/i)).toBeInTheDocument();
    fireEvent.change(screen.getByLabelText("Your country"), { target: { value: "US" } });
    fireEvent.click(screen.getByRole("button", { name: /save and continue/i }));

    await screen.findByRole("dialog", { name: /hunyuan video weights license/i });
    expect(calls[1].country).toBe("US");
    expect(calls[1].accepted).toBeUndefined();
    expect(screen.queryByRole("dialog", { name: /country/i })).not.toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: /agree & install/i }));
    await waitFor(() => expect(calls).toHaveLength(3));
    expect(calls[2].accepted).toBe(true);
  });

  it("Cancel on the country dialog installs nothing", async () => {
    const calls: Call[] = [];
    global.fetch = gateFetch(calls, [GATED_APP]) as unknown as typeof fetch;
    render(<StoreApp windowId="test" />);
    fireEvent.click(await screen.findByRole("button", { name: /^services$/i }));
    fireEvent.click(await screen.findByRole("button", { name: /install hunyuan video/i }));
    await screen.findByRole("dialog", { name: /country/i });
    fireEvent.click(screen.getByRole("button", { name: /^cancel$/i }));
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
    expect(calls).toHaveLength(1);
  });
});

function mobileApp(): CatalogApp {
  return { ...GATED_APP, stars: 10 } as unknown as CatalogApp;
}

function renderMobile(onInstall = vi.fn()) {
  return render(
    <MobileStore
      apps={[mobileApp()]}
      loading={false}
      installTargets={[]}
      selectedDevices={[]}
      onDevicesChange={() => {}}
      selectedBackends={[]}
      compatMap={new Map()}
      onInstall={onInstall}
    />,
  );
}

describe("phone Store (MobileStore): country then licence", () => {
  it("shows the country dialog, then the licence dialog, then installs on Agree", async () => {
    const calls: Call[] = [];
    global.fetch = gateFetch(calls, []) as unknown as typeof fetch;
    const onInstall = vi.fn();
    renderMobile(onInstall);
    fireEvent.click(screen.getAllByText("Get")[0]);

    await screen.findByRole("dialog", { name: /hunyuan video country/i });
    // A 412 is a step for the person, not a failed install.
    expect(screen.queryByText("Retry")).not.toBeInTheDocument();
    fireEvent.change(screen.getByLabelText("Your country"), { target: { value: "GB" } });
    fireEvent.click(screen.getByRole("button", { name: /save and continue/i }));

    await screen.findByRole("dialog", { name: /hunyuan video weights license/i });
    expect(calls[1].country).toBe("GB");
    expect(onInstall).not.toHaveBeenCalled();

    fireEvent.click(screen.getByRole("button", { name: /agree & install/i }));
    await waitFor(() => expect(onInstall).toHaveBeenCalledWith("hunyuan"));
    expect(calls[calls.length - 1].accepted).toBe(true);
  });

  it("declining the licence dialog installs nothing and does not show Retry", async () => {
    const calls: Call[] = [];
    global.fetch = vi.fn(async (_u: string, init?: RequestInit) => {
      calls.push(JSON.parse(String(init?.body ?? "{}")));
      return new Response(JSON.stringify({
        needs_license_acceptance: true, license_name: "CC-BY-NC 4.0", name: "Hunyuan Video",
        text: "Hunyuan Video uses CC-BY-NC 4.0, for non-commercial use only.",
      }), { status: 412, headers: { "content-type": "application/json" } });
    }) as unknown as typeof fetch;
    const onInstall = vi.fn();
    renderMobile(onInstall);
    fireEvent.click(screen.getAllByText("Get")[0]);
    await screen.findByRole("dialog", { name: /weights license/i });
    fireEvent.click(screen.getByRole("button", { name: /^cancel$/i }));
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
    expect(screen.queryByText("Retry")).not.toBeInTheDocument();
    expect(onInstall).not.toHaveBeenCalled();
    expect(calls).toHaveLength(1);
  });

  it("a real failure still shows Retry", async () => {
    global.fetch = vi.fn(async () => new Response(null, { status: 500 })) as unknown as typeof fetch;
    renderMobile();
    fireEvent.click(screen.getAllByText("Get")[0]);
    await waitFor(() => expect(screen.getAllByText("Retry").length).toBeGreaterThan(0));
  });
});
