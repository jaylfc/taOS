import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import { LoginScreen } from "./LoginScreen";

let originalUserAgent: string;
let originalRequestFullscreen: typeof document.documentElement.requestFullscreen;

describe("LoginScreen", () => {
  beforeEach(() => {
    originalUserAgent = navigator.userAgent;
    originalRequestFullscreen = document.documentElement.requestFullscreen;
    document.documentElement.requestFullscreen = vi.fn().mockResolvedValue(undefined);
  });

  afterEach(() => {
    Object.defineProperty(navigator, "userAgent", {
      value: originalUserAgent,
      configurable: true,
    });
    document.documentElement.requestFullscreen = originalRequestFullscreen;
    vi.restoreAllMocks();
  });

  function setUserAgent(ua: string) {
    Object.defineProperty(navigator, "userAgent", {
      value: ua,
      configurable: true,
    });
  }

  it("renders the Launch taOS button when not launching", () => {
    setUserAgent("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) Chrome/120.0.0.0");
    const onLaunch = vi.fn();
    render(<LoginScreen onLaunch={onLaunch} />);
    const btn = screen.getByRole("button", { name: /launch taos/i });
    expect(btn).toBeInTheDocument();
    expect(btn).not.toBeDisabled();
  });

  it("shows Launching... text and disables the button while launching", async () => {
    setUserAgent("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) Chrome/120.0.0.0");
    const onLaunch = vi.fn();
    render(<LoginScreen onLaunch={onLaunch} />);
    const btn = screen.getByRole("button", { name: /launch taos/i });
    fireEvent.click(btn);
    expect(btn).toBeDisabled();
    expect(screen.getByText("Launching...")).toBeInTheDocument();
  });

  it("calls onLaunch after the launch delay", async () => {
    vi.useFakeTimers();
    setUserAgent("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) Chrome/120.0.0.0");
    const onLaunch = vi.fn();
    render(<LoginScreen onLaunch={onLaunch} />);
    fireEvent.click(screen.getByRole("button", { name: /launch taos/i }));
    expect(onLaunch).not.toHaveBeenCalled();
    await vi.advanceTimersByTimeAsync(600);
    expect(onLaunch).toHaveBeenCalledTimes(1);
    vi.useRealTimers();
  });

  it("shows 'Full experience available' for Chrome user agent", () => {
    setUserAgent("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) Chrome/120.0.0.0");
    const onLaunch = vi.fn();
    render(<LoginScreen onLaunch={onLaunch} />);
    expect(screen.getByText("Full experience available")).toBeInTheDocument();
    expect(screen.queryByText(/install taos as an app/i)).not.toBeInTheDocument();
    expect(screen.queryByText(/full keyboard support/i)).not.toBeInTheDocument();
  });

  it("shows 'Install taOS as an app' for Safari user agent", () => {
    setUserAgent(
      "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) Version/17.0 Safari/604.1.34",
    );
    const onLaunch = vi.fn();
    render(<LoginScreen onLaunch={onLaunch} />);
    expect(screen.getByText("Install taOS as an app for the best experience")).toBeInTheDocument();
    expect(screen.queryByText(/full experience available/i)).not.toBeInTheDocument();
    expect(screen.queryByText(/full keyboard support/i)).not.toBeInTheDocument();
  });

  it("shows 'use Chrome or Edge' for unknown browsers", () => {
    setUserAgent("Mozilla/5.0 (compatible; SomeBot/1.0)");
    const onLaunch = vi.fn();
    render(<LoginScreen onLaunch={onLaunch} />);
    expect(
      screen.getByText("For full keyboard support, use Chrome or Edge"),
    ).toBeInTheDocument();
    expect(screen.queryByText(/full experience available/i)).not.toBeInTheDocument();
    expect(screen.queryByText(/install taos as an app/i)).not.toBeInTheDocument();
  });

  it("renders the taOS logo image", () => {
    setUserAgent("Mozilla/5.0 Chrome/120.0.0.0");
    const onLaunch = vi.fn();
    render(<LoginScreen onLaunch={onLaunch} />);
    const img = screen.getByRole("img", { name: /taos/i });
    expect(img).toBeInTheDocument();
    expect(img).toHaveAttribute("src", "/static/taos-logo.png");
  });

  it("calls requestFullscreen when the launch button is clicked", () => {
    setUserAgent("Mozilla/5.0 Chrome/120.0.0.0");
    const onLaunch = vi.fn();
    render(<LoginScreen onLaunch={onLaunch} />);
    fireEvent.click(screen.getByRole("button", { name: /launch taos/i }));
    expect(document.documentElement.requestFullscreen).toHaveBeenCalledTimes(1);
  });
});
