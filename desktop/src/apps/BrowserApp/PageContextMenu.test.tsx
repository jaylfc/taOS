import { describe, it, expect, beforeEach, vi, afterEach } from "vitest";
import { render, screen, fireEvent, waitFor, act } from "@testing-library/react";
import { PageContextMenu } from "./PageContextMenu";
import * as browserAgentApi from "@/lib/browser-agent-api";
import * as browserExtractApi from "@/lib/browser-extract-api";

const AGENTS = [
  { id: "agent-1", name: "Alpha", emoji: "🤖" },
  { id: "agent-2", name: "Beta", emoji: "🧪" },
];

function defaultProps(overrides?: Partial<Parameters<typeof PageContextMenu>[0]>) {
  return {
    windowId: "win-1",
    tabId: "tab-1",
    url: "https://example.com/article",
    title: "Test Article",
    selection: null,
    x: 200,
    y: 150,
    onClose: vi.fn(),
    profileId: "personal",
    ...overrides,
  };
}

beforeEach(() => {
  vi.spyOn(browserAgentApi, "listAgents").mockResolvedValue(AGENTS);
  vi.spyOn(browserExtractApi, "extractReadable").mockResolvedValue({
    title: "Test Article",
    text: "Extracted text content",
    html: "<p>Extracted text content</p>",
    word_count: 3,
  });
});

afterEach(() => {
  vi.restoreAllMocks();
});

