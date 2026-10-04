import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import { describe, it, expect, vi } from "vitest";
import { CodingStudioApp } from "./CodingStudioApp";

function renderApp() {
  return render(<CodingStudioApp windowId="test-window" />);
}

describe("CodingStudioApp", () => {
  it("renders all rail items", () => {
    renderApp();
    // Rail buttons use aria-label for exact matching via the nav element
    const nav = screen.getByRole("navigation", { name: "Coding Studio views" });
    expect(nav).toBeDefined();
    expect(screen.getByRole("button", { name: "Code" })).toBeDefined();
    expect(screen.getByRole("button", { name: "Preview" })).toBeDefined();
    expect(screen.getByRole("button", { name: "Templates" })).toBeDefined();
    expect(screen.getByRole("button", { name: "Models" })).toBeDefined();
  });

  it("shows Build view by default with Build rail item active", () => {
    renderApp();
    // The rail Build button (inside nav) should be aria-current="page"
    const nav = screen.getByRole("navigation", { name: "Coding Studio views" });
    const railBuildBtn = nav.querySelector('[aria-label="Build"]') as HTMLElement;
    expect(railBuildBtn).toBeTruthy();
    expect(railBuildBtn.getAttribute("aria-current")).toBe("page");
  });

  it("switches to Templates view on rail click", () => {
    renderApp();
    fireEvent.click(screen.getByRole("button", { name: "Templates" }));
    expect(screen.getByRole("button", { name: "Templates" }).getAttribute("aria-current")).toBe(
      "page",
    );
    expect(screen.getByText("Describe what you want to build.")).toBeDefined();
  });

  it("Templates view shows all 8 template cards", () => {
    renderApp();
    fireEvent.click(screen.getByRole("button", { name: "Templates" }));
    const expectedNames = [
      "Web App",
      "REST API",
      "CLI Tool",
      "Discord Bot",
      "Static Site",
      "Data Pipeline",
      "Python Script",
      "Browser Extension",
    ];
    for (const name of expectedNames) {
      expect(screen.getByText(name)).toBeDefined();
    }
  });

  it("switches to Preview view on rail click and shows preview header", () => {
    renderApp();
    fireEvent.click(screen.getByRole("button", { name: "Preview" }));
    expect(screen.getByRole("button", { name: "Preview" }).getAttribute("aria-current")).toBe(
      "page",
    );
    // Preview header h2
    expect(screen.getByRole("heading", { name: "Preview" })).toBeDefined();
  });

  it("handoff: selects a workspace in Build and switches to Code with that workspace selected", async () => {
    const originalFetch = globalThis.fetch;
    try {
      globalThis.fetch = vi.fn(async (url: string) => {
        if (url === "/api/coding/workspaces") {
          return {
            ok: true,
            json: async () => [
              { id: "ws-1", name: "Workspace 1", path: "/tmp/ws1", created_at: "2024-01-01" },
              { id: "ws-2", name: "Workspace 2", path: "/tmp/ws2", created_at: "2024-01-02" },
            ],
          } as Response;
        }
        return originalFetch(url);
      }) as typeof globalThis.fetch;

      renderApp();

      await waitFor(() => {
        expect(screen.getByLabelText("Select workspace")).toBeTruthy();
      });

      const buildSelect = screen.getByLabelText("Select workspace") as HTMLSelectElement;
      fireEvent.change(buildSelect, { target: { value: "ws-1" } });

      fireEvent.click(screen.getByRole("button", { name: "Code" }));

      await waitFor(() => {
        const codeSelect = screen.getByLabelText("Select workspace") as HTMLSelectElement;
        expect(codeSelect.value).toBe("ws-1");
      });
    } finally {
      globalThis.fetch = originalFetch;
    }
  });
});
