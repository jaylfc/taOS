import { describe, it, expect, vi, afterEach } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import { WifiPanel } from "./WifiPanel";

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
});