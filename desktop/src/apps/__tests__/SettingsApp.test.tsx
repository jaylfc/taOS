import { describe, it, expect, vi, afterEach, beforeEach } from "vitest";
import { render, screen, waitFor, within, fireEvent, act } from "@testing-library/react";
import { SettingsApp, DesktopDockSection } from "../SettingsApp";
import { useThemeStore } from "@/stores/theme-store";

function mockAuthStatus(isAdmin: boolean) {
  return vi.spyOn(globalThis, "fetch").mockImplementation((input) => {
    if (String(input).includes("/auth/status")) {
      return Promise.resolve(
        new Response(
          JSON.stringify({
            configured: true,
            authenticated: true,
            user: { username: "jay", is_admin: isAdmin },
          }),
          { status: 200, headers: { "Content-Type": "application/json" } },
        ),
      );
    }
    // Every other endpoint (system info, cloud account, memory, etc.) is
    // irrelevant to this test, so just answer with "not available" and let
    // the mounted section's own fetches settle quietly.
    return Promise.resolve(new Response(null, { status: 404 }));
  });
}

function mockAuthAndVersion(isAdmin: boolean, mdnsHostname: string | null) {
  return vi.spyOn(globalThis, "fetch").mockImplementation((input) => {
    const url = String(input);
    if (url.includes("/auth/status")) {
      return Promise.resolve(
        new Response(
          JSON.stringify({
            configured: true,
            authenticated: true,
            user: { username: "jay", is_admin: isAdmin },
          }),
          { status: 200, headers: { "Content-Type": "application/json" } },
        ),
      );
    }
    if (url.includes("/api/version")) {
      return Promise.resolve(
        new Response(
          JSON.stringify({
            version: "x",
            mdns_hostname: mdnsHostname,
          }),
          { status: 200, headers: { "Content-Type": "application/json" } },
        ),
      );
    }
    // Every other endpoint (system info, cloud account, memory, etc.) is
    // irrelevant to this test, so just answer with "not available" and let
    // the mounted section's own fetches settle quietly.
    return Promise.resolve(new Response(null, { status: 404 }));
  });
}

describe("SettingsApp admin-only sections", () => {
  afterEach(() => {
    vi.restoreAllMocks();
  });

  it("hides admin-only sections from a non-admin user", async () => {
    mockAuthStatus(false);
    render(<SettingsApp windowId="w1" />);
    const nav = screen.getByRole("navigation", { name: "Settings sections" });

    await waitFor(() => {
      expect(screen.queryByText("Updates")).not.toBeInTheDocument();
    });
    expect(screen.queryByText("Advanced")).not.toBeInTheDocument();
    // Demo mode changes the lock screen for everyone: admin-only.
    expect(within(nav).queryByText("Demo mode")).not.toBeInTheDocument();

    // Personal sections stay visible in the sidebar.
    expect(within(nav).getByText("Themes")).toBeInTheDocument();
    expect(within(nav).getByText("Account")).toBeInTheDocument();
    // Every user owns how their own lock screen opens.
    expect(within(nav).getByText("Lock screen")).toBeInTheDocument();
  });

  it("shows admin-only sections to an admin user", async () => {
    mockAuthStatus(true);
    render(<SettingsApp windowId="w1" />);
    const nav = screen.getByRole("navigation", { name: "Settings sections" });

    await waitFor(() => {
      expect(within(nav).getByText("Updates")).toBeInTheDocument();
    });
    expect(within(nav).getByText("Users")).toBeInTheDocument();
    expect(within(nav).getByText("Advanced")).toBeInTheDocument();
    expect(within(nav).getByText("Demo mode")).toBeInTheDocument();
    expect(within(nav).getByText("Lock screen")).toBeInTheDocument();

    expect(within(nav).getByText("Themes")).toBeInTheDocument();
    expect(within(nav).getByText("Account")).toBeInTheDocument();
  });
});

