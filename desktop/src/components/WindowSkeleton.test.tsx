import { render, screen } from "@testing-library/react";
import { describe, it, expect, vi } from "vitest";
import { WindowSkeleton } from "./WindowSkeleton";

// Lazy thunk that never resolves so <Suspense> stays on the fallback forever.
vi.mock("@/registry/app-registry", () => ({
  getApp: () => ({
    id: "browser",
    name: "Browser",
    component: () => new Promise(() => {}),
  }),
}));

import { WindowContent } from "./WindowContent";

describe("WindowSkeleton", () => {
  it("renders a labelled skeleton placeholder", () => {
    render(<WindowSkeleton />);
    const skeleton = screen.getByTestId("window-skeleton");
    expect(skeleton).toBeTruthy();
    expect(screen.getByRole("status", { name: "Loading app" })).toBeTruthy();
  });

  it("is shown as the Suspense fallback while the app chunk loads", () => {
    render(<WindowContent appId="browser" windowId="w1" launchNonce={0} />);
    // Skeleton is rendered, not the old bare "Loading..." text.
    expect(screen.getByTestId("window-skeleton")).toBeTruthy();
    expect(screen.queryByText("Loading...")).toBeNull();
  });
});
