import { render, screen, fireEvent } from "@testing-library/react";
import { describe, it, expect, vi, beforeEach } from "vitest";
import { TopBar } from "./TopBar";

// Stub child components / hooks that pull in heavy dependencies
vi.mock("@/hooks/use-clock", () => ({ useClock: () => "12:00" }));
vi.mock("@/stores/widget-store", () => ({
  useWidgetStore: () => ({ showWidgets: false, toggleWidgets: vi.fn() }),
}));
vi.mock("@/stores/notification-store", () => ({
  useNotificationStore: (sel: (s: { notifications: never[]; toggleCentre: () => void }) => unknown) =>
    sel({ notifications: [], toggleCentre: vi.fn() }),
}));
vi.mock("@/stores/process-store", () => ({
  useProcessStore: (sel: (s: { openWindow: () => void }) => unknown) =>
    sel({ openWindow: vi.fn() }),
}));
vi.mock("./StatusIndicators", () => ({ StatusIndicators: () => null }));

// lucide-react uses ESM; mock only what we need to avoid SVG issues in jsdom
vi.mock("lucide-react", async () => {
  const actual = await vi.importActual<typeof import("lucide-react")>("lucide-react");
  return actual;
});

describe("TopBar", () => {
  const onSearchOpen = vi.fn();
  const onAssistantOpen = vi.fn();

  beforeEach(() => {
    vi.clearAllMocks();
  });

  it("renders the taOS Assistant sparkles button", () => {
    render(<TopBar onSearchOpen={onSearchOpen} onAssistantOpen={onAssistantOpen} />);
    const btn = screen.getByRole("button", { name: /open taOS Assistant/i });
    expect(btn).toBeInTheDocument();
  });

  it("calls onAssistantOpen when sparkles button is clicked", () => {
    render(<TopBar onSearchOpen={onSearchOpen} onAssistantOpen={onAssistantOpen} />);
    const btn = screen.getByRole("button", { name: /open taOS Assistant/i });
    fireEvent.click(btn);
    expect(onAssistantOpen).toHaveBeenCalledOnce();
  });

  it("calls onSearchOpen when Search button is clicked", () => {
    render(<TopBar onSearchOpen={onSearchOpen} onAssistantOpen={onAssistantOpen} />);
    const btn = screen.getByRole("button", { name: /search/i });
    fireEvent.click(btn);
    expect(onSearchOpen).toHaveBeenCalledOnce();
  });
});