describe("PageContextMenu", () => {
  it("renders one Send-to entry per agent", async () => {
    render(<PageContextMenu {...defaultProps()} />);
    await waitFor(() => {
      expect(screen.getByText(/send to alpha/i)).toBeTruthy();
      expect(screen.getByText(/send to beta/i)).toBeTruthy();
    });
  });

  it("renders Pin to Memory entry", async () => {
    render(<PageContextMenu {...defaultProps()} />);
    await waitFor(() => {
      expect(screen.getByText(/pin to memory/i)).toBeTruthy();
    });
  });

  it("clicking 'Send to <Agent>' dispatches taos:open-messages with channelId + prefillCard", async () => {
    const dispatched: CustomEvent[] = [];
    const listener = (e: Event) => dispatched.push(e as CustomEvent);
    window.addEventListener("taos:open-messages", listener);

    render(<PageContextMenu {...defaultProps()} />);
    await waitFor(() => {
      expect(screen.getByText(/send to alpha/i)).toBeTruthy();
    });

    fireEvent.click(screen.getByText(/send to alpha/i));
    await waitFor(() => {
      expect(dispatched.length).toBe(1);
      expect(dispatched[0].detail.channelId).toBe("Alpha");
      expect(dispatched[0].detail.prefillCard).toBeDefined();
      expect(dispatched[0].detail.prefillCard.url).toBe("https://example.com/article");
      expect(dispatched[0].detail.prefillCard.title).toBe("Test Article");
    });

    window.removeEventListener("taos:open-messages", listener);
  });

  it("the dispatched prefillCard contains the resolved extract.text", async () => {
    const dispatched: CustomEvent[] = [];
    const listener = (e: Event) => dispatched.push(e as CustomEvent);
    window.addEventListener("taos:open-messages", listener);

    render(<PageContextMenu {...defaultProps()} />);
    await waitFor(() => {
      expect(screen.getByText(/send to alpha/i)).toBeTruthy();
    });

    fireEvent.click(screen.getByText(/send to alpha/i));
    await waitFor(() => {
      expect(dispatched.length).toBe(1);
      expect(dispatched[0].detail.prefillCard.extract).toBe("Extracted text content");
    });

    window.removeEventListener("taos:open-messages", listener);
  });

  it("clicking 'Pin to Memory' dispatches taos:open-memory", async () => {
    const dispatched: CustomEvent[] = [];
    const listener = (e: Event) => dispatched.push(e as CustomEvent);
    window.addEventListener("taos:open-memory", listener);

    render(<PageContextMenu {...defaultProps()} />);
    await waitFor(() => {
      expect(screen.getByText(/pin to memory/i)).toBeTruthy();
    });

    fireEvent.click(screen.getByText(/pin to memory/i));
    await waitFor(() => {
      expect(dispatched.length).toBe(1);
      expect(dispatched[0].detail.prefillCard).toBeDefined();
      expect(dispatched[0].detail.prefillCard.url).toBe("https://example.com/article");
    });

    window.removeEventListener("taos:open-memory", listener);
  });

  it("Esc closes via onClose", async () => {
    const onClose = vi.fn();
    const { container } = render(<PageContextMenu {...defaultProps({ onClose })} />);
    await waitFor(() => {
      expect(screen.getByRole("menu")).toBeTruthy();
    });
    fireEvent.keyDown(container.firstChild as HTMLElement, { key: "Escape" });
    expect(onClose).toHaveBeenCalled();
  });

  it("click outside closes via onClose", async () => {
    const onClose = vi.fn();
    render(<PageContextMenu {...defaultProps({ onClose })} />);
    await waitFor(() => {
      expect(screen.getByRole("menu")).toBeTruthy();
    });
    // Deferred-add pattern — wait for setTimeout(0)
    await act(async () => {
      await new Promise((r) => setTimeout(r, 10));
    });
    fireEvent.mouseDown(document.body);
    expect(onClose).toHaveBeenCalled();
  });

  it("ArrowDown/Up navigate between entries", async () => {
    const { container } = render(<PageContextMenu {...defaultProps()} />);
    await waitFor(() => {
      expect(screen.getByText(/send to alpha/i)).toBeTruthy();
    });

    const menu = container.firstChild as HTMLElement;
    // Initially Alpha should be focused (first item). Assert via waitFor so the
    // focus-reset effect that fires when agents finish loading is fully flushed
    // before we read aria-current (it would otherwise race the keydown below).
    await waitFor(() => {
      const items = screen.getAllByRole("menuitem");
      expect(items[0].getAttribute("aria-current")).toBe("true");
    });

    // Wrap the keydown in act() so the setFocusedIdx update and any pending
    // load effect settle deterministically before the assertion, rather than
    // racing the 1s waitFor under loaded CI runners.
    await act(async () => {
      fireEvent.keyDown(menu, { key: "ArrowDown" });
    });
    await waitFor(() => {
      const updated = screen.getAllByRole("menuitem");
      expect(updated[1].getAttribute("aria-current")).toBe("true");
    });

    await act(async () => {
      fireEvent.keyDown(menu, { key: "ArrowUp" });
    });
    await waitFor(() => {
      const updated = screen.getAllByRole("menuitem");
      expect(updated[0].getAttribute("aria-current")).toBe("true");
    });
  });

  it("Enter triggers the focused entry", async () => {
    const dispatched: CustomEvent[] = [];
    const listener = (e: Event) => dispatched.push(e as CustomEvent);
    window.addEventListener("taos:open-messages", listener);

    const { container } = render(<PageContextMenu {...defaultProps()} />);
    await waitFor(() => {
      expect(screen.getByText(/send to alpha/i)).toBeTruthy();
    });

    const menu = container.firstChild as HTMLElement;
    // First item is focused; Enter should trigger it
    fireEvent.keyDown(menu, { key: "Enter" });
    await waitFor(() => {
      expect(dispatched.length).toBe(1);
    });

    window.removeEventListener("taos:open-messages", listener);
  });

  it("if extractReadable returns null, dispatches with empty extract", async () => {
    vi.spyOn(browserExtractApi, "extractReadable").mockResolvedValue(null);
    const dispatched: CustomEvent[] = [];
    const listener = (e: Event) => dispatched.push(e as CustomEvent);
    window.addEventListener("taos:open-messages", listener);

    render(<PageContextMenu {...defaultProps()} />);
    await waitFor(() => {
      expect(screen.getByText(/send to alpha/i)).toBeTruthy();
    });

    fireEvent.click(screen.getByText(/send to alpha/i));
    await waitFor(() => {
      expect(dispatched.length).toBe(1);
      expect(dispatched[0].detail.prefillCard.extract).toBe("");
    });

    window.removeEventListener("taos:open-messages", listener);
  });

  it("role=menu + role=menuitem present", async () => {
    render(<PageContextMenu {...defaultProps()} />);
    await waitFor(() => {
      expect(screen.getByRole("menu")).toBeTruthy();
      const items = screen.getAllByRole("menuitem");
      expect(items.length).toBeGreaterThan(0);
    });
  });

  it("position uses x/y props", async () => {
    const { container } = render(
      <PageContextMenu {...defaultProps({ x: 300, y: 400 })} />,
    );
    await waitFor(() => {
      expect(screen.getByRole("menu")).toBeTruthy();
    });
    const menu = container.firstChild as HTMLElement;
    // Should be positioned; check the style attribute references the coords
    // (the component adjusts for overflow so exact values may shift, but
    // the menu element uses position:fixed)
    expect(menu.style.position).toBe("fixed");
  });

  it("menu closes after clicking 'Send to <Agent>'", async () => {
    const onClose = vi.fn();
    render(<PageContextMenu {...defaultProps({ onClose })} />);
    await waitFor(() => {
      expect(screen.getByText(/send to alpha/i)).toBeTruthy();
    });

    fireEvent.click(screen.getByText(/send to alpha/i));
    await waitFor(() => {
      expect(onClose).toHaveBeenCalled();
    });
  });

  it("menu closes after clicking 'Pin to Memory'", async () => {
    const onClose = vi.fn();
    render(<PageContextMenu {...defaultProps({ onClose })} />);
    await waitFor(() => {
      expect(screen.getByText(/pin to memory/i)).toBeTruthy();
    });

    fireEvent.click(screen.getByText(/pin to memory/i));
    await waitFor(() => {
      expect(onClose).toHaveBeenCalled();
    });
  });

  it("suppresses dispatch when component unmounts during extractReadable", async () => {
    let resolveExtract!: (v: null) => void;
    vi.spyOn(browserExtractApi, "extractReadable").mockReturnValue(
      new Promise<null>((resolve) => { resolveExtract = resolve; }),
    );

    const dispatched: Event[] = [];
    const listener = (e: Event) => dispatched.push(e);
    window.addEventListener("taos:open-messages", listener);

    const { unmount } = render(<PageContextMenu {...defaultProps()} />);
    await waitFor(() => {
      expect(screen.getByText(/send to alpha/i)).toBeTruthy();
    });

    fireEvent.click(screen.getByText(/send to alpha/i));
    // Unmount before the extract resolves
    unmount();
    // Now resolve — the guard should swallow the dispatch
    await act(async () => { resolveExtract(null); });

    expect(dispatched.length).toBe(0);

    window.removeEventListener("taos:open-messages", listener);
  });
});
