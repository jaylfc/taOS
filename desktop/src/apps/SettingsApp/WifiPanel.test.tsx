import { describe, it, expect, vi, afterEach } from "vitest";
import { render, screen, waitFor, act, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
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
    await act(async () => {
      vi.advanceTimersByTime(10_000);
    });
    await act(async () => {
      resolveSecond!(json({ available: true, wifi_enabled: true, access_points: [{ ssid: "SecondAP", strength: 90, frequency: 2437, secured: true, active: true }] }));
    });
    await waitFor(() => expect(screen.getByText("SecondAP")).toBeInTheDocument());
    await act(async () => {
      resolveFirst!(json({ available: true, wifi_enabled: true, access_points: [{ ssid: "FirstAP", strength: 50, frequency: 2412, secured: true, active: false }] }));
    });
    expect(screen.getByText("SecondAP")).toBeInTheDocument();
    expect(screen.queryByText("FirstAP")).not.toBeInTheDocument();
    vi.useRealTimers();
  });
});

describe("WifiPanel controls", () => {
  afterEach(() => vi.restoreAllMocks());

  const baseResponse = {
    available: true,
    wifi_enabled: true,
    devices: [{ path: "/org/freedesktop/NetworkManager/Devices/1", iface: "wlan0", state: 100 }],
    access_points: [
      { ssid: "ConnectedNet", strength: 80, frequency: 2437, secured: true, active: true },
      { ssid: "OpenNet", strength: 60, frequency: 2462, secured: false, active: false },
      { ssid: "SecuredNet", strength: 70, frequency: 2412, secured: true, active: false },
    ],
  };

  it("shows Wi-Fi toggle with aria-label and reflects wifi_enabled state", async () => {
    vi.spyOn(globalThis, "fetch").mockResolvedValue(json(baseResponse));
    render(<WifiPanel />);
    await waitFor(() => expect(screen.getByText("ConnectedNet")).toBeInTheDocument());
    const toggle = screen.getByRole("switch", { name: "Wi-Fi" });
    expect(toggle).toBeChecked();
    expect(toggle).toHaveAttribute("aria-label", "Wi-Fi");
  });

  it("toggle off posts {enabled:false} and reloads", async () => {
    const fetchSpy = vi.spyOn(globalThis, "fetch");
    fetchSpy
      .mockResolvedValueOnce(json(baseResponse))
      .mockResolvedValueOnce(json({ ok: true, wifi_enabled: false }))
      .mockResolvedValueOnce(json({ ...baseResponse, wifi_enabled: false }));
    render(<WifiPanel />);
    await waitFor(() => expect(screen.getByText("ConnectedNet")).toBeInTheDocument());
    const toggle = screen.getByRole("switch", { name: "Wi-Fi" });
    await act(async () => {
      await userEvent.click(toggle);
    });
    expect(fetchSpy).toHaveBeenCalledTimes(3);
    const [, toggleCall] = fetchSpy.mock.calls;
    expect(toggleCall[0]).toBe("/api/hardware/wifi/enabled");
    expect(JSON.parse(toggleCall[1]?.body as string)).toEqual({ enabled: false });
  });

  it("Disconnect button on connected row posts {iface} once and reloads", async () => {
    const fetchSpy = vi.spyOn(globalThis, "fetch");
    fetchSpy
      .mockResolvedValueOnce(json(baseResponse))
      .mockResolvedValueOnce(json({ ok: true }))
      .mockResolvedValueOnce(json({ ...baseResponse, access_points: baseResponse.access_points.map(ap => ({ ...ap, active: false })) }));
    render(<WifiPanel />);
    await waitFor(() => expect(screen.getByText("ConnectedNet")).toBeInTheDocument());
    const disconnectBtn = screen.getByRole("button", { name: /Disconnect from ConnectedNet/i });
    await act(async () => {
      await userEvent.click(disconnectBtn);
    });
    expect(fetchSpy).toHaveBeenCalledTimes(3);
    const [, disconnectCall] = fetchSpy.mock.calls;
    expect(disconnectCall[0]).toBe("/api/hardware/wifi/disconnect");
    expect(JSON.parse(disconnectCall[1]?.body as string)).toEqual({ iface: "wlan0" });
  });

  it("Join open network posts connect with ssid only (no psk) and reloads", async () => {
    const fetchSpy = vi.spyOn(globalThis, "fetch");
    fetchSpy
      .mockResolvedValueOnce(json(baseResponse))
      .mockResolvedValueOnce(json({ ok: true }))
      .mockResolvedValueOnce(json({ ...baseResponse, access_points: baseResponse.access_points.map(ap => ({ ...ap, active: ap.ssid === "OpenNet" })) }));
    render(<WifiPanel />);
    await waitFor(() => expect(screen.getByText("OpenNet")).toBeInTheDocument());
    const joinBtn = screen.getByRole("button", { name: /Join OpenNet/i });
    await act(async () => {
      await userEvent.click(joinBtn);
    });
    expect(fetchSpy).toHaveBeenCalledTimes(3);
    const [, connectCall] = fetchSpy.mock.calls;
    expect(connectCall[0]).toBe("/api/hardware/wifi/connect");
    const body = JSON.parse(connectCall[1]?.body as string);
    expect(body).toEqual({ ssid: "OpenNet" });
    expect(body.psk).toBeUndefined();
  });

  it("keyboard-only join secured network: Tab to Join, Enter, type password, Enter posts {ssid, psk} once", async () => {
    const fetchSpy = vi.spyOn(globalThis, "fetch");
    fetchSpy
      .mockResolvedValueOnce(json(baseResponse))
      .mockResolvedValueOnce(json({ ok: true }))
      .mockResolvedValueOnce(json({ ...baseResponse, access_points: baseResponse.access_points.map(ap => ({ ...ap, active: ap.ssid === "SecuredNet" })) }));
    render(<WifiPanel />);
    await waitFor(() => expect(screen.getByText("SecuredNet")).toBeInTheDocument());
    const joinBtn = screen.getByRole("button", { name: /Join SecuredNet \(secured\)/i });
    await act(async () => {
      await userEvent.tab();
      await userEvent.tab();
      await userEvent.tab();
      await userEvent.tab();
      await userEvent.keyboard("{Enter}");
    });
    await waitFor(() => {
      const input = screen.getByLabelText("Password for SecuredNet");
      expect(input).toBeInTheDocument();
      expect(input).toHaveFocus();
    });
    await act(async () => {
      await userEvent.type(screen.getByLabelText("Password for SecuredNet"), "secret123");
      await userEvent.keyboard("{Enter}");
    });
    expect(fetchSpy).toHaveBeenCalledTimes(3);
    const [, connectCall] = fetchSpy.mock.calls;
    expect(connectCall[0]).toBe("/api/hardware/wifi/connect");
    expect(JSON.parse(connectCall[1]?.body as string)).toEqual({ ssid: "SecuredNet", psk: "secret123" });
  });

  it("Esc cancels inline password with no POST", async () => {
    const fetchSpy = vi.spyOn(globalThis, "fetch");
    fetchSpy.mockResolvedValueOnce(json(baseResponse));
    render(<WifiPanel />);
    await waitFor(() => expect(screen.getByText("SecuredNet")).toBeInTheDocument());
    const joinBtn = screen.getByRole("button", { name: /Join SecuredNet \(secured\)/i });
    await act(async () => {
      await userEvent.tab();
      await userEvent.tab();
      await userEvent.tab();
      await userEvent.tab();
      await userEvent.keyboard("{Enter}");
    });
    await waitFor(() => {
      expect(screen.getByLabelText("Password for SecuredNet")).toBeInTheDocument();
    });
    await act(async () => {
      await userEvent.keyboard("{Escape}");
    });
    expect(screen.queryByLabelText("Password for SecuredNet")).not.toBeInTheDocument();
    expect(fetchSpy).toHaveBeenCalledTimes(1);
  });

  it("show/hide password toggle works", async () => {
    vi.spyOn(globalThis, "fetch").mockResolvedValue(json(baseResponse));
    render(<WifiPanel />);
    await waitFor(() => expect(screen.getByText("SecuredNet")).toBeInTheDocument());
    const joinBtn = screen.getByRole("button", { name: /Join SecuredNet \(secured\)/i });
    await act(async () => {
      await userEvent.click(joinBtn);
    });
    await waitFor(() => {
      const input = screen.getByLabelText("Password for SecuredNet");
      expect(input).toHaveAttribute("type", "password");
    });
    const showBtn = screen.getByRole("button", { name: "Show password" });
    await act(async () => {
      await userEvent.click(showBtn);
    });
    expect(screen.getByLabelText("Password for SecuredNet")).toHaveAttribute("type", "text");
    const hideBtn = screen.getByRole("button", { name: "Hide password" });
    await act(async () => {
      await userEvent.click(hideBtn);
    });
    expect(screen.getByLabelText("Password for SecuredNet")).toHaveAttribute("type", "password");
  });

  it("403 not_permitted connect shows role=alert error", async () => {
    const fetchSpy = vi.spyOn(globalThis, "fetch");
    fetchSpy
      .mockResolvedValueOnce(json(baseResponse))
      .mockResolvedValueOnce(json({ error: "not_permitted" }, 403));
    render(<WifiPanel />);
    await waitFor(() => expect(screen.getByText("SecuredNet")).toBeInTheDocument());
    const joinBtn = screen.getByRole("button", { name: /Join SecuredNet \(secured\)/i });
    await act(async () => {
      await userEvent.click(joinBtn);
    });
    let input: HTMLInputElement;
    await waitFor(() => {
      input = screen.getByLabelText("Password for SecuredNet");
      expect(input).toBeInTheDocument();
    });
    await act(async () => {
      await userEvent.type(input!, "secret123");
      await userEvent.keyboard("{Enter}");
    });
    expect(await screen.findByRole("alert")).toHaveTextContent(/Connection failed: not_permitted/);
  });

  it("other connect errors show inline password error, not global alert", async () => {
    const fetchSpy = vi.spyOn(globalThis, "fetch");
    fetchSpy
      .mockResolvedValueOnce(json(baseResponse))
      .mockResolvedValueOnce(json({ error: "invalid_psk" }, 422));
    render(<WifiPanel />);
    await waitFor(() => expect(screen.getByText("SecuredNet")).toBeInTheDocument());
    const joinBtn = screen.getByRole("button", { name: /Join SecuredNet \(secured\)/i });
    await act(async () => {
      await userEvent.click(joinBtn);
    });
    let input: HTMLInputElement;
    await waitFor(() => {
      input = screen.getByLabelText("Password for SecuredNet");
      expect(input).toBeInTheDocument();
    });
    await act(async () => {
      await userEvent.type(input!, "wrong");
      await userEvent.keyboard("{Enter}");
    });
    expect(screen.getByText("Connection failed: invalid_psk")).toBeInTheDocument();
    const globalAlert = screen.queryByRole("alert");
    expect(globalAlert).toBeNull();
  });

  it("in-flight status announced via aria-live polite", async () => {
    let resolveConnect: (value: Response) => void;
    const connectPromise = new Promise<Response>((resolve) => {
      resolveConnect = resolve;
    });
    const fetchSpy = vi.spyOn(globalThis, "fetch");
    fetchSpy
      .mockResolvedValueOnce(json(baseResponse))
      .mockReturnValueOnce(connectPromise);
    render(<WifiPanel />);
    await waitFor(() => expect(screen.getByText("OpenNet")).toBeInTheDocument());
    const joinBtn = screen.getByRole("button", { name: /Join OpenNet/i });
    await act(async () => {
      await userEvent.click(joinBtn);
    });
    expect(screen.getByText(/Joining OpenNet/)).toBeInTheDocument();
    await act(async () => {
      resolveConnect!(json({ ok: true }));
    });
    await waitFor(() => expect(screen.queryByText(/Joining OpenNet/)).not.toBeInTheDocument());
  });

  it("password field cleared after successful connect", async () => {
    const fetchSpy = vi.spyOn(globalThis, "fetch");
    fetchSpy
      .mockResolvedValueOnce(json(baseResponse))
      .mockResolvedValueOnce(json({ ok: true }))
      .mockResolvedValueOnce(json({ ...baseResponse, access_points: baseResponse.access_points.map(ap => ({ ...ap, active: ap.ssid === "SecuredNet" })) }));
    render(<WifiPanel />);
    await waitFor(() => expect(screen.getByText("SecuredNet")).toBeInTheDocument());
    const joinBtn = screen.getByRole("button", { name: /Join SecuredNet \(secured\)/i });
    await act(async () => {
      await userEvent.click(joinBtn);
    });
    let input: HTMLInputElement;
    await waitFor(() => {
      input = screen.getByLabelText("Password for SecuredNet");
      expect(input).toBeInTheDocument();
    });
    await act(async () => {
      await userEvent.type(input!, "secret123");
      await userEvent.keyboard("{Enter}");
    });
    await waitFor(() => expect(screen.queryByLabelText("Password for SecuredNet")).not.toBeInTheDocument());
  });
});

