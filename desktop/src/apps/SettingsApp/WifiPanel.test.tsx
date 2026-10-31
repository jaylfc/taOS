import { describe, it, expect, vi, afterEach } from "vitest";
import { render, screen, waitFor, act } from "@testing-library/react";
import { WifiPanel } from "./WifiPanel";
import { StrictMode } from "react";

function json(obj: unknown, status = 200) {
  return new Response(JSON.stringify(obj), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

describe("WifiPanel", () => {
  afterEach(() => vi.restoreAllMocks());

  it("lists two access points sorted as served, shows Connected on the active one and Signal 80 percent label", async () => {
    vi.spyOn(globalThis, "fetch").mockResolvedValue(
      json({
        available: true,
        wifi_enabled: true,
        devices: [{ path: "/org/freedesktop/NetworkManager/Devices/1", iface: "wlan0", state: 100 }],
        access_points: [
          { ssid: "HomeNetwork", strength: 80, frequency: 2437, secured: true, active: true },
          { ssid: "GuestNetwork", strength: 45, frequency: 2462, secured: true, active: false },
        ],
      })
    );
    render(<WifiPanel />);
    await waitFor(() => {
      expect(screen.getByText("HomeNetwork")).toBeInTheDocument();
      expect(screen.getByText("GuestNetwork")).toBeInTheDocument();
    });
    expect(screen.getByText("Connected")).toBeInTheDocument();
    expect(screen.getByText("80%")).toBeInTheDocument();
    expect(screen.getByLabelText("Signal 80 percent")).toBeInTheDocument();
  });

  it("available=false shows the NetworkManager text", async () => {
    vi.spyOn(globalThis, "fetch").mockResolvedValue(
      json({ available: false, wifi_enabled: null, devices: [], access_points: [] })
    );
    render(<WifiPanel />);
    expect(await screen.findByText("Wi-Fi controls need NetworkManager, which is not running on this device.")).toBeInTheDocument();
  });

  it("wifi_enabled=false shows Wi-Fi is turned off.", async () => {
    vi.spyOn(globalThis, "fetch").mockResolvedValue(
      json({ available: true, wifi_enabled: false, devices: [], access_points: [] })
    );
    render(<WifiPanel />);
    expect(await screen.findByText("Wi-Fi is turned off.")).toBeInTheDocument();
  });

  it("a 403 response shows the role=alert error and no list", async () => {
    vi.spyOn(globalThis, "fetch").mockResolvedValue(json({ detail: "forbidden" }, 403));
    render(<WifiPanel />);
    expect(await screen.findByRole("alert")).toHaveTextContent("Could not load Wi-Fi state.");
    expect(screen.queryByRole("list", { name: "Wi-Fi networks" })).not.toBeInTheDocument();
  });

it("refreshes access points every 10s and clears interval on unmount", async () => {
    vi.useFakeTimers({ toFake: ["setInterval", "clearInterval"] });
    const fetchSpy = vi.spyOn(globalThis, "fetch");
    fetchSpy
      .mockResolvedValueOnce(
        json({
          available: true,
          wifi_enabled: true,
          devices: [],
          access_points: [{ ssid: "FirstAP", strength: 50, frequency: 2412, secured: true, active: false }],
        })
      )
      .mockResolvedValueOnce(
        json({
          available: true,
          wifi_enabled: true,
          devices: [],
          access_points: [{ ssid: "SecondAP", strength: 75, frequency: 2437, secured: true, active: true }],
        })
      );
    const { unmount } = render(<WifiPanel />);
    await waitFor(() => expect(screen.getByText("FirstAP")).toBeInTheDocument(), { timeout: 3000 });
    expect(fetchSpy).toHaveBeenCalledTimes(1);
    await act(async () => {
      vi.advanceTimersByTime(10_000);
    });
    await waitFor(() => expect(screen.getByText("SecondAP")).toBeInTheDocument(), { timeout: 3000 });
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
    render(<WifiPanel />);
    // Advance timers to trigger the interval (second fetch)
    await act(async () => {
      vi.advanceTimersByTime(10_000);
    });
    // Now resolve the second fetch first (newer load)
    await act(async () => {
      resolveSecond!(json({ available: true, wifi_enabled: true, access_points: [{ ssid: "SecondAP", strength: 90, frequency: 2437, secured: true, active: true }] }));
    });
    await waitFor(() => expect(screen.getByText("SecondAP")).toBeInTheDocument());
    // Then resolve the first fetch (older load)
    await act(async () => {
      resolveFirst!(json({ available: true, wifi_enabled: true, access_points: [{ ssid: "FirstAP", strength: 50, frequency: 2412, secured: true, active: false }] }));
    });
    expect(screen.getByText("SecondAP")).toBeInTheDocument();
    expect(screen.queryByText("FirstAP")).not.toBeInTheDocument();
    vi.useRealTimers();
  });
});

describe("WifiPanel StrictMode", () => {
  it("loads access points even when mounted remounts under StrictMode", async () => {
    vi.spyOn(globalThis, "fetch").mockResolvedValue(
      json({
        available: true,
        wifi_enabled: true,
        access_points: [
          { ssid: "TestNetwork", strength: 75, frequency: 2437, secured: true, active: true },
        ],
      })
    );
    render(<StrictMode><WifiPanel /></StrictMode>);
    await waitFor(() => {
      expect(screen.getByText("TestNetwork")).toBeInTheDocument();
    });
  });
});