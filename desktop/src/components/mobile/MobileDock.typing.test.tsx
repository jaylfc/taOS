import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { render, screen, act, cleanup } from "@testing-library/react";
import { MobileDock } from "./MobileDock";
import { useTextEntryActive } from "@/hooks/use-text-entry-active";

// Mirrors how App wires the hook to the dock on the mobile shell.
function Shell({ mobile = true }: { mobile?: boolean }) {
  const typing = useTextEntryActive(mobile);
  return (
    <div>
      <input aria-label="email" type="text" />
      <MobileDock
        onOpenApp={() => {}}
        onToggleSwitcher={() => {}}
        onOpenLaunchpad={() => {}}
        activeAppId={null}
        hidden={typing}
      />
    </div>
  );
}

beforeEach(() => {
  vi.useFakeTimers();
  window.matchMedia = vi.fn().mockImplementation((q: string) => ({
    matches: false, media: q, addEventListener: vi.fn(), removeEventListener: vi.fn(),
  })) as unknown as typeof window.matchMedia;
});
afterEach(() => {
  cleanup();
  vi.useRealTimers();
});

const dock = () => document.querySelector<HTMLElement>('[role="toolbar"]')!;

describe("MobileDock while typing", () => {
  it("is visible and interactive by default", () => {
    render(<Shell />);
    expect(dock().getAttribute("aria-hidden")).toBeNull();
    expect(dock().hasAttribute("inert")).toBe(false);
  });

  it("is aria-hidden and inert while a text field is focused, and returns after blur", () => {
    render(<Shell />);
    const input = screen.getByLabelText("email");
    act(() => { input.focus(); vi.advanceTimersByTime(150); });
    expect(dock().getAttribute("aria-hidden")).toBe("true");
    expect(dock().hasAttribute("inert")).toBe(true);
    expect(dock().style.pointerEvents).toBe("none");
    expect(dock().style.height).toBe("0px");
    act(() => { input.blur(); vi.advanceTimersByTime(150); });
    expect(dock().getAttribute("aria-hidden")).toBeNull();
    expect(dock().hasAttribute("inert")).toBe(false);
  });

  it("is unaffected when the shell is not mobile (desktop)", () => {
    render(<Shell mobile={false} />);
    const input = screen.getByLabelText("email");
    act(() => { input.focus(); vi.advanceTimersByTime(150); });
    expect(dock().getAttribute("aria-hidden")).toBeNull();
    expect(dock().hasAttribute("inert")).toBe(false);
  });
});
