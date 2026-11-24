import { describe, it, expect, vi, afterEach } from "vitest";
import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import { OfflineModelPanel } from "./OfflineModelPanel";

function json(obj: unknown, status = 200) {
  return new Response(JSON.stringify(obj), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

describe("OfflineModelPanel", () => {
  afterEach(() => vi.restoreAllMocks());

  it("renders the switch and reports availability", async () => {
    vi.spyOn(globalThis, "fetch").mockResolvedValue(
      json({
        available: true,
        enabled: false,
        running: false,
        healthy: false,
        model: "LFM3 1.7B (on this phone, offline)",
        licence: "Apache-2.0",
        licence_note: "Freely redistributable.",
        performance_note: "Runs on-device, no network round trip.",
      }),
    );
    render(<OfflineModelPanel />);
    const sw = await screen.findByLabelText(
      /Offline model/,
      { selector: "button" },
    );
    expect(sw).toHaveAttribute("aria-checked", "false");
    expect(screen.getByText("Freely redistributable.")).toBeInTheDocument();
    expect(
      screen.getByText("Runs on-device, no network round trip."),
    ).toBeInTheDocument();
  });

  it("says when the model is not available on this device", async () => {
    vi.spyOn(globalThis, "fetch").mockResolvedValue(
      json({
        available: false,
        enabled: false,
        running: false,
        healthy: false,
        model: "",
        licence: "",
        licence_note: "",
        performance_note: "",
      }),
    );
    render(<OfflineModelPanel />);
    expect(
      await screen.findByText(
        "The offline model is not available on this device.",
      ),
    ).toBeInTheDocument();
  });

  it("PUTs the new value and reflects the server's answer", async () => {
    const spy = vi.spyOn(globalThis, "fetch").mockImplementation((_u, init) =>
      Promise.resolve(
        init?.method === "PUT"
          ? json({
              ok: true,
              status: "disabled",
              enabled: false,
              available: true,
            })
          : json({
              available: true,
              enabled: true,
              running: true,
              healthy: true,
              model: "LFM3 1.7B (on this phone, offline)",
              licence_note: "Freely redistributable.",
              performance_note: "Runs on-device, no network round trip.",
            }),
      ),
    );
    render(<OfflineModelPanel />);
    fireEvent.click(await screen.findByLabelText(/Offline model/, { selector: "button" }));
    await waitFor(() =>
      expect(
        screen.getByLabelText(/Offline model/, { selector: "button" }),
      ).toHaveAttribute("aria-checked", "false"),
    );
    const put = spy.mock.calls.find(([, i]) => i?.method === "PUT")!;
    expect(put[0]).toBe("/api/system/offline-model");
    expect(JSON.parse(String(put[1]!.body))).toEqual({ enabled: false });
  });

  it("keeps the old state and says so when the save is refused", async () => {
    vi.spyOn(globalThis, "fetch").mockImplementation((_u, init) =>
      Promise.resolve(
        init?.method === "PUT"
          ? json({ detail: "forbidden" }, 403)
          : json({
              available: true,
              enabled: true,
              running: true,
              healthy: true,
              model: "LFM3 1.7B (on this phone, offline)",
            }),
      ),
    );
    render(<OfflineModelPanel />);
    fireEvent.click(await screen.findByLabelText(/Offline model/, { selector: "button" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("forbidden");
    expect(
      screen.getByLabelText(/Offline model/, { selector: "button" }),
    ).toHaveAttribute("aria-checked", "true");
  });
});
