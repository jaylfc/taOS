import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen } from "@testing-library/react";
import { ServiceAppWindow } from "../ServiceAppWindow";

// ---------------------------------------------------------------------------
// Mock the process store to control window props in each test
// ---------------------------------------------------------------------------
const mockWindows: Record<string, unknown>[] = [];

vi.mock("@/stores/process-store", () => ({
  useProcessStore: (selector: (s: { windows: unknown[] }) => unknown) =>
    selector({ windows: mockWindows }),
}));

beforeEach(() => {
  mockWindows.length = 0;
});

describe("ServiceAppWindow", () => {
  it("renders an iframe with the service URL", () => {
    mockWindows.push({
      id: "win-1",
      appId: "service:gitea-lxc",
      props: { url: "/apps/gitea-lxc/", displayName: "Gitea" },
    });

    render(<ServiceAppWindow windowId="win-1" />);

    const iframe = screen.getByTitle("Gitea");
    expect(iframe.tagName).toBe("IFRAME");
    expect(iframe).toHaveAttribute("src", "/apps/gitea-lxc/");
  });

  it("sets required sandbox attributes", () => {
    mockWindows.push({
      id: "win-2",
      appId: "service:gitea-lxc",
      props: { url: "/apps/gitea-lxc/", displayName: "Gitea" },
    });

    render(<ServiceAppWindow windowId="win-2" />);

    const iframe = screen.getByTitle("Gitea");
    const sandbox = iframe.getAttribute("sandbox") ?? "";
    expect(sandbox).toContain("allow-scripts");
    expect(sandbox).toContain("allow-forms");
    expect(sandbox).toContain("allow-same-origin");
    expect(sandbox).toContain("allow-popups");
    expect(sandbox).toContain("allow-downloads");

    const allow = iframe.getAttribute("allow") ?? "";
    expect(allow).toContain("clipboard-write");
    expect(allow).toContain("fullscreen");
  });

  it("renders an error message when URL is missing", () => {
    mockWindows.push({
      id: "win-3",
      appId: "service:no-url",
      props: {},
    });

    render(<ServiceAppWindow windowId="win-3" />);

    expect(screen.queryByRole("document")).toBeNull(); // no iframe
    expect(screen.getByText(/no url configured/i)).toBeTruthy();
  });

  it("renders an error message when window is not found", () => {
    // mockWindows is empty — no window with this ID
    render(<ServiceAppWindow windowId="win-missing" />);

    expect(screen.getByText(/no url configured/i)).toBeTruthy();
  });
});
