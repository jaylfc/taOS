import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { render, screen, fireEvent } from "@testing-library/react";
import { InstallHelperPanel } from "./InstallHelperPanel";

describe("InstallHelperPanel", () => {
  beforeEach(() => {
    Object.defineProperty(window, "location", {
      value: { origin: "http://localhost:3000" },
      writable: true,
      configurable: true,
    });
  });

  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("renders with appId and appName in heading", () => {
    render(
      <InstallHelperPanel appId="myapp" appName="My App" onClose={vi.fn()} />
    );
    expect(screen.getByText("Install My App")).toBeInTheDocument();
  });

  it("shows the correct install URL", () => {
    render(
      <InstallHelperPanel appId="myapp" appName="My App" onClose={vi.fn()} />
    );
    expect(
      screen.getByDisplayValue("http://localhost:3000/app.html?app=myapp")
    ).toBeInTheDocument();
  });

  it("Copy uses the clipboard API in a secure context", async () => {
    const writeText = vi.fn().mockResolvedValue(undefined);
    vi.stubGlobal("navigator", {
      ...navigator,
      clipboard: { writeText },
      userAgent: "Chrome",
      platform: "Win32",
      maxTouchPoints: 0,
    });
    Object.defineProperty(window, "isSecureContext", {
      value: true,
      configurable: true,
    });

    render(
      <InstallHelperPanel appId="myapp" appName="My App" onClose={vi.fn()} />
    );

    fireEvent.click(screen.getByText("Copy"));
    expect(writeText).toHaveBeenCalledWith(
      expect.stringContaining("/app.html?app=myapp")
    );
  });

  it("Copy falls back to execCommand on a non-secure origin (HTTP)", async () => {
    // Plain-HTTP origins (LAN / Tailscale IP) don't expose navigator.clipboard;
    // the button must still copy via the execCommand fallback rather than throw.
    vi.stubGlobal("navigator", {
      ...navigator,
      clipboard: undefined,
      userAgent: "Chrome",
      platform: "Win32",
      maxTouchPoints: 0,
    });
    Object.defineProperty(window, "isSecureContext", {
      value: false,
      configurable: true,
    });
    const execCommand = vi.fn().mockReturnValue(true);
    Object.defineProperty(document, "execCommand", {
      value: execCommand,
      configurable: true,
      writable: true,
    });

    render(
      <InstallHelperPanel appId="myapp" appName="My App" onClose={vi.fn()} />
    );

    fireEvent.click(screen.getByText("Copy"));
    expect(execCommand).toHaveBeenCalledWith("copy");
  });

  it("onClose fires when Done button is clicked", () => {
    const onClose = vi.fn();
    render(
      <InstallHelperPanel appId="myapp" appName="My App" onClose={onClose} />
    );
    fireEvent.click(screen.getByText("Done"));
    expect(onClose).toHaveBeenCalled();
  });
});
