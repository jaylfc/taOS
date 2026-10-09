import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { render } from "@testing-library/react";
import { MobileTopBar } from "../MobileTopBar";

// Mock the stores and hooks with implementations (except useIsPwa)
vi.mock("@/stores/notification-store", () => ({
  useNotificationStore: (selector: (s: any) => any) => {
    const store = {
      notifications: [],
      toggleCentre: vi.fn(),
    };
    return selector(store);
  },
}));
vi.mock("@/hooks/use-update-available", () => ({
  useUpdateAvailable: () => false,
}));
vi.mock("@/stores/process-store", () => ({
  useProcessStore: (selector: (s: any) => any) => {
    const store = {
      openWindow: vi.fn(),
    };
    return selector(store);
  },
}));
vi.mock("../StatusIndicators", () => ({
  StatusIndicators: (_: { compact?: boolean }) => {
    return <div data-testid="status-indicators-mock" />;
  },
}));

// Mock matchMedia and navigator.standalone
let matchMediaListeners: Map<string, Set<(e: { matches: boolean }) => void>>;

function createMql(matches: boolean) {
  return {
    matches,
    media: "(display-mode: standalone)",
    addEventListener: (event: string, handler: (e: { matches: boolean }) => void) => {
      if (!matchMediaListeners.has(event)) matchMediaListeners.set(event, new Set());
      matchMediaListeners.get(event)!.add(handler);
    },
    removeEventListener: (event: string, handler: (e: { matches: boolean }) => void) => {
      matchMediaListeners.get(event)?.delete(handler);
    },
    // Legacy API
    addListener: vi.fn(),
    removeListener: vi.fn(),
    onchange: null,
    dispatchEvent: vi.fn(),
  };
}

beforeEach(() => {
  matchMediaListeners = new Map();
  vi.stubGlobal("matchMedia", vi.fn((query: string) => createMql(false)));
  // Reset navigator.standalone
  Object.defineProperty(navigator, "standalone", { value: undefined, configurable: true });
});

afterEach(() => {
  vi.unstubAllGlobals();
  // Reset navigator.standalone
  Object.defineProperty(navigator, "standalone", { value: undefined, configurable: true });
});

describe("MobileTopBar edge backdrop", () => {
  it("renders the backdrop when in standalone mode", () => {
    // Set up navigator.standalone = true for iOS PWA
    Object.defineProperty(navigator, "standalone", {
      value: true,
      configurable: true,
    });
    // Also set matchMedia to return true for (display-mode: standalone)
    vi.stubGlobal("matchMedia", vi.fn((query: string) => createMql(true)));

    // Render the component
    render(<MobileTopBar onHome={() => {}} onSearch={() => {}} />);

     // Check that the backdrop exists and is a direct child of document.body
     const backdrop = document.body.querySelector('[data-testid="ios-edge-backdrop"]');
     expect(backdrop).not.toBeNull();
     expect(backdrop.parentElement).toBe(document.body);

     // Check its style
     expect(backdrop.style.position).toBe("fixed");
     expect(backdrop.style.backgroundColor).toBe("rgb(20, 20, 21)");
     // Ensure it's not rgba (i.e., opaque)
     expect(backdrop.style.backgroundColor).not.toContain("rgba");
  });

  it("does not render the backdrop when not in standalone mode", () => {
    // Ensure navigator.standalone is undefined and matchMedia returns false
    Object.defineProperty(navigator, "standalone", {
      value: undefined,
      configurable: true,
    });
    vi.stubGlobal("matchMedia", vi.fn((query: string) => createMql(false)));

    // Render the component
    render(<MobileTopBar onHome={() => {}} onSearch={() => {}} />);

    // Check that the backdrop does not exist
    const backdrop = document.body.querySelector('[data-testid="ios-edge-backdrop"]');
    expect(backdrop).toBeNull();
  });
});