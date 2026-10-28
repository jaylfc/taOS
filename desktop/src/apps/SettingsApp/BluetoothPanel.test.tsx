import { describe, it, expect, vi, afterEach } from "vitest";
import { render, screen, waitFor, act } from "@testing-library/react";
import { BluetoothPanel } from "./BluetoothPanel";
import { StrictMode } from "react";

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

  it("refreshes devices every 10s and clears interval on unmount", async () => {
    vi.useFakeTimers({ toFake: ["setInterval", "clearInterval"] });
    const fetchSpy = vi.spyOn(globalThis, "fetch");
    fetchSpy
      .mockResolvedValueOnce(
        json({
          available: true,
          adapters: [{ path: "/org/bluez/hci0", address: "AA:BB:CC:DD:EE:FF", powered: true }],
          devices: [{ path: "/org/bluez/hci0/dev_11_22_33_44_55_66", address: "11:22:33:44:55:66", name: "FirstDevice", paired: true, connected: true, trusted: true }],
        })
      )
      .mockResolvedValueOnce(
        json({
          available: true,
          adapters: [{ path: "/org/bluez/hci0", address: "AA:BB:CC:DD:EE:FF", powered: true }],
          devices: [{ path: "/org/bluez/hci0/dev_AA_BB_CC_DD_EE_FF", address: "AA:BB:CC:DD:EE:FF", name: "SecondDevice", paired: true, connected: true, trusted: true }],
        })
      );
    const { unmount } = render(<BluetoothPanel />);
    await waitFor(() => expect(screen.getByText("FirstDevice")).toBeInTheDocument(), { timeout: 3000 });
    expect(fetchSpy).toHaveBeenCalledTimes(1);
    await act(async () => {
      vi.advanceTimersByTime(10_000);
    });
    await waitFor(() => expect(screen.getByText("SecondDevice")).toBeInTheDocument(), { timeout: 3000 });
    expect(fetchSpy).toHaveBeenCalledTimes(2);
    unmount();
    await act(async () => {
      vi.advanceTimersByTime(10_000);
    });
    expect(fetchSpy).toHaveBeenCalledTimes(2);
    vi.useRealTimers();
  }, 15000);

  it("ignores stale response when a newer load starts before the first resolves", async () => {
    vi.useFakeTimers({ toFake: ["setInterval", "clearInterval"] });
    let resolveFirst: (value: Response) => void;
    let resolveSecond: (value: Response) => void;
    const firstPromise = new Promise<Response>((resolve) => {
      resolveFirst = resolve;
    });
    const secondPromise = new Promise<Response>((resolve) => {
      resolveSecond = resolve;
    });
    const fetchSpy = vi.spyOn(globalThis, "fetch");
    fetchSpy.mockReturnValueOnce(firstPromise);
    fetchSpy.mockReturnValueOnce(secondPromise);
    render(<BluetoothPanel />);
    // Advance timers to trigger the interval (second fetch)
    await act(async () => {
      vi.advanceTimersByTime(10_000);
    });
    // Now resolve the second fetch first (newer load)
    await act(async () => {
      resolveSecond!(json({ available: true, adapters: [{ path: "/org/bluez/hci0", address: "AA:BB:CC:DD:EE:FF", powered: true }], devices: [{ path: "/org/bluez/hci0/dev_AA_BB_CC_DD_EE_FF", address: "AA:BB:CC:DD:EE:FF", name: "SecondDevice", paired: true, connected: true, trusted: true }] }));
    });
    await waitFor(() => expect(screen.getByText("SecondDevice")).toBeInTheDocument());
    // Then resolve the first fetch (older load)
    await act(async () => {
      resolveFirst!(json({ available: true, adapters: [{ path: "/org/bluez/hci0", address: "AA:BB:CC:DD:EE:FF", powered: true }], devices: [{ path: "/org/bluez/hci0/dev_11_22_33_44_55_66", address: "11:22:33:44:55:66", name: "FirstDevice", paired: true, connected: true, trusted: true }] }));
    });
    expect(screen.getByText("SecondDevice")).toBeInTheDocument();
    expect(screen.queryByText("FirstDevice")).not.toBeInTheDocument();
    vi.useRealTimers();
  });
});

describe("BluetoothPanel StrictMode", () => {
  it("loads devices even when mounted remounts under StrictMode", async () => {
    vi.spyOn(globalThis, "fetch").mockResolvedValue(
      json({
        available: true,
        adapters: [{ path: "/org/bluez/hci0", address: "AA:BB:CC:DD:EE:FF", powered: true }],
        devices: [{ path: "/org/bluez/hci0/dev_11_22_33_44_55_66", address: "11:22:33:44:55:66", name: "TestDevice", paired: true, connected: true, trusted: true }],
      })
    );
    render(<StrictMode><BluetoothPanel /></StrictMode>);
    await waitFor(() => {
      expect(screen.getByText("TestDevice")).toBeInTheDocument();
    });
  });
});