describe("WifiPanel StrictMode", () => {
  afterEach(() => vi.restoreAllMocks());

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

  it("StrictMode double mount does not double-POST on toggle", async () => {
    const fetchSpy = vi.spyOn(globalThis, "fetch");
    fetchSpy
      .mockResolvedValueOnce(json({ available: true, wifi_enabled: true, devices: [], access_points: [] }))
      .mockResolvedValueOnce(json({ available: true, wifi_enabled: true, devices: [], access_points: [] }))
      .mockResolvedValueOnce(json({ ok: true, wifi_enabled: false }))
      .mockResolvedValueOnce(json({ available: true, wifi_enabled: false, devices: [], access_points: [] }));
    render(<StrictMode><WifiPanel /></StrictMode>);
    await waitFor(() => expect(screen.getByRole("switch", { name: "Wi-Fi" })).toBeChecked());
    const toggle = screen.getByRole("switch", { name: "Wi-Fi" });
    await act(async () => {
      await userEvent.click(toggle);
    });
    const toggleCalls = fetchSpy.mock.calls.filter(call => call[0] === "/api/hardware/wifi/enabled");
    expect(toggleCalls.length).toBe(1);
  });

  it("StrictMode double mount does not double-POST on disconnect", async () => {
    const fetchSpy = vi.spyOn(globalThis, "fetch");
    fetchSpy
      .mockResolvedValueOnce(json({ available: true, wifi_enabled: true, devices: [{ path: "/org/freedesktop/NetworkManager/Devices/1", iface: "wlan0", state: 100 }], access_points: [{ ssid: "TestNet", strength: 50, frequency: 2412, secured: true, active: true }] }))
      .mockResolvedValueOnce(json({ available: true, wifi_enabled: true, devices: [{ path: "/org/freedesktop/NetworkManager/Devices/1", iface: "wlan0", state: 100 }], access_points: [{ ssid: "TestNet", strength: 50, frequency: 2412, secured: true, active: true }] }))
      .mockResolvedValueOnce(json({ ok: true }))
      .mockResolvedValueOnce(json({ available: true, wifi_enabled: true, devices: [], access_points: [] }));
    render(<StrictMode><WifiPanel /></StrictMode>);
    await waitFor(() => expect(screen.getByText("TestNet")).toBeInTheDocument());
    const disconnectBtn = screen.getByRole("button", { name: /Disconnect from TestNet/i });
    await act(async () => {
      await userEvent.click(disconnectBtn);
    });
    const disconnectCalls = fetchSpy.mock.calls.filter(call => call[0] === "/api/hardware/wifi/disconnect");
    expect(disconnectCalls.length).toBe(1);
  });

  it("StrictMode double mount does not double-POST on connect", async () => {
    const fetchSpy = vi.spyOn(globalThis, "fetch");
    fetchSpy
      .mockResolvedValueOnce(json({ available: true, wifi_enabled: true, devices: [], access_points: [{ ssid: "TestNet", strength: 50, frequency: 2412, secured: true, active: false }] }))
      .mockResolvedValueOnce(json({ available: true, wifi_enabled: true, devices: [], access_points: [{ ssid: "TestNet", strength: 50, frequency: 2412, secured: true, active: false }] }))
      .mockResolvedValueOnce(json({ ok: true }))
      .mockResolvedValueOnce(json({ available: true, wifi_enabled: true, devices: [], access_points: [{ ssid: "TestNet", strength: 50, frequency: 2412, secured: true, active: true }] }));
    render(<StrictMode><WifiPanel /></StrictMode>);
    await waitFor(() => expect(screen.getByText("TestNet")).toBeInTheDocument());
    const joinBtn = screen.getByRole("button", { name: /Join TestNet \(secured\)/i });
    await act(async () => {
      await userEvent.click(joinBtn);
    });
    await waitFor(() => expect(screen.getByLabelText("Password for TestNet")).toBeInTheDocument());
    await act(async () => {
      await userEvent.type(screen.getByLabelText("Password for TestNet"), "secret");
      await userEvent.keyboard("{Enter}");
    });
    const connectCalls = fetchSpy.mock.calls.filter(call => call[0] === "/api/hardware/wifi/connect");
    expect(connectCalls.length).toBe(1);
  });
});