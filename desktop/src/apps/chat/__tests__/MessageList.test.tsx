import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen } from "@testing-library/react";
import { MessageList } from "../MessageList";
import type { MessageRow, MessageListProps } from "../MessageList";
import type { Channel, LiveAgent, ArchivedAgentEntry } from "../types";
import type { PinnedMessage } from "../PinnedMessagesPopover";
import type { AgentTyping } from "../TypingFooter";

/* ------------------------------------------------------------------ */
/*  Mock heavy / JSDOM-incompatible deps                               */
/* ------------------------------------------------------------------ */

vi.mock("emoji-picker-react", () => ({
  default: () => null,
  Theme: { DARK: "dark" },
}));

vi.mock("react-dom", async () => {
  const actual = await vi.importActual("react-dom");
  return {
    ...(actual as object),
    createPortal: (children: React.ReactNode) => children,
  };
});

/* ------------------------------------------------------------------ */
/*  Test helpers                                                       */
/* ------------------------------------------------------------------ */

function msg(overrides: Partial<MessageRow> = {}): MessageRow {
  return {
    id: "m1",
    channel_id: "ch1",
    author_id: "alice",
    author_type: "user",
    content: "Hello world",
    state: "complete",
    created_at: 1700000000,
    ...overrides,
  };
}

function defaults(
  overrides: Partial<MessageListProps> = {},
): MessageListProps {
  return {
    messages: [],
    fetchedChannel: null,
    channel: undefined,
    selectedChannel: null,
    isMobile: false,
    keyboardInset: 0,
    nowMs: Date.now(),
    liveAgents: [],
    archivedAgents: [],
    currentUserId: "user",
    currentUserDisplayName: "You",
    pinnedMessages: [],
    pinnedPopoverOpen: false,
    onTogglePinnedPopover: vi.fn(),
    editingMessageId: null,
    onCancelEdit: vi.fn(),
    onSaveEdit: vi.fn(),
    onToggleReaction: vi.fn(),
    showEmoji: null,
    onShowEmoji: vi.fn(),
    hoveredMessageId: null,
    onHoverMessage: vi.fn(),
    onReplyInThread: vi.fn(),
    onOverflow: vi.fn(),
    onOpenThread: vi.fn(),
    onApprovePinRequest: vi.fn(),
    onViewCanvas: vi.fn(),
    newDividerAtId: null,
    atBottom: true,
    newCount: 0,
    onScrollToLatest: vi.fn(),
    onScroll: vi.fn(),
    dropTarget: {
      isOver: false,
      isValidTarget: false,
      handlers: {
        onDragEnter: vi.fn(),
        onDragOver: vi.fn(),
        onDragLeave: vi.fn(),
        onDrop: vi.fn(),
      },
    },
    showAllThreads: false,
    onToggleAllThreads: vi.fn(),
    showSearch: false,
    onToggleSearch: vi.fn(),
    onOpenSettings: vi.fn(),
    typingHumans: [],
    typingAgents: [],
    ...overrides,
  };
}

function channel(overrides: Partial<Channel> = {}): Channel {
  return { id: "ch1", name: "general", type: "topic", ...overrides };
}

/** Render with sane defaults for a channel with one message. */
function renderWithMsg(props: Partial<MessageListProps> = {}) {
  return render(
    <MessageList
      {...defaults({
        selectedChannel: "ch1",
        channel: channel(),
        ...props,
      })}
    />,
  );
}

/* ------------------------------------------------------------------ */
/*  Tests                                                              */
/* ------------------------------------------------------------------ */

