/**
 * ClusterApp — Bluetooth device pairing tab in the "Add device" modal.
 *
 * Covers the four flows called out in the spec: the controller reporting
 * Bluetooth unavailable (503), a completed scan with no devices, a 409
 * "not pairable" error from pair/start, and the happy path through
 * scan -> pair/start -> pair/confirm.
 */
import { render, screen, fireEvent, waitFor, act } from "@testing-library/react";
import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { ClusterApp } from "../ClusterApp";

const WORKERS_RESPONSE = [
  {
    name: "existing-worker",
    url: "http://10.0.0.5:8080",
    status: "online",
    last_heartbeat: Date.now() / 1000,
    hardware: {},
    backends: [],
    capabilities: [],
  },
];

function jsonResponse(body: unknown, status = 200) {
  return Promise.resolve({
    ok: status >= 200 && status < 300,
    status,
    headers: new Headers({ "content-type": "application/json" }),
    json: () => Promise.resolve(body),
  } as unknown as Response);
}

async function openAddDeviceModal() {
  render(<ClusterApp windowId="test" />);
  await act(async () => {
    await new Promise((r) => setTimeout(r, 0));
  });
  fireEvent.click(await screen.findByRole("button", { name: "Add a device" }));
  // Radix's TabsTrigger switches tabs from onMouseDown, not onClick, so
  // fireEvent.click alone (no preceding mousedown) never activates it.
  fireEvent.mouseDown(await screen.findByRole("tab", { name: "Bluetooth" }), { button: 0 });
}

describe("ClusterApp — Bluetooth pairing", () => {
  afterEach(() => {
    vi.restoreAllMocks();
  });

  it("shows a clear empty state when the controller reports bluetooth unavailable (503)", async () => {
    globalThis.fetch = vi.fn().mockImplementation((input: string) => {
      const url = String(input);
      if (url.startsWith("/api/cluster/workers")) return jsonResponse(WORKERS_RESPONSE);
      if (url.startsWith("/api/cluster/ble/scan")) return jsonResponse({ error: "bluetooth_unavailable" }, 503);
      return jsonResponse({}, 404);
    }) as typeof fetch;

    await openAddDeviceModal();
    fireEvent.click(screen.getByRole("button", { name: "Search" }));

    expect(
      await screen.findByText("Bluetooth isn't available on this controller."),
    ).toBeInTheDocument();
  });

  it("shows the no-devices-found empty state with a search-again button", async () => {
    globalThis.fetch = vi.fn().mockImplementation((input: string) => {
      const url = String(input);
      if (url.startsWith("/api/cluster/workers")) return jsonResponse(WORKERS_RESPONSE);
      if (url.startsWith("/api/cluster/ble/scan")) return jsonResponse({ devices: [] });
      return jsonResponse({}, 404);
    }) as typeof fetch;

    await openAddDeviceModal();
    fireEvent.click(screen.getByRole("button", { name: "Search" }));

    expect(
      await screen.findByText(/No taOS devices found\. Power the device on/),
    ).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Search again" })).toBeInTheDocument();
  });

  it("shows the 409 'not pairable' error when pairing starts on a device that isn't in pairing mode", async () => {
    globalThis.fetch = vi.fn().mockImplementation((input: string, init?: RequestInit) => {
      const url = String(input);
      if (url.startsWith("/api/cluster/workers")) return jsonResponse(WORKERS_RESPONSE);
      if (url.startsWith("/api/cluster/ble/scan")) {
        return jsonResponse({
          devices: [
            { address: "AA:BB:CC:DD:EE:FF", name: "taOSusb-JLJ6", board_id: "jlj6", state: "unpaired", pairable: true, rssi: -55 },
          ],
        });
      }
      if (url === "/api/cluster/ble/pair/start" && init?.method === "POST") {
        return jsonResponse({ error: "not_pairable" }, 409);
      }
      return jsonResponse({}, 404);
    }) as typeof fetch;

    await openAddDeviceModal();
    fireEvent.click(screen.getByRole("button", { name: "Search" }));

    const pairBtn = await screen.findByRole("button", { name: "Pair with taOSusb-JLJ6" });
    fireEvent.click(pairBtn);

    expect(
      await screen.findByText(
        "This device isn't in pairing mode. Power-cycle it and try within 5 minutes.",
      ),
    ).toBeInTheDocument();
  });

  it("completes the happy path: scan, pair/start shows the code, pair/confirm closes the modal", async () => {
    const fetchMock = vi.fn().mockImplementation((input: string, init?: RequestInit) => {
      const url = String(input);
      if (url.startsWith("/api/cluster/workers")) return jsonResponse(WORKERS_RESPONSE);
      if (url.startsWith("/api/cluster/ble/scan")) {
        return jsonResponse({
          devices: [
            { address: "AA:BB:CC:DD:EE:FF", name: "taOSusb-JLJ6", board_id: "jlj6", state: "unpaired", pairable: true, rssi: -55 },
          ],
        });
      }
      if (url === "/api/cluster/ble/pair/start" && init?.method === "POST") {
        return jsonResponse({ session: "sess-1", code: "123456", board_id: "jlj6", name: "taOSusb-JLJ6" });
      }
      if (url === "/api/cluster/ble/pair/confirm" && init?.method === "POST") {
        return jsonResponse({ node: { name: "taOSusb-JLJ6", kind: "device", board_id: "jlj6" } });
      }
      return jsonResponse({}, 404);
    });
    globalThis.fetch = fetchMock as unknown as typeof fetch;

    await openAddDeviceModal();
    fireEvent.click(screen.getByRole("button", { name: "Search" }));

    const pairBtn = await screen.findByRole("button", { name: "Pair with taOSusb-JLJ6" });
    fireEvent.click(pairBtn);

    // Code appears, split 3+3, with an aria-label reading the digits.
    const codeEl = await screen.findByLabelText("Pairing code 1 2 3 4 5 6");
    expect(codeEl).toBeInTheDocument();
    expect(screen.getByText("123 456")).toBeInTheDocument();
    expect(
      screen.getByText(/Pairing with taOSusb-JLJ6\. If the device has a screen/),
    ).toBeInTheDocument();

    // Focus should move to the code element when it appears.
    await waitFor(() => expect(codeEl).toHaveFocus());

    fireEvent.click(screen.getByRole("button", { name: "Pair" }));

    await waitFor(() => {
      expect(fetchMock).toHaveBeenCalledWith(
        "/api/cluster/ble/pair/confirm",
        expect.objectContaining({ method: "POST" }),
      );
    });

    // Modal closes on success.
    await waitFor(() => {
      expect(screen.queryByRole("dialog", { name: "Add a device" })).not.toBeInTheDocument();
    });
  });
});
