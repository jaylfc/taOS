import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, fireEvent } from "@testing-library/react";

vi.mock("@/stores/process-store", () => {
  const store = { openWindow: vi.fn(() => "win-1") };
  return {
    useProcessStore: (sel?: (s: typeof store) => unknown) =>
      sel ? sel(store) : store,
  };
});

vi.mock("@/hooks/use-installed-services", () => ({
  useInstalledServices: () => [],
}));

vi.mock("@/hooks/use-installed-optional-apps", () => ({
  useInstalledOptionalApps: () => new Set<string>(),
}));

vi.mock("@/hooks/use-installed-userspace-apps", () => ({
  useInstalledUserspaceApps: () => [],
}));

vi.mock("@/hooks/use-shortcut-registry", () => ({
  useShortcut: vi.fn(),
}));

vi.mock("@/registry/app-registry", () => ({
  getLaunchableApps: (installedOptional: Set<string>) => [
    { id: "messages", name: "Messages", icon: "message-circle", category: "platform", defaultSize: { w: 900, h: 600 } },
    { id: "mail", name: "Mail", icon: "mail", category: "platform", defaultSize: { w: 1200, h: 800 } },
    { id: "weather", name: "Weather", icon: "cloud", category: "os", defaultSize: { w: 800, h: 600 } },
    { id: "chess", name: "Chess", icon: "crown", category: "game", defaultSize: { w: 700, h: 700 } },
  ].filter((a) => !a.id.startsWith("service:") && !a.id.startsWith("userspace:") && (a.id !== "reddit" || installedOptional.has("reddit"))),
  getApp: (id: string) => {
    const apps: Record<string, { id: string; name: string; defaultSize: { w: number; h: number } }> = {
      messages: { id: "messages", name: "Messages", defaultSize: { w: 900, h: 600 } },
      mail: { id: "mail", name: "Mail", defaultSize: { w: 1200, h: 800 } },
      weather: { id: "weather", name: "Weather", defaultSize: { w: 800, h: 600 } },
      chess: { id: "chess", name: "Chess", defaultSize: { w: 700, h: 700 } },
    };
    return apps[id];
  },
  getOrRegisterServiceApp: (appId: string, displayName: string) => ({
    id: `service:${appId}`,
    name: displayName,
    defaultSize: { w: 1100, h: 750 },
  }),
}));

import { Launchpad } from "./Launchpad";

describe("Launchpad", () => {
  const onClose = vi.fn();
  const onOpenApp = vi.fn();

  beforeEach(() => {
    vi.clearAllMocks();
  });

  it("returns null when open is false", () => {
    const { container } = render(<Launchpad open={false} onClose={onClose} onOpenApp={onOpenApp} />);
    expect(container.firstChild).toBeNull();
  });

  it("renders the dialog with aria-label when open is true", () => {
    render(<Launchpad open={true} onClose={onClose} onOpenApp={onOpenApp} />);
    expect(screen.getByRole("dialog", { name: /launchpad/i })).toBeInTheDocument();
  });

  it("renders the search input", () => {
    render(<Launchpad open={true} onClose={onClose} onOpenApp={onOpenApp} />);
    expect(screen.getByPlaceholderText("Search apps...")).toBeInTheDocument();
  });

  it("renders category headings for the built-in apps", () => {
    render(<Launchpad open={true} onClose={onClose} onOpenApp={onOpenApp} />);
    expect(screen.getByText("Platform")).toBeInTheDocument();
    expect(screen.getByText("Utilities")).toBeInTheDocument();
    expect(screen.getByText("Games")).toBeInTheDocument();
  });

  it("renders app names for built-in apps", () => {
    render(<Launchpad open={true} onClose={onClose} onOpenApp={onOpenApp} />);
    expect(screen.getByText("Messages")).toBeInTheDocument();
    expect(screen.getByText("Mail")).toBeInTheDocument();
    expect(screen.getByText("Weather")).toBeInTheDocument();
    expect(screen.getByText("Chess")).toBeInTheDocument();
  });

  it("filters apps by search query", () => {
    render(<Launchpad open={true} onClose={onClose} onOpenApp={onOpenApp} />);
    const input = screen.getByPlaceholderText("Search apps...");
    fireEvent.change(input, { target: { value: "chess" } });
    expect(screen.getByText("Chess")).toBeInTheDocument();
    expect(screen.queryByText("Messages")).not.toBeInTheDocument();
    expect(screen.queryByText("Mail")).not.toBeInTheDocument();
  });

  it("shows clear button when query is non-empty and clears on click", () => {
    render(<Launchpad open={true} onClose={onClose} onOpenApp={onOpenApp} />);
    const input = screen.getByPlaceholderText("Search apps...");
    fireEvent.change(input, { target: { value: "chess" } });
    const clearBtn = screen.getByRole("button", { name: /clear search/i });
    expect(clearBtn).toBeInTheDocument();
    fireEvent.click(clearBtn);
    expect(input).toHaveValue("");
  });

  it("does not show clear button when query is empty", () => {
    render(<Launchpad open={true} onClose={onClose} onOpenApp={onOpenApp} />);
    expect(screen.queryByRole("button", { name: /clear search/i })).not.toBeInTheDocument();
  });

  it("calls onClose when an app icon is clicked", () => {
    render(<Launchpad open={true} onClose={onClose} onOpenApp={onOpenApp} />);
    const btn = screen.getByRole("button", { name: /open messages/i });
    fireEvent.click(btn);
    // handleLaunch calls onClose, and the click bubbles to the overlay onClick
    // which also calls onClose. Both fire as expected by the component design.
    expect(onClose).toHaveBeenCalledTimes(2);
  });

  it("calls onOpenApp with the window id returned by openWindow when launching an app", () => {
    render(<Launchpad open={true} onClose={onClose} onOpenApp={onOpenApp} />);
    const btn = screen.getByRole("button", { name: /open messages/i });
    fireEvent.click(btn);
    expect(onOpenApp).toHaveBeenCalledWith("win-1");
  });

  it("does not render Services section when no services are installed", () => {
    render(<Launchpad open={true} onClose={onClose} onOpenApp={onOpenApp} />);
    expect(screen.queryByText("Services")).not.toBeInTheDocument();
  });

  it("does not render My Apps section when no userspace apps are installed", () => {
    render(<Launchpad open={true} onClose={onClose} onOpenApp={onOpenApp} />);
    expect(screen.queryByText("My Apps")).not.toBeInTheDocument();
  });
});
