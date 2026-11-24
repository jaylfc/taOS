import { describe, it, expect, vi, afterEach } from "vitest";
import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import { PerformancePanel, PERFORMANCE_CAPTION } from "./PerformancePanel";

function json(obj: unknown, status = 200) {
  return new Response(JSON.stringify(obj), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

describe("PerformancePanel", () => {
  afterEach(() => vi.restoreAllMocks());

  it("renders the switch with its caption", async () => {
    vi.spyOn(globalThis, "fetch").mockResolvedValue(
      json({ supported: true, installed: true, enabled: true, active: true }),
    );
    render(<PerformancePanel />);
    const sw = await screen.findByLabelText("Performance mode", { selector: "button" });
    expect(sw).toHaveAttribute("aria-checked", "true");
    expect(screen.getByText(PERFORMANCE_CAPTION)).toBeInTheDocument();
    expect(PERFORMANCE_CAPTION).toContain("RK3588");
  });

  it("PUTs the new value and reflects the server's answer", async () => {
    const spy = vi.spyOn(globalThis, "fetch").mockImplementation((_u, init) =>
      Promise.resolve(
        init?.method === "PUT"
          ? json({ ok: true, supported: true, installed: true, enabled: false, active: false })
          : json({ supported: true, installed: true, enabled: true, active: true }),
      ),
    );
    render(<PerformancePanel />);
    fireEvent.click(await screen.findByLabelText("Performance mode", { selector: "button" }));
    await waitFor(() =>
      expect(screen.getByLabelText("Performance mode", { selector: "button" })).toHaveAttribute("aria-checked", "false"),
    );
    const put = spy.mock.calls.find(([, i]) => i?.method === "PUT")!;
    expect(put[0]).toBe("/api/settings/rk3588-performance");
    expect(JSON.parse(String(put[1]!.body))).toEqual({ enabled: false });
  });

  it("keeps the old state and says so when the save is refused", async () => {
    vi.spyOn(globalThis, "fetch").mockImplementation((_u, init) =>
      Promise.resolve(
        init?.method === "PUT"
          ? json({ error: "systemctl disable taos-rk3588-perf.service failed: boom" }, 502)
          : json({ supported: true, installed: true, enabled: true, active: true }),
      ),
    );
    render(<PerformancePanel />);
    fireEvent.click(await screen.findByLabelText("Performance mode", { selector: "button" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("boom");
    expect(screen.getByLabelText("Performance mode", { selector: "button" })).toHaveAttribute("aria-checked", "true");
  });

  it("says when the perf service is not installed and disables the switch", async () => {
    vi.spyOn(globalThis, "fetch").mockResolvedValue(
      json({ supported: true, installed: false, enabled: false, active: false }),
    );
    render(<PerformancePanel />);
    expect(await screen.findByText(/performance service is not installed/i)).toBeInTheDocument();
    expect(screen.getByLabelText("Performance mode", { selector: "button" })).toBeDisabled();
  });
});
