import { describe, it, expect, vi, afterEach, beforeEach } from "vitest";
import { render, screen } from "@testing-library/react";
import { InstallPromptBanner } from "./InstallPromptBanner";

function mockMatchMedia(standalone = false) {
  window.matchMedia = vi.fn().mockImplementation((q: string) => ({
    matches: q.includes("standalone") ? standalone : false,
    media: q,
    addEventListener: vi.fn(),
    removeEventListener: vi.fn(),
    addListener: vi.fn(),
    removeListener: vi.fn(),
    onchange: null,
    dispatchEvent: vi.fn(),
  }));
}

describe("InstallPromptBanner", () => {
  beforeEach(() => {
    localStorage.clear();
    mockMatchMedia(false);
  });
  afterEach(() => {
    vi.restoreAllMocks();
    localStorage.clear();
  });

  it("shows the Add to Home Screen instruction on iOS Safari (not installed)", () => {
    vi.stubGlobal("navigator", {
      userAgent: "Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) Safari",
      platform: "iPhone",
      maxTouchPoints: 5,
      standalone: false,
    });
    render(<InstallPromptBanner />);
    expect(screen.getByText(/Add to Home Screen/i)).toBeInTheDocument();
  });

  it("renders nothing once installed (standalone display mode)", () => {
    mockMatchMedia(true);
    vi.stubGlobal("navigator", {
      userAgent: "Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) Safari",
      platform: "iPhone",
      maxTouchPoints: 5,
      standalone: true,
    });
    const { container } = render(<InstallPromptBanner />);
    expect(container).toBeEmptyDOMElement();
  });

  it("renders nothing on a desktop browser with no install event", () => {
    vi.stubGlobal("navigator", {
      userAgent: "Mozilla/5.0 (Macintosh; Intel Mac OS X) Chrome",
      platform: "MacIntel",
      maxTouchPoints: 0,
      standalone: undefined,
    });
    const { container } = render(<InstallPromptBanner />);
    expect(container).toBeEmptyDOMElement();
  });
});
