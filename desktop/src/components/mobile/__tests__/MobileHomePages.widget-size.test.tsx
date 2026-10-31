import { describe, it, expect, beforeEach, afterEach, vi } from "vitest";
import { render, screen, fireEvent, act, cleanup } from "@testing-library/react";
import { MobileHomePages } from "../MobileHomePages";
import { useMobileHomeStore } from "@/stores/mobile-home-store";
import { setWidgetSize } from "@/stores/mobile-widget-size";

vi.mock("@/hooks/use-installed-optional-apps", () => ({
  useInstalledOptionalApps: () => new Set<string>(),
}));
vi.mock("@/components/widgets/GreetingWidget", () => ({ GreetingWidget: () => <div>greeting</div> }));
vi.mock("@/components/widgets/ClockWidget", () => ({ ClockWidget: () => <div>clock</div> }));
vi.mock("@/components/widgets/AgentStatusWidget", () => ({ AgentStatusWidget: () => <div>agents</div> }));
vi.mock("@/components/widgets/SystemStatsWidget", () => ({ SystemStatsWidget: () => <div>stats</div> }));
vi.mock("@/components/widgets/QuickNotesWidget", () => ({ QuickNotesWidget: () => <div>notes</div> }));
vi.mock("@/components/widgets/WeatherWidget", () => ({
  WeatherWidget: () => <div data-testid="weather-content">weather</div>,
}));

function weatherCard(): HTMLElement {
  const card = screen.getByTestId("weather-content").parentElement;
  if (!card) throw new Error("no weather card");
  return card;
}

describe("MobileHomePages widget size", () => {
  const onOpenApp = vi.fn();

  beforeEach(() => {
    vi.useFakeTimers();
    localStorage.clear();
    onOpenApp.mockClear();
    useMobileHomeStore.setState({ activePageIndex: 0 });
  });
  afterEach(() => {
    cleanup();
    vi.useRealTimers();
  });

  it("pairs the weather card by default", () => {
    render(<MobileHomePages onOpenApp={onOpenApp} />);
    expect(weatherCard().closest('[data-testid="widget-pair"]')).not.toBeNull();
  });

  it("long-press opens the size sheet without opening the app, and Full width unpairs", () => {
    render(<MobileHomePages onOpenApp={onOpenApp} />);
    const card = weatherCard();
    fireEvent.pointerDown(card, { clientX: 10, clientY: 10 });
    act(() => { vi.advanceTimersByTime(500); });
    fireEvent.pointerUp(card);
    fireEvent.click(card);
    expect(screen.getByRole("dialog", { name: "Widget size" })).toBeTruthy();
    expect(onOpenApp).not.toHaveBeenCalled();

    fireEvent.click(screen.getByRole("button", { name: "Full width" }));
    expect(weatherCard().closest('[data-testid="widget-pair"]')).toBeNull();
    expect(JSON.parse(localStorage.getItem("taos-mobile-widget-size") ?? "{}")).toEqual({ weather: "full" });
    expect(screen.queryByRole("dialog", { name: "Widget size" })).toBeNull();
  });

  it("a short tap opens the app and shows no dialog", () => {
    render(<MobileHomePages onOpenApp={onOpenApp} />);
    const card = weatherCard();
    fireEvent.pointerDown(card, { clientX: 10, clientY: 10 });
    act(() => { vi.advanceTimersByTime(100); });
    fireEvent.pointerUp(card);
    fireEvent.click(card);
    expect(onOpenApp).toHaveBeenCalledWith("weather");
    expect(screen.queryByRole("dialog", { name: "Widget size" })).toBeNull();
  });

  it("keyboard ContextMenu opens the size sheet", () => {
    render(<MobileHomePages onOpenApp={onOpenApp} />);
    const card = weatherCard();
    expect(card.tabIndex).toBe(0);
    fireEvent.keyDown(card, { key: "ContextMenu" });
    expect(screen.getByRole("dialog", { name: "Widget size" })).toBeTruthy();
  });

  it("a lone half side widget renders at half width", () => {
    setWidgetSize("weather", "half");
    useMobileHomeStore.setState({
      activePageIndex: 0,
      pages: [{ items: [{ type: "widget", widgetType: "weather" }] }],
    });
    render(<MobileHomePages onOpenApp={onOpenApp} />);
    expect(weatherCard().style.width).toBe("calc(50% - 6px)");
  });
});
