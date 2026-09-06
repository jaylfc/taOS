// desktop/src/apps/StoreApp/MobileStore.test.tsx
import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { render, screen, fireEvent, waitFor, cleanup } from "@testing-library/react";
import { MobileStore } from "./MobileStore";
import { StoreCover, AppIcon } from "./AppIcon";
import type { CatalogApp, InstallTarget } from "./types";

function app(over: Partial<CatalogApp>): CatalogApp {
  return {
    id: "x", name: "X", type: "agent-framework", version: "1.0.0",
    description: "d", installed: false, compat: "green", ...over,
  } as CatalogApp;
}

const TARGETS: InstallTarget[] = [];

function renderStore(apps: CatalogApp[], onInstall = vi.fn()) {
  return render(
    <MobileStore
      apps={apps}
      loading={false}
      installTargets={TARGETS}
      selectedDevices={[]}
      onDevicesChange={() => {}}
      selectedBackends={[]}
      compatMap={new Map()}
      onInstall={onInstall}
    />,
  );
}

beforeEach(() => {
  // jsdom does not implement Element.scrollTo; MobileStore calls it on tab change.
  if (!Element.prototype.scrollTo) {
    Element.prototype.scrollTo = (() => {}) as typeof Element.prototype.scrollTo;
  }
});
afterEach(() => { cleanup(); vi.restoreAllMocks(); });

describe("MobileStore GetButton", () => {
  it("shows Get for a not-installed app and an honest Installed status (not a fake Open)", () => {
    renderStore([
      app({ id: "a", name: "Alpha", stars: 100 }),
      app({ id: "b", name: "Bravo", stars: 50, installed: true }),
    ]);
    expect(screen.getAllByText("Get").length).toBeGreaterThan(0);
    expect(screen.getAllByText("Installed").length).toBeGreaterThan(0);
    expect(screen.queryByText("Open")).toBeNull();
    // The installed indicator is a status, not a button masquerading as an action.
    const status = screen.getAllByText("Installed")[0].closest("[role=status]");
    expect(status).not.toBeNull();
  });

  it("surfaces a Retry affordance when the install request fails", async () => {
    vi.spyOn(globalThis, "fetch").mockResolvedValue(
      new Response(null, { status: 500 }),
    );
    renderStore([app({ id: "a", name: "Alpha", stars: 100 })]);
    fireEvent.click(screen.getAllByText("Get")[0]);
    await waitFor(() => expect(screen.getAllByText("Retry").length).toBeGreaterThan(0));
  });

  it("calls onInstall when the install request succeeds", async () => {
    vi.spyOn(globalThis, "fetch").mockResolvedValue(
      new Response("{}", { status: 200 }),
    );
    const onInstall = vi.fn();
    renderStore([app({ id: "a", name: "Alpha", stars: 100 })], onInstall);
    fireEvent.click(screen.getAllByText("Get")[0]);
    await waitFor(() => expect(onInstall).toHaveBeenCalledWith("a"));
  });
});

describe("StoreCover / AppIcon instance reuse", () => {
  it("StoreCover retries the new image after a prior error when coverImage changes", () => {
    const { rerender, container } = render(
      <StoreCover app={app({ id: "a", coverImage: "/a.webp" })} />,
    );
    // Force the first image into a failed state.
    fireEvent.error(container.querySelector("img")!);
    expect(container.querySelector("img")).toBeNull();
    // A new app on the reused instance must retry its own cover.
    rerender(<StoreCover app={app({ id: "b", coverImage: "/b.webp" })} />);
    const img = container.querySelector("img");
    expect(img).not.toBeNull();
    expect(img!.getAttribute("src")).toBe("/b.webp");
  });

  it("AppIcon resets to the first candidate icon when the app prop changes", () => {
    const { rerender, container } = render(
      <AppIcon app={app({ id: "a", name: "Alpha", iconSlug: "alpha" })} size={56} />,
    );
    // Exhaust candidates so the first app falls back to its monogram.
    let img = container.querySelector("img");
    while (img) { fireEvent.error(img); img = container.querySelector("img"); }
    expect(container.querySelector("img")).toBeNull();
    // The reused instance must start a new app from its first icon candidate.
    rerender(<AppIcon app={app({ id: "b", name: "Bravo", iconSlug: "bravo" })} size={56} />);
    expect(container.querySelector("img")).not.toBeNull();
  });
});
