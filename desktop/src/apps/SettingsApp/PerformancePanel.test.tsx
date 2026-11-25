import { describe, it, expect, vi, afterEach } from "vitest";
import { StrictMode } from "react";
import { render, screen, fireEvent, waitFor, act } from "@testing-library/react";
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
          ? json({ error: "systemctl stop taos-rk3588-perf.service failed: boom" }, 502)
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

  it("loads under React StrictMode instead of sticking in its loading state", async () => {
    // StrictMode runs setup -> cleanup -> setup in dev; the mounted flag
    // must be restored by the second setup or the load completion is
    // dropped and the switch never renders.
    vi.spyOn(globalThis, "fetch").mockResolvedValue(
      json({ supported: true, installed: true, enabled: true, active: true }),
    );
    render(
      <StrictMode>
        <PerformancePanel />
      </StrictMode>,
    );
    const sw = await screen.findByLabelText("Performance mode", { selector: "button" });
    expect(sw).toHaveAttribute("aria-checked", "true");
  });

  it("ignores a superseded load response under StrictMode", async () => {
    // StrictMode runs the load effect twice; both requests end up in
    // flight. The first belongs to the already-unmounted first mount,
    // but the shared mounted flag is true again by the time it
    // resolves, so only a per-effect cancellation flag can keep its
    // stale value from overwriting newer state - here, a toggle the
    // user made after the second load landed.
    let resolveFirst: (r: Response) => void = () => {};
    let gets = 0;
    vi.spyOn(globalThis, "fetch").mockImplementation((_u, init) => {
      if (init?.method === "PUT") {
        return Promise.resolve(
          json({ ok: true, supported: true, installed: true, enabled: false, active: false }),
        );
      }
      gets += 1;
      if (gets === 1) {
        return new Promise<Response>((res) => { resolveFirst = res; });
      }
      return Promise.resolve(
        json({ supported: true, installed: true, enabled: true, active: true }),
      );
    });
    render(
      <StrictMode>
        <PerformancePanel />
      </StrictMode>,
    );
    const sw = await screen.findByLabelText("Performance mode", { selector: "button" });
    expect(sw).toHaveAttribute("aria-checked", "true");
    fireEvent.click(sw);
    await waitFor(() =>
      expect(screen.getByLabelText("Performance mode", { selector: "button" })).toHaveAttribute("aria-checked", "false"),
    );
    // The superseded first load finally resolves, carrying the
    // pre-toggle value. It must be ignored.
    await act(async () => {
      resolveFirst(json({ supported: true, installed: true, enabled: true, active: true }));
    });
    expect(screen.getByLabelText("Performance mode", { selector: "button" })).toHaveAttribute("aria-checked", "false");
  });

  it("re-reads the real state after a failed save instead of keeping the stale one", async () => {
    // The toggle starts/stops the unit before it persists the flag,
    // so a failure can leave the shown state behind the real one. The
    // switch must settle on the re-read state, not the pre-save one.
    let gets = 0;
    vi.spyOn(globalThis, "fetch").mockImplementation((_u, init) => {
      if (init?.method === "PUT") {
        return Promise.resolve(
          json({ error: "systemctl start taos-rk3588-perf.service failed: start boom" }, 502),
        );
      }
      gets += 1;
      return Promise.resolve(
        json({ supported: true, installed: true, enabled: gets > 1, active: false }),
      );
    });
    render(<PerformancePanel />);
    fireEvent.click(await screen.findByLabelText("Performance mode", { selector: "button" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("start boom");
    await waitFor(() =>
      expect(screen.getByLabelText("Performance mode", { selector: "button" })).toHaveAttribute("aria-checked", "true"),
    );
  });

  it("drops the switch to unknown when the re-read after a failed save also fails", async () => {
    let gets = 0;
    vi.spyOn(globalThis, "fetch").mockImplementation((_u, init) => {
      if (init?.method === "PUT") {
        return Promise.resolve(
          json({ error: "systemctl start taos-rk3588-perf.service failed: boom" }, 502),
        );
      }
      gets += 1;
      if (gets > 1) return Promise.resolve(json({ error: "nope" }, 500));
      return Promise.resolve(
        json({ supported: true, installed: true, enabled: false, active: false }),
      );
    });
    render(<PerformancePanel />);
    fireEvent.click(await screen.findByLabelText("Performance mode", { selector: "button" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("boom");
    // The stale pre-save value must not stay on screen as if it were
    // current: with the state unknown, the switch unmounts.
    await waitFor(() =>
      expect(screen.queryByLabelText("Performance mode", { selector: "button" })).not.toBeInTheDocument(),
    );
  });
});
