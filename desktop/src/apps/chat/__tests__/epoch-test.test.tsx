import { describe, it, expect, vi } from "vitest";
import { render, screen } from "@testing-library/react";
import { MobileChatList, type MobileChatListProps } from "../MobileChatList";
import type { Channel } from "../types";

function ch(overrides: Partial<Channel> & { id: string; name: string }): Channel {
  return { type: "topic", ...overrides };
}

describe("MobileChatList epoch-seconds sorting", () => {
  it("sorts channels with epoch-seconds last_message_at in recency order (REAL API values)", () => {
    // API returns epoch seconds (e.g., 1727788920 = Oct 2024)
    // Original sortKey uses Date.parse() which returns NaN for these values,
    // causing the list to stay in creation order instead of most-recent-first
    const props = {
      sections: [
        {
          items: [
            ch({ id: "c1", name: "alpha", last_message_at: 1727788920 }),
            ch({ id: "c2", name: "beta", last_message_at: 1727875321 }),
            ch({ id: "c3", name: "gamma", last_message_at: 1727788922 }),
          ],
        },
      ],
      projectGroups: [],
      selectedChannel: null,
      onSelectChannel: vi.fn(),
      unread: {},
      nowMs: Date.now(),
      formatRelativeTime: (ts) => String(ts),
      bus: { channels: [], available: false, loaded: true },
      busSelected: null,
      onSelectBusChannel: vi.fn(),
    } as MobileChatListProps;

    render(<MobileChatList {...props} />);
    const rowNames = screen
      .getAllByRole("button", { name: /^Chat / })
      .map((b) => b.getAttribute("aria-label")!.replace(/^Chat /, ""));
    console.log("Real epoch Row order:", rowNames);
    // beta has later timestamp (1727875321 > 1727788922 > 1727788920),
    // so should appear first (most recent first)
    expect(rowNames).toEqual(["beta", "gamma", "alpha"]);
  });

  it("ISO strings still sort correctly", () => {
    const props = {
      sections: [
        {
          items: [
            ch({ id: "c1", name: "alpha", last_message_at: "2026-10-01T10:00:00Z" }),
            ch({ id: "c2", name: "beta", last_message_at: "2026-10-03T10:00:00Z" }),
            ch({ id: "c3", name: "gamma", last_message_at: "2026-10-02T10:00:00Z" }),
          ],
        },
      ],
      projectGroups: [],
      selectedChannel: null,
      onSelectChannel: vi.fn(),
      unread: {},
      nowMs: Date.now(),
      formatRelativeTime: (ts) => String(ts),
      bus: { channels: [], available: false, loaded: true },
      busSelected: null,
      onSelectBusChannel: vi.fn(),
    } as MobileChatListProps;

    render(<MobileChatList {...props} />);
    const rowNames = screen
      .getAllByRole("button", { name: /^Chat / })
      .map((b) => b.getAttribute("aria-label")!.replace(/^Chat /, ""));
    console.log("ISO Row order:", rowNames);
    // Expected: beta (Oct 3), gamma (Oct 2), alpha (Oct 1)
    expect(rowNames).toEqual(["beta", "gamma", "alpha"]);
  });

  it("null last_message_at falls back to created_at", () => {
    const props = {
      sections: [
        {
          items: [
            ch({ id: "c1", name: "alpha", created_at: "2026-10-01T10:00:00Z", last_message_at: null }),
            ch({ id: "c2", name: "beta", created_at: "2026-10-03T10:00:00Z", last_message_at: null }),
            ch({ id: "c3", name: "gamma", created_at: "2026-10-02T10:00:00Z", last_message_at: null }),
          ],
        },
      ],
      projectGroups: [],
      selectedChannel: null,
      onSelectChannel: vi.fn(),
      unread: {},
      nowMs: Date.now(),
      formatRelativeTime: (ts) => String(ts),
      bus: { channels: [], available: false, loaded: true },
      busSelected: null,
      onSelectBusChannel: vi.fn(),
    } as MobileChatListProps;

    render(<MobileChatList {...props} />);
    const rowNames = screen
      .getAllByRole("button", { name: /^Chat / })
      .map((b) => b.getAttribute("aria-label")!.replace(/^Chat /, ""));
    console.log("Null Row order:", rowNames);
    // Expected: beta (created Oct 3), gamma (created Oct 2), alpha (created Oct 1)
    expect(rowNames).toEqual(["beta", "gamma", "alpha"]);
  });
});