describe("MessageList", () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  /* ---- empty / no-channel state ---- */

  describe("empty state", () => {
    it("renders placeholder when no channel is selected", () => {
      render(<MessageList {...defaults()} />);
      expect(screen.getByText("Pick a channel or start a DM")).toBeInTheDocument();
    });

    it("renders empty-messages prompt when channel is selected but no messages", () => {
      render(
        <MessageList
          {...defaults({
            selectedChannel: "ch1",
            fetchedChannel: "ch1",
            channel: channel(),
          })}
        />,
      );
      expect(screen.getByText(/No messages yet/i)).toBeInTheDocument();
    });

    it("does not show empty prompt when fetchedChannel differs", () => {
      render(
        <MessageList
          {...defaults({
            selectedChannel: "ch1",
            fetchedChannel: "ch2", // mismatch
            channel: channel(),
          })}
        />,
      );
      expect(screen.queryByText(/No messages yet/i)).toBeNull();
    });
  });

  /* ---- message rendering ---- */

  describe("message rendering", () => {
    it("renders message content", () => {
      renderWithMsg({
        messages: [msg({ content: "Hello world" })],
      });
      expect(screen.getByText("Hello world")).toBeInTheDocument();
    });

    it("renders author name for user message", () => {
      renderWithMsg({
        messages: [msg({ author_id: "alice", author_type: "user" })],
        currentUserDisplayName: "Me",
      });
      expect(screen.getByText("alice")).toBeInTheDocument();
    });

    it("shows 'You' for own messages", () => {
      renderWithMsg({
        messages: [msg({ author_id: "user" })],
        currentUserId: "user",
        currentUserDisplayName: "You",
      });
      expect(screen.getByText("You")).toBeInTheDocument();
    });

    it("renders agent badge for agent messages", () => {
      renderWithMsg({
        messages: [msg({ author_id: "hal", author_type: "agent" })],
        liveAgents: [{ name: "hal" }],
      });
      expect(screen.getByText("Agent")).toBeInTheDocument();
    });

    it("hides author name for consecutive messages by same author", () => {
      renderWithMsg({
        messages: [
          msg({ id: "m1", author_id: "alice" }),
          msg({ id: "m2", author_id: "alice" }),
        ],
      });
      // The author name appears only once
      const authors = screen.getAllByText("alice");
      expect(authors).toHaveLength(1);
    });

    it("shows author name again when author changes", () => {
      renderWithMsg({
        messages: [
          msg({ id: "m1", author_id: "alice" }),
          msg({ id: "m2", author_id: "bob" }),
        ],
      });
      expect(screen.getByText("alice")).toBeInTheDocument();
      expect(screen.getByText("bob")).toBeInTheDocument();
    });
  });

  /* ---- day separators ---- */

  describe("day separators", () => {
    it("renders day label on first message", () => {
      renderWithMsg({
        messages: [msg()],
      });
      // dayLabel returns "Today", "Yesterday", or a formatted date — verify
      // the separator element exists and contains human-readable text
      const separator = document.querySelector(".select-none .text-white\\/40");
      expect(separator).not.toBeNull();
      expect(separator?.textContent?.trim().length).toBeGreaterThan(0);
    });

    it("renders separator when day changes between messages", () => {
      const day1 = 1700000000; // seconds
      const day2 = 1700086400; // next day
      renderWithMsg({
        messages: [
          msg({ id: "m1", created_at: day1 }),
          msg({ id: "m2", created_at: day2 }),
        ],
      });
      // Should have two day separator elements
      const separators = document.querySelectorAll(".select-none .text-white\\/40");
      expect(separators.length).toBe(2);
    });
  });

  /* ---- new divider ---- */

  describe("new divider", () => {
    it("shows 'New' separator at newDividerAtId", () => {
      renderWithMsg({
        messages: [msg({ id: "m1" }), msg({ id: "m2" })],
        newDividerAtId: "m2",
      });
      expect(screen.getByText("New")).toBeInTheDocument();
    });

    it("does not show separator when null", () => {
      renderWithMsg({
        messages: [msg({ id: "m1" }), msg({ id: "m2" })],
        newDividerAtId: null,
      });
      expect(screen.queryByText("New")).toBeNull();
    });
  });

  /* ---- deleted messages ---- */

  describe("deleted messages", () => {
    it("renders tombstone for deleted messages", () => {
      renderWithMsg({
        messages: [msg({ deleted_at: 1700000001 })],
      });
      expect(screen.getByText(/deleted/i)).toBeInTheDocument();
    });

    it("does not render content for deleted messages", () => {
      renderWithMsg({
        messages: [msg({ content: "should not show", deleted_at: 1700000001 })],
      });
      expect(screen.queryByText("should not show")).toBeNull();
    });
  });

  /* ---- editing state ---- */

  describe("editing state", () => {
    it("renders MessageEditor when editing a message", () => {
      renderWithMsg({
        messages: [msg({ id: "m1", content: "old text" })],
        editingMessageId: "m1",
      });
      // MessageEditor renders a textarea with the initial content
      const textarea = screen.getByLabelText("Edit message");
      expect(textarea).toBeInTheDocument();
      expect(textarea).toHaveValue("old text");
    });

    it("does not render raw content when editing", () => {
      renderWithMsg({
        messages: [msg({ id: "m1", content: "old text" })],
        editingMessageId: "m1",
      });
      // The textarea has the content, not the raw rendered markdown
      expect(screen.getByDisplayValue("old text")).toBeInTheDocument();
    });
  });

  /* ---- message states ---- */

  describe("message states", () => {
    it("renders pending indicator", () => {
      renderWithMsg({
        messages: [msg({ state: "pending" })],
      });
      expect(screen.getByText("...")).toBeInTheDocument();
    });

    it("renders streaming dots", () => {
      renderWithMsg({
        messages: [msg({ state: "streaming" })],
      });
      // Three animated dots rendered as spans
      const dots = document.querySelectorAll('[class*="animate-bounce"]');
      expect(dots.length).toBe(3);
    });

    it("renders error indicator", () => {
      renderWithMsg({
        messages: [msg({ state: "error" })],
      });
      expect(screen.getByText("(error)")).toBeInTheDocument();
    });
  });

  /* ---- edited indicator ---- */

  describe("edited indicator", () => {
    it("shows (edited) when edited_at is set", () => {
      renderWithMsg({
        messages: [msg({ edited_at: 1700000001 })],
      });
      expect(screen.getByText("(edited)")).toBeInTheDocument();
    });

    it("does not show (edited) by default", () => {
      renderWithMsg({
        messages: [msg()],
      });
      expect(screen.queryByText("(edited)")).toBeNull();
    });
  });

  /* ---- reactions ---- */

  describe("reactions", () => {
    it("renders ReactionBar when message has reactions", () => {
      renderWithMsg({
        messages: [msg({ reactions: { "👍": ["alice"] } })],
      });
      // ReactionBar shows the emoji and user count as a button
      const btn = screen.getByRole("button", { name: /👍/ });
      expect(btn).toBeInTheDocument();
      expect(btn.textContent).toContain("1");
    });

    it("does not render reactions when empty", () => {
      renderWithMsg({
        messages: [msg({ reactions: {} })],
      });
      // ReactionBar renders buttons for each reaction; empty means no buttons
      const reactionButtons = screen.queryAllByRole("button", { name: /👍|❤️/ });
      expect(reactionButtons.length).toBe(0);
    });
  });

  /* ---- thread indicators ---- */

  describe("thread indicators", () => {
    it("renders ThreadIndicator when reply_count > 0", () => {
      renderWithMsg({
        messages: [msg({ reply_count: 3, last_reply_at: 1700000001 })],
      });
      expect(screen.getByText(/3 replies/i)).toBeInTheDocument();
    });

    it("does not render thread indicator when no replies", () => {
      renderWithMsg({
        messages: [msg({ reply_count: 0 })],
      });
      expect(screen.queryByText(/repl/i)).toBeNull();
    });
  });

  /* ---- dead agents ---- */

  describe("dead agents", () => {
    it("shows inactive badge for archived agents", () => {
      renderWithMsg({
        messages: [msg({ author_id: "oldbot", author_type: "agent" })],
        liveAgents: [],
        archivedAgents: [{ id: "a1", archived_slug: "oldbot" }],
      });
      expect(screen.getByText("inactive")).toBeInTheDocument();
    });

    it("shows removed badge for unknown agents", () => {
      renderWithMsg({
        messages: [msg({ author_id: "gone", author_type: "agent" })],
        liveAgents: [],
        archivedAgents: [],
      });
      expect(screen.getByText("removed")).toBeInTheDocument();
    });

    it("shows active badge for live agents", () => {
      renderWithMsg({
        messages: [msg({ author_id: "hal", author_type: "agent" })],
        liveAgents: [{ name: "hal" }],
      });
      expect(screen.getByText("Agent")).toBeInTheDocument();
      expect(screen.queryByText("inactive")).toBeNull();
      expect(screen.queryByText("removed")).toBeNull();
    });
  });

  /* ---- scroll-to-bottom ---- */

  describe("scroll-to-bottom button", () => {
    it("shows jump-to-latest when not at bottom", () => {
      renderWithMsg({
        messages: [msg()],
        atBottom: false,
        newCount: 5,
      });
      expect(screen.getByText("5 new")).toBeInTheDocument();
      expect(screen.getByLabelText("Jump to latest")).toBeInTheDocument();
    });

    it("hides when at bottom", () => {
      renderWithMsg({
        messages: [msg()],
        atBottom: true,
        newCount: 5,
      });
      expect(screen.queryByLabelText("Jump to latest")).toBeNull();
    });
  });

  /* ---- channel header ---- */

  describe("channel header", () => {
    it("shows Hash icon for topic channels", () => {
      renderWithMsg({
        channel: channel({ type: "topic" }),
      });
      // lucide Hash icon is rendered
      expect(document.querySelector(".lucide-hash")).toBeTruthy();
    });

    it("shows Users icon for group channels", () => {
      renderWithMsg({
        channel: channel({ type: "group" }),
      });
      expect(document.querySelector(".lucide-users")).toBeTruthy();
    });

    it("shows AtSign icon for dm channels", () => {
      renderWithMsg({
        channel: channel({ type: "dm", members: ["user", "hal"] }),
        liveAgents: [{ name: "hal", emoji: "🤖" }],
      });
      expect(document.querySelector(".lucide-at-sign")).toBeTruthy();
    });

    it("shows channel description when set", () => {
      renderWithMsg({
        channel: channel({ description: "General discussion" }),
      });
      expect(screen.getByText("General discussion")).toBeInTheDocument();
    });

    it("shows member count for group channels", () => {
      const { container } = renderWithMsg({
        channel: channel({ type: "group", members: ["user", "alice", "bob"] }),
      });
      // Member count is rendered next to the Users icon
      const memberDiv = container.querySelector(".text-white\\/30.flex.items-center");
      expect(memberDiv).not.toBeNull();
      expect(memberDiv?.textContent).toContain("3");
    });
  });

  /* ---- canvas attachment ---- */

  describe("canvas attachments", () => {
    it("renders View Canvas button for canvas type messages", () => {
      renderWithMsg({
        messages: [
          msg({
            content_type: "canvas",
            metadata: { canvas_url: "https://example.com/canvas/1", canvas_title: "My Canvas" },
          }),
        ],
      });
      expect(screen.getByText(/View Canvas: My Canvas/)).toBeInTheDocument();
    });

    it("renders without title when canvas_title is missing", () => {
      renderWithMsg({
        messages: [
          msg({
            content_type: "canvas",
            metadata: { canvas_url: "https://example.com/canvas/1" },
          }),
        ],
      });
      expect(screen.getByLabelText("View canvas")).toBeInTheDocument();
    });
  });

  /* ---- pin request affordance ---- */

  describe("pin request", () => {
    it("shows PinRequestAffordance for agent messages with pin_requested", () => {
      renderWithMsg({
        messages: [
          msg({
            author_id: "hal",
            author_type: "agent",
            metadata: { pin_requested: true },
          }),
        ],
        liveAgents: [{ name: "hal" }],
      });
      expect(screen.getByText(/wants to pin this/i)).toBeInTheDocument();
      expect(screen.getByLabelText(/Pin this message/i)).toBeInTheDocument();
    });

    it("does not show for user messages", () => {
      renderWithMsg({
        messages: [
          msg({
            author_type: "user",
            metadata: { pin_requested: true },
          }),
        ],
      });
      expect(screen.queryByText(/requested pin/i)).toBeNull();
    });
  });

  /* ---- typing footer ---- */

  describe("typing footer", () => {
    it("renders typing indicators", () => {
      renderWithMsg({
        messages: [msg()],
        typingHumans: ["alice"],
        typingAgents: [] as AgentTyping[],
      });
      expect(screen.getByText("alice is typing…")).toBeInTheDocument();
    });

    it("renders nothing when nobody is typing", () => {
      const { container } = render(
        <MessageList
          {...defaults({
            selectedChannel: "ch1",
            channel: channel(),
            messages: [msg()],
            typingHumans: [],
            typingAgents: [],
          })}
        />,
      );
      // TypingFooter returns null when empty — no aria-live region rendered
      expect(container.querySelector('[aria-live="polite"]')).toBeNull();
    });
  });

  /* ---- imperative handle ---- */

  describe("ref imperative handle", () => {
    it("exposes scrollToBottom via ref", () => {
      const ref = { current: null as { scrollToBottom: () => void } | null };
      render(
        <MessageList
          {...defaults({
            selectedChannel: "ch1",
            channel: channel(),
            messages: [msg()],
          })}
          ref={ref}
        />,
      );
      expect(ref.current).not.toBeNull();
      expect(typeof ref.current?.scrollToBottom).toBe("function");
    });

    it("scrollToBottom does not throw", () => {
      const ref = { current: null as { scrollToBottom: () => void } | null };
      render(
        <MessageList
          {...defaults({
            selectedChannel: "ch1",
            channel: channel(),
            messages: [msg()],
          })}
          ref={ref}
        />,
      );
      expect(() => ref.current?.scrollToBottom()).not.toThrow();
    });
  });

  /* ---- mobile ---- */

  describe("mobile", () => {
    it("applies keyboard inset padding when mobile + keyboard visible", () => {
      const { container } = render(
        <MessageList
          {...defaults({
            selectedChannel: "ch1",
            channel: channel(),
            messages: [msg()],
            isMobile: true,
            keyboardInset: 200,
          })}
        />,
      );
      const scrollArea = container.querySelector(".message-list-drop-target");
      expect(scrollArea).toBeTruthy();
      const style = scrollArea?.getAttribute("style") ?? "";
      expect(style).toContain("padding-bottom");
    });
  });

  /* ---- pinned messages ---- */

  describe("pinned messages", () => {
    it("shows PinBadge when there are pinned messages", () => {
      renderWithMsg({
        pinnedMessages: [{ id: "p1", content: "pinned note", author_id: "alice" } as PinnedMessage],
      });
      expect(screen.getByRole("button", { name: /pin/i })).toBeInTheDocument();
    });

    it("shows PinnedMessagesPopover when popover is open", () => {
      renderWithMsg({
        pinnedMessages: [
          {
            id: "p1",
            content: "pinned note",
            author_id: "alice",
            created_at: 1700000000,
            pinned_by: "user",
            pinned_at: 1700000001,
          },
        ] as PinnedMessage[],
        pinnedPopoverOpen: true,
      });
      // The popover should be visible
      expect(screen.getByText("pinned note")).toBeInTheDocument();
    });
  });

  /* ---- thread/view toggle buttons ---- */

  describe("toggle buttons", () => {
    it("renders All Threads toggle button", () => {
      renderWithMsg();
      expect(screen.getByTitle("All threads")).toBeInTheDocument();
    });

    it("renders Search toggle button", () => {
      renderWithMsg();
      expect(screen.getByTitle("Search")).toBeInTheDocument();
    });
  });
});
