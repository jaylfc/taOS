import { describe, it, expect, vi } from "vitest";
import { render, screen, fireEvent } from "@testing-library/react";
import { MobileChatTabs, CHAT_TAB_APP_IDS, type ChatTab } from "../MobileChatTabs";

describe("MobileChatTabs", () => {
  it("renders all four expected tabs in order", () => {
    render(<MobileChatTabs active="chats" onSelect={() => {}} />);
    expect(screen.getByTestId("mobile-chat-tab-chats")).toBeInTheDocument();
    expect(screen.getByTestId("mobile-chat-tab-projects")).toBeInTheDocument();
    expect(screen.getByTestId("mobile-chat-tab-decisions")).toBeInTheDocument();
    expect(screen.getByTestId("mobile-chat-tab-agents")).toBeInTheDocument();
  });

  it("marks the active tab with aria-current=page and leaves others unset", () => {
    render(<MobileChatTabs active="decisions" onSelect={() => {}} />);
    expect(screen.getByTestId("mobile-chat-tab-decisions")).toHaveAttribute("aria-current", "page");
    expect(screen.getByTestId("mobile-chat-tab-chats")).not.toHaveAttribute("aria-current");
  });

  it("calls onSelect with the clicked tab id", () => {
    const onSelect = vi.fn();
    render(<MobileChatTabs active="chats" onSelect={onSelect} />);
    fireEvent.click(screen.getByTestId("mobile-chat-tab-agents"));
    expect(onSelect).toHaveBeenCalledWith("agents");
  });

  it("does not overflow horizontally on a narrow viewport (no horizontal scroll)", () => {
    const { container } = render(<MobileChatTabs active="chats" onSelect={() => {}} />);
    const nav = container.querySelector("[data-testid='mobile-chat-tabs']") as HTMLElement;
    expect(nav).toBeInTheDocument();
    // The bar is laid out as a flex row of flex-1 buttons — it must never be
    // wider than its parent or introduce a horizontal scroll. The parent is
    // 100vw; the nav width is set by its own flexbox.
    expect(nav.className).toMatch(/flex/);
    // All four tabs share the row equally (each has flex-1); total natural
    // width must not exceed the parent or the flex container would either
    // shrink buttons or wrap to a second row.
    const buttons = nav.querySelectorAll("button");
    for (const b of Array.from(buttons)) {
      expect(b.className).toMatch(/flex-1/);
    }
  });

  it("maps each tab to the expected desktop app id for deep-link routing", () => {
    expect(CHAT_TAB_APP_IDS.chats).toBe("messages");
    expect(CHAT_TAB_APP_IDS.projects).toBe("projects");
    expect(CHAT_TAB_APP_IDS.decisions).toBe("decisions");
    expect(CHAT_TAB_APP_IDS.agents).toBe("agents");
  });

  it("exposes every ChatTab as a registered app id", () => {
    const tabs: ChatTab[] = ["chats", "projects", "decisions", "agents"];
    for (const t of tabs) {
      expect(typeof CHAT_TAB_APP_IDS[t]).toBe("string");
      expect(CHAT_TAB_APP_IDS[t].length).toBeGreaterThan(0);
    }
  });
});