describe("SystemInfoSection local address", () => {
  afterEach(() => {
    vi.restoreAllMocks();
  });

  it("shows Local address row with link and copy button when mdns_hostname is set", async () => {
    mockAuthAndVersion(true, "taos-ab12.local");
    render(<SettingsApp windowId="w1" />);

    await waitFor(() => {
      // System Information is the default section, so the link should appear
      expect(screen.getByText(/^http:\/\/taos-ab12\.local:/)).toBeInTheDocument();
    });
    expect(screen.getByRole("button", { name: "Copy local address" })).toBeInTheDocument();
  });

  it("uses execCommand fallback when navigator.clipboard is undefined", async () => {
    const fetchSpy = mockAuthAndVersion(true, "taos-ab12.local");
    const originalClipboard = navigator.clipboard;
    Object.defineProperty(navigator, "clipboard", { value: undefined, configurable: true });
    const execCommandSpy = vi.fn(() => true);
    document.execCommand = execCommandSpy;
    render(<SettingsApp windowId="w1" />);

    await waitFor(() => {
      expect(screen.getByText(/^http:\/\/taos-ab12\.local:/)).toBeInTheDocument();
    });
    fireEvent.click(screen.getByRole("button", { name: "Copy local address" }));
    await waitFor(() => {
      expect(execCommandSpy).toHaveBeenCalledWith("copy");
    });

    Object.defineProperty(navigator, "clipboard", { value: originalClipboard, configurable: true });
  });

  it("shows copy failed message when execCommand returns false", async () => {
    const fetchSpy = mockAuthAndVersion(true, "taos-ab12.local");
    const originalClipboard = navigator.clipboard;
    Object.defineProperty(navigator, "clipboard", { value: undefined, configurable: true });
    const execCommandSpy = vi.fn(() => false);
    document.execCommand = execCommandSpy;
    render(<SettingsApp windowId="w1" />);

    await waitFor(() => {
      expect(screen.getByText(/^http:\/\/taos-ab12\.local:/)).toBeInTheDocument();
    });
    fireEvent.click(screen.getByRole("button", { name: "Copy local address" }));
    await screen.findByText("Copy failed, select the address to copy it");

    Object.defineProperty(navigator, "clipboard", { value: originalClipboard, configurable: true });
  });

  it("does not render Local address row when mdns_hostname is null", async () => {
    const fetchSpy = mockAuthAndVersion(true, null);
    render(<SettingsApp windowId="w1" />);

    await waitFor(() => {
      expect(fetchSpy.mock.calls.some(([u]) => String(u).includes("/api/version"))).toBe(true);
    });
    await act(async () => { await Promise.resolve(); });

    expect(screen.queryByText("Local address")).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Copy local address" })).not.toBeInTheDocument();
  });
});

describe("DesktopDockSection wallpaper entry", () => {
  beforeEach(() => {
    useThemeStore.setState({
      wallpaperId: "graphite",
      wallpaperImage: "url('/static/wallpaper-graphite.png')",
      wallpaperFallback: "#141415",
      wallpaperKind: "image",
      wallpaperOverlayText: null,
      showOverlayText: true,
      wallpaperParams: { density: 200, speed: 0.5, glow: 6 },
    });
  });

  it("shows the current wallpaper label", () => {
    render(<DesktopDockSection />);
    expect(screen.getByText("Graphite")).toBeInTheDocument();
  });

  it("shows a \"Change…\" button", () => {
    render(<DesktopDockSection />);
    expect(
      screen.getByRole("button", { name: /change…/i }),
    ).toBeInTheDocument();
  });

  it("opens the WallpaperPicker when \"Change…\" is clicked", () => {
    render(<DesktopDockSection />);
    fireEvent.click(screen.getByRole("button", { name: /change…/i }));
    expect(
      screen.getByRole("dialog", { name: /change wallpaper/i }),
    ).toBeInTheDocument();
  });

  it("closes the WallpaperPicker when its close button is clicked", () => {
    render(<DesktopDockSection />);
    fireEvent.click(screen.getByRole("button", { name: /change…/i }));
    const dialog = screen.getByRole("dialog", { name: /change wallpaper/i });
    expect(dialog).toBeInTheDocument();
    fireEvent.click(within(dialog).getByRole("button", { name: /close/i }));
    expect(
      screen.queryByRole("dialog", { name: /change wallpaper/i }),
    ).toBeNull();
  });

  it("updates the wallpaper label when the store wallpaperId changes", () => {
    const { rerender } = render(<DesktopDockSection />);
    expect(screen.getByText("Graphite")).toBeInTheDocument();
    act(() => {
      useThemeStore.setState({ wallpaperId: "aurora" });
    });
    rerender(<DesktopDockSection />);
    expect(screen.getByText("Aurora")).toBeInTheDocument();
  });

  it("retains dock icon size and position controls", () => {
    render(<DesktopDockSection />);
    expect(screen.getByText("Dock icon size")).toBeInTheDocument();
    expect(screen.getByText("Dock position")).toBeInTheDocument();
    expect(
      screen.getByRole("group", { name: "Dock icon size" }),
    ).toBeInTheDocument();
    expect(
      screen.getByRole("group", { name: "Dock position" }),
    ).toBeInTheDocument();
  });
});

