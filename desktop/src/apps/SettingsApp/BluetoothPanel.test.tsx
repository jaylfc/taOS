import { describe, it, expect, vi, afterEach } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import { BluetoothPanel } from "./BluetoothPanel";

function json(obj: unknown, status = 200) {
  return new Response(JSON.stringify(obj), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

describe("BluetoothPanel", () => {
  afterEach(() => vi.restoreAllMocks());

  it("shows a connected named device and an unnamed paired one (shows the address)", async () => {
    vi.spyOn(globalThis, "fetch").mockResolvedValue(
      json({
        available: true,
        adapters: [{ path: "/org/bluez/hci0", address: "AA:BB:CC:DD:EE:FF", powered: true }],
        devices: [
          { path: "/org/bluez/hci0/dev_11_22_33_44_55_66", address: "11:22:33:44:55:66", name: "Headphones", paired: true, connected: true, trusted: true },
          { path: "/org/bluez/hci0/dev_AA_BB_CC_DD_EE_FF", address: "AA:BB:CC:DD:EE:FF", name: null, paired: true, connected: false, trusted: false },
        ],
      })
    );
    render(<BluetoothPanel />);
    await waitFor(() => {
      expect(screen.getByText("Headphones")).toBeInTheDocument();
    });
    expect(screen.getByText("AA:BB:CC:DD:EE:FF")).toBeInTheDocument();
    expect(screen.getByText("Connected")).toBeInTheDocument();
    expect(screen.getByText("Paired")).toBeInTheDocument();
  });

  it("available=false shows the BlueZ text", async () => {
    vi.spyOn(globalThis, "fetch").mockResolvedValue(
      json({ available: false, adapters: [], devices: [] })
    );
    render(<BluetoothPanel />);
    expect(await screen.findByText("Bluetooth controls need BlueZ, which is not running on this device.")).toBeInTheDocument();
  });

  it("adapters empty text", async () => {
    vi.spyOn(globalThis, "fetch").mockResolvedValue(
      json({ available: true, adapters: [], devices: [] })
    );
    render(<BluetoothPanel />);
    expect(await screen.findByText("No Bluetooth adapter found.")).toBeInTheDocument();
  });

  it("adapter powered=false text", async () => {
    vi.spyOn(globalThis, "fetch").mockResolvedValue(
      json({ available: true, adapters: [{ path: "/org/bluez/hci0", address: "AA:BB:CC:DD:EE:FF", powered: false }], devices: [] })
    );
    render(<BluetoothPanel />);
    expect(await screen.findByText("Bluetooth is turned off.")).toBeInTheDocument();
  });

  it("a 403 shows the role=alert error", async () => {
    vi.spyOn(globalThis, "fetch").mockResolvedValue(json({ detail: "forbidden" }, 403));
    render(<BluetoothPanel />);
    expect(await screen.findByRole("alert")).toBeInTheDocument();
  });
});
