import { render } from "@testing-library/react";
import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { GreetingWidget } from "../GreetingWidget";

// Fixed tier so the standalone icon renders deterministically (avoids relying
// on ResizeObserver in jsdom).
vi.mock("@/hooks/use-widget-size", () => ({
  useWidgetSize: () => [{ current: null }, { tier: "l" }],
}));

// Greeting fetches a system summary on mount — stub it so jsdom doesn't throw.
vi.stubGlobal("fetch", vi.fn().mockResolvedValue({ ok: false, json: async () => ({}) }));

describe("GreetingWidget", () => {
  beforeEach(() => vi.useFakeTimers());
  afterEach(() => vi.useRealTimers());

  function renderAtHour(hour: number) {
    vi.setSystemTime(new Date(2026, 0, 1, hour, 0, 0));
    return render(<GreetingWidget />);
  }

  it("renders a Sunset SVG icon in the evening (not a font-dependent emoji)", () => {
    const { container } = renderAtHour(19);
    expect(container.querySelector(".lucide-sunset")).not.toBeNull();
    expect(container.textContent).toContain("Good evening.");
  });

  it("renders a Sun SVG icon in the morning", () => {
    const { container } = renderAtHour(8);
    expect(container.querySelector(".lucide-sun")).not.toBeNull();
    expect(container.textContent).toContain("Good morning.");
  });

  it("renders a Moon SVG icon at night", () => {
    const { container } = renderAtHour(23);
    expect(container.querySelector(".lucide-moon")).not.toBeNull();
    expect(container.textContent).toContain("Good night.");
  });

  it("does not emit the iOS-broken sunset emoji glyph", () => {
    const { container } = renderAtHour(19);
    expect(container.textContent).not.toContain("\u{1F306}"); // 🌆
  });
});
