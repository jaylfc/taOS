import { describe, it, expect, vi, afterEach } from "vitest";
import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import { DemoModePanel, DEMO_CAPTION } from "./DemoModePanel";

function json(obj: unknown, status = 200) {
  return new Response(JSON.stringify(obj), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

describe("DemoModePanel", () => {
  afterEach(() => vi.restoreAllMocks());

  it("renders the switch with its caption", async () => {
    vi.spyOn(globalThis, "fetch").mockResolvedValue(json({ enabled: true, available: true }));
    render(<DemoModePanel />);
    const sw = await screen.findByLabelText("Demo mode", { selector: "button" });
    expect(sw).toHaveAttribute("aria-checked", "true");
    expect(screen.getByText(DEMO_CAPTION)).toBeInTheDocument();
    expect(DEMO_CAPTION).toBe(
      "Shows scripted agents, notifications and calls on the lock screen for demonstrations.",
    );
  });

  it("PUTs the new value and reflects the server's answer", async () => {
    const spy = vi.spyOn(globalThis, "fetch").mockImplementation((_u, init) =>
      Promise.resolve(
        init?.method === "PUT"
          ? json({ ok: true, enabled: false, available: true })
          : json({ enabled: true, available: true }),
      ),
    );
    render(<DemoModePanel />);
    fireEvent.click(await screen.findByLabelText("Demo mode", { selector: "button" }));
    await waitFor(() =>
      expect(screen.getByLabelText("Demo mode", { selector: "button" })).toHaveAttribute("aria-checked", "false"),
    );
    const put = spy.mock.calls.find(([, i]) => i?.method === "PUT")!;
    expect(put[0]).toBe("/api/settings/demo-mode");
    expect(JSON.parse(String(put[1]!.body))).toEqual({ enabled: false });
  });

  it("keeps the old state and says so when the save is refused", async () => {
    vi.spyOn(globalThis, "fetch").mockImplementation((_u, init) =>
      Promise.resolve(
        init?.method === "PUT"
          ? json({ detail: "forbidden" }, 403)
          : json({ enabled: true, available: true }),
      ),
    );
    render(<DemoModePanel />);
    fireEvent.click(await screen.findByLabelText("Demo mode", { selector: "button" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("forbidden");
    expect(screen.getByLabelText("Demo mode", { selector: "button" })).toHaveAttribute("aria-checked", "true");
  });

  it("says when no demo content is configured", async () => {
    vi.spyOn(globalThis, "fetch").mockResolvedValue(json({ enabled: false, available: false }));
    render(<DemoModePanel />);
    expect(await screen.findByText("No demo content is configured on this device.")).toBeInTheDocument();
  });
});