describe("BackupSection restore", () => {
  afterEach(() => {
    vi.restoreAllMocks();
  });

  function mockAuthAndRestore(isAdmin: boolean, restoreResponse: { status: number; body: object }) {
    return vi.spyOn(globalThis, "fetch").mockImplementation((input) => {
      const url = String(input);
      if (url.includes("/auth/status")) {
        return Promise.resolve(
          new Response(
            JSON.stringify({
              configured: true,
              authenticated: true,
              user: { username: "jay", is_admin: isAdmin },
            }),
            { status: 200, headers: { "Content-Type": "application/json" } },
          ),
        );
      }
      if (url.includes("/api/restore")) {
        return Promise.resolve(
          new Response(JSON.stringify(restoreResponse.body), {
            status: restoreResponse.status,
            headers: { "Content-Type": "application/json" },
          }),
        );
      }
      if (url.includes("/api/account/me")) {
        return Promise.resolve(
          new Response(JSON.stringify({ username: "jay", is_admin: isAdmin }), {
            status: 200,
            headers: { "Content-Type": "application/json" },
          }),
        );
      }
      return Promise.resolve(new Response(null, { status: 404 }));
    });
  }

  async function renderBackupSection() {
    mockAuthAndRestore(true, { status: 200, body: { status: "restored" } });
    render(<SettingsApp windowId="w1" section="backup" />);
    await waitFor(() => {
      expect(screen.getByText("Restore from Backup")).toBeInTheDocument();
    });
    return screen.getByLabelText("Upload backup file");
  }

  it("uploads backup file on change, shows success message, and resets input", async () => {
    const confirmSpy = vi.spyOn(window, "confirm").mockReturnValue(true);
    const input = await renderBackupSection();

    const file = new File(["backup content"], "backup.tar.gz", { type: "application/gzip" });

    fireEvent.change(input, { target: { files: [file] } });

    await waitFor(() => {
      expect(fetch).toHaveBeenCalledWith("/api/restore", expect.objectContaining({ method: "POST" }));
    });

    const restoreCalls = (fetch as vi.Mock).mock.calls.filter(([url]) => String(url).includes("/api/restore"));
    expect(restoreCalls).toHaveLength(1);
    const [url, options] = restoreCalls[0];
    expect(url).toBe("/api/restore");
    expect(options.method).toBe("POST");
    expect(options.body).toBeInstanceOf(FormData);
    const formData = options.body as FormData;
    expect(formData.get("file")).toBe(file);

    await waitFor(() => {
      expect(screen.getByText("restored")).toBeInTheDocument();
    });

    expect(input.value).toBe("");

    confirmSpy.mockRestore();
  });

  it("shows error message on 400 response", async () => {
    const confirmSpy = vi.spyOn(window, "confirm").mockReturnValue(true);
    mockAuthAndRestore(true, { status: 400, body: { error: "Invalid backup file: not a tar.gz" } });
    render(<SettingsApp windowId="w1" section="backup" />);
    await waitFor(() => {
      expect(screen.getByText("Restore from Backup")).toBeInTheDocument();
    });
    const input = screen.getByLabelText("Upload backup file");
    const file = new File(["bad content"], "backup.zip", { type: "application/zip" });

    fireEvent.change(input, { target: { files: [file] } });

    await waitFor(() => {
      expect(fetch).toHaveBeenCalledWith("/api/restore", expect.objectContaining({ method: "POST" }));
    });

    await waitFor(() => {
      expect(screen.getByText("Invalid backup file: not a tar.gz")).toBeInTheDocument();
    });

    expect(input.value).toBe("");

    confirmSpy.mockRestore();
  });

  it("does nothing when user cancels confirm dialog", async () => {
    const confirmSpy = vi.spyOn(window, "confirm").mockReturnValue(false);
    const fetchSpy = mockAuthAndRestore(true, { status: 200, body: { status: "restored" } });
    render(<SettingsApp windowId="w1" section="backup" />);
    await waitFor(() => {
      expect(screen.getByText("Restore from Backup")).toBeInTheDocument();
    });
    const input = screen.getByLabelText("Upload backup file");
    const file = new File(["backup content"], "backup.tar.gz", { type: "application/gzip" });

    fireEvent.change(input, { target: { files: [file] } });

    await act(async () => { await Promise.resolve(); });
    const restoreCalls = fetchSpy.mock.calls.filter(([url]) => String(url).includes("/api/restore"));
    expect(restoreCalls).toHaveLength(0);

    expect(input.value).toBe("");

    confirmSpy.mockRestore();
  });

  it("has accept attribute limited to .tar.gz and .tgz", async () => {
    mockAuthAndRestore(true, { status: 200, body: { status: "restored" } });
    render(<SettingsApp windowId="w1" section="backup" />);
    await waitFor(() => {
      expect(screen.getByText("Restore from Backup")).toBeInTheDocument();
    });
    const input = screen.getByLabelText("Upload backup file");
    expect(input.getAttribute("accept")).toBe(".tar.gz,.tgz");
  });
});

describe("Users section reachable by a member", () => {
  afterEach(() => {
    vi.restoreAllMocks();
  });

  it("shows the Users nav item and My Account card for a non-admin", async () => {
    vi.spyOn(globalThis, "fetch").mockImplementation((input) => {
      const url = String(input);
      if (url.includes("/auth/status")) {
        return Promise.resolve(
          new Response(
            JSON.stringify({
              configured: true,
              authenticated: true,
              user: { username: "m", is_admin: false },
            }),
            { status: 200, headers: { "Content-Type": "application/json" } },
          ),
        );
      }
      return Promise.resolve(new Response(null, { status: 404 }));
    });
    render(<SettingsApp windowId="w1" />);
    const nav = screen.getByRole("navigation", { name: "Settings sections" });

    await waitFor(() => {
      expect(within(nav).getByText("Users")).toBeInTheDocument();
    });
    fireEvent.click(within(nav).getByRole("button", { name: "Users" }));

    expect(screen.getByRole("heading", { name: "My Account" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Change password" })).toBeInTheDocument();
    expect(screen.queryByText("Team Members")).not.toBeInTheDocument();
  });
});
