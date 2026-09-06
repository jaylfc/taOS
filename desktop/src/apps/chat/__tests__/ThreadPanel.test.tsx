import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import { ThreadPanel } from "../ThreadPanel";

const stubParent = {
  id: "parent-1",
  author_id: "alice",
  author_type: "user" as const,
  content: "Anyone seen the logs?",
  created_at: 1700000000,
};

const stubReply1 = {
  id: "reply-1",
  author_id: "bob",
  author_type: "user" as const,
  content: "Looking now",
  created_at: 1700000100,
};

const stubReply2 = {
  id: "reply-2",
  author_id: "carol",
  author_type: "agent" as const,
  content: "Found 3 errors in nginx.log",
  created_at: 1700000200,
};

beforeEach(() => {
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
});

afterEach(() => {
  vi.unstubAllGlobals();
});

/* ── helpers ────────────────────────────────────────────────────── */

function stubFetch(responses: Record<string, unknown>) {
  vi.stubGlobal(
    "fetch",
    vi.fn().mockImplementation((url: string) => {
      // Match on the path segment that distinguishes the endpoints:
      //   GET /api/chat/messages/{id}              → "messages"
      //   GET /api/chat/channels/{ch}/threads/{id}/messages → "threads"
      // We check url.includes so ".../threads/p1/messages" matches "threads"
      // and ".../messages/p1" matches "messages". Order matters: /threads/
      // is checked first because a thread-messages URL also contains /messages/.
      let key: string | undefined;
      if (url.includes("/threads/")) {
        key = "threads";
      } else if (url.includes("/messages/")) {
        key = "messages";
      }
      const hit = key ? responses[key] : undefined;
      if (!hit) throw new Error(`Unmocked fetch: ${url}`);
      if (hit instanceof Error) return Promise.reject(hit);
      const r = hit as { ok: boolean; status?: number; json?: () => unknown };
      return Promise.resolve({
        ok: r.ok ?? true,
        status: r.status ?? 200,
        json: r.json ?? (() => Promise.resolve({})),
      });
    }),
  );
}

function stubFetchOk(overrides: { parent?: unknown; replies?: unknown } = {}) {
  stubFetch({
    messages: {
      ok: true,
      json: () => Promise.resolve(overrides.parent ?? stubParent),
    },
    threads: {
      ok: true,
      json: () =>
        Promise.resolve(
          overrides.replies ?? { messages: [stubReply1, stubReply2] },
        ),
    },
  });
}

/* ── rendering & structure ──────────────────────────────────────── */

describe("ThreadPanel", () => {
  it("renders the panel header and close button", async () => {
    stubFetchOk();
    render(
      <ThreadPanel
        channelId="ch1"
        parentId="p1"
        onClose={vi.fn()}
        onSend={vi.fn()}
      />,
    );
    expect(screen.getByText("Thread")).toBeInTheDocument();
    expect(
      screen.getByRole("button", { name: "Close thread" }),
    ).toBeInTheDocument();
    await waitFor(() =>
      expect(screen.getByText(stubParent.content)).toBeInTheDocument(),
    );
  });

  it("renders in normal panel layout by default", () => {
    stubFetchOk();
    const { container } = render(
      <ThreadPanel
        channelId="ch1"
        parentId="p1"
        onClose={vi.fn()}
        onSend={vi.fn()}
      />,
    );
    const panel = container.firstElementChild!;
    expect(panel.className).toContain("w-[360px]");
    expect(panel.className).toContain("top-0");
    expect(panel.className).toContain("right-0");
    expect(panel).toHaveAttribute("role", "complementary");
    expect(panel).toHaveAttribute("aria-label", "Thread panel");
  });

  it("renders fullscreen layout with Back button and safe-area padding", () => {
    stubFetchOk();
    const { container } = render(
      <ThreadPanel
        channelId="ch1"
        parentId="p1"
        onClose={vi.fn()}
        onSend={vi.fn()}
        isFullscreen
      />,
    );
    const panel = container.firstElementChild!;
    expect(panel.className).toContain("fixed");
    expect(panel.className).toContain("inset-0");
    expect(panel.className).toContain("z-50");
    expect(panel).toHaveStyle({
      paddingTop: "env(safe-area-inset-top, 0px)",
    });
    expect(
      screen.getByRole("button", { name: "Back" }),
    ).toBeInTheDocument();
  });

  it("renders the reply textarea with placeholder", () => {
    stubFetchOk();
    render(
      <ThreadPanel
        channelId="ch1"
        parentId="p1"
        onClose={vi.fn()}
        onSend={vi.fn()}
      />,
    );
    const ta = screen.getByRole("textbox", { name: "Thread reply" });
    expect(ta).toHaveAttribute("placeholder", "Reply in thread…");
  });

  /* ── parent message ─────────────────────────────────────────── */

  it("fetches and displays the parent message", async () => {
    stubFetchOk();
    render(
      <ThreadPanel
        channelId="ch1"
        parentId="p1"
        onClose={vi.fn()}
        onSend={vi.fn()}
      />,
    );
    await waitFor(() =>
      expect(screen.getByText(stubParent.content)).toBeInTheDocument(),
    );
  });

  it("shows the parent author label", async () => {
    stubFetchOk({
      parent: { ...stubParent, author_id: "alice", author_type: "user" },
    });
    render(
      <ThreadPanel
        channelId="ch1"
        parentId="p1"
        onClose={vi.fn()}
        onSend={vi.fn()}
        authorCtx={{ currentUserId: "bob", currentUserDisplayName: null }}
      />,
    );
    await waitFor(() => expect(screen.getByText("alice")).toBeInTheDocument());
  });

  it("shows currentUserDisplayName for own messages as parent author", async () => {
    stubFetchOk({
      parent: { ...stubParent, author_id: "me", author_type: "user" },
    });
    render(
      <ThreadPanel
        channelId="ch1"
        parentId="p1"
        onClose={vi.fn()}
        onSend={vi.fn()}
        authorCtx={{
          currentUserId: "me",
          currentUserDisplayName: "Myself",
        }}
      />,
    );
    await waitFor(() => expect(screen.getByText("Myself")).toBeInTheDocument());
  });

  /* ── replies ────────────────────────────────────────────────── */

  it("fetches and displays thread replies", async () => {
    stubFetchOk();
    render(
      <ThreadPanel
        channelId="ch1"
        parentId="p1"
        onClose={vi.fn()}
        onSend={vi.fn()}
      />,
    );
    await waitFor(() =>
      expect(screen.getByText(stubReply1.content)).toBeInTheDocument(),
    );
    expect(screen.getByText(stubReply2.content)).toBeInTheDocument();
  });

  it("shows author for each reply", async () => {
    stubFetchOk();
    render(
      <ThreadPanel
        channelId="ch1"
        parentId="p1"
        onClose={vi.fn()}
        onSend={vi.fn()}
      />,
    );
    await waitFor(() =>
      expect(screen.getByText(stubReply1.content)).toBeInTheDocument(),
    );
    // bob is a user, carol is an agent — both show their id by default
    expect(screen.getByText("bob")).toBeInTheDocument();
    expect(screen.getByText("carol")).toBeInTheDocument();
  });

  it("merges liveReplies with fetched replies, de-duplicating by id", async () => {
    // Fetched replies: reply-1, reply-2
    stubFetchOk();
    const liveReply = {
      id: "reply-3",
      author_id: "dave",
      author_type: "user" as const,
      content: "Live update!",
      created_at: 1700000300,
    };
    const dupeReply = {
      id: "reply-1", // same id as stubReply1
      author_id: "bob",
      author_type: "user" as const,
      content: "Updated content via WS",
      created_at: 1700000300,
    };
    const { rerender } = render(
      <ThreadPanel
        channelId="ch1"
        parentId="p1"
        onClose={vi.fn()}
        onSend={vi.fn()}
      />,
    );
    await waitFor(() =>
      expect(screen.getByText(stubReply1.content)).toBeInTheDocument(),
    );
    // Now re-render with liveReplies — the dupe should NOT duplicate reply-1,
    // and the original stub content should still show (de-dupe keeps first seen).
    rerender(
      <ThreadPanel
        channelId="ch1"
        parentId="p1"
        onClose={vi.fn()}
        onSend={vi.fn()}
        liveReplies={[liveReply, dupeReply]}
      />,
    );
    await waitFor(() =>
      expect(screen.getByText("Live update!")).toBeInTheDocument(),
    );
    // reply-1 appears once, with original content
    expect(screen.getByText(stubReply1.content)).toBeInTheDocument();
    expect(screen.queryByText("Updated content via WS")).not.toBeInTheDocument();
  });

  it("scrolls to bottom when a new liveReply arrives", async () => {
    stubFetchOk();
    const { rerender, container } = render(
      <ThreadPanel
        channelId="ch1"
        parentId="p1"
        onClose={vi.fn()}
        onSend={vi.fn()}
      />,
    );
    await waitFor(() =>
      expect(screen.getByText(stubReply1.content)).toBeInTheDocument(),
    );
    // The component sets scrollTop = scrollHeight in a useEffect that watches
    // liveReplies.length. In JSDOM both default to 0, so we pin a non-zero
    // scrollHeight on the scroll container to verify the assignment.
    const scroller = container.querySelector('[class*="overflow-y-auto"]')!;
    expect(scroller).toBeTruthy();
    Object.defineProperty(scroller, "scrollHeight", {
      value: 400,
      writable: true,
      configurable: true,
    });
    scroller.scrollTop = 0;
    rerender(
      <ThreadPanel
        channelId="ch1"
        parentId="p1"
        onClose={vi.fn()}
        onSend={vi.fn()}
        liveReplies={[
          {
            id: "fresh",
            author_id: "eve",
            author_type: "agent",
            content: "fresh reply",
            created_at: 1700000400,
          },
        ]}
      />,
    );
    await waitFor(() =>
      expect(screen.getByText("fresh reply")).toBeInTheDocument(),
    );
    // The scrollToBottom useEffect should have set scrollTop = scrollHeight (400).
    expect(scroller.scrollTop).toBe(400);
  });

  /* ── error handling ──────────────────────────────────────────── */

  it("shows load error when parent fetch fails", async () => {
    stubFetch({
      messages: new Error("fail"),
      threads: {
        ok: true,
        json: () => Promise.resolve({ messages: [] }),
      },
    });
    render(
      <ThreadPanel
        channelId="ch1"
        parentId="p1"
        onClose={vi.fn()}
        onSend={vi.fn()}
      />,
    );
    await waitFor(() =>
      expect(
        screen.getByText("couldn't load this thread"),
      ).toBeInTheDocument(),
    );
  });

  it("shows load error when replies fetch fails", async () => {
    stubFetch({
      messages: {
        ok: true,
        json: () => Promise.resolve(stubParent),
      },
      threads: new Error("fail"),
    });
    render(
      <ThreadPanel
        channelId="ch1"
        parentId="p1"
        onClose={vi.fn()}
        onSend={vi.fn()}
      />,
    );
    await waitFor(() =>
      expect(
        screen.getByText("couldn't load this thread"),
      ).toBeInTheDocument(),
    );
  });

  it("handles parent fetch returning non-OK gracefully", async () => {
    stubFetch({
      messages: { ok: false, status: 404, json: () => Promise.resolve({}) },
      threads: {
        ok: true,
        json: () => Promise.resolve({ messages: [] }),
      },
    });
    render(
      <ThreadPanel
        channelId="ch1"
        parentId="p1"
        onClose={vi.fn()}
        onSend={vi.fn()}
      />,
    );
    await waitFor(() =>
      expect(
        screen.getByText("couldn't load this thread"),
      ).toBeInTheDocument(),
    );
  });

  it("handles replies endpoint returning non-OK by using empty messages", async () => {
    stubFetch({
      messages: {
        ok: true,
        json: () => Promise.resolve(stubParent),
      },
      threads: { ok: false, status: 404, json: () => Promise.resolve({}) },
    });
    render(
      <ThreadPanel
        channelId="ch1"
        parentId="p1"
        onClose={vi.fn()}
        onSend={vi.fn()}
      />,
    );
    // Parent should still render, but no replies.
    await waitFor(() =>
      expect(screen.getByText(stubParent.content)).toBeInTheDocument(),
    );
    expect(screen.queryByText(stubReply1.content)).not.toBeInTheDocument();
  });

  it("handles replies missing the messages field", async () => {
    stubFetch({
      messages: {
        ok: true,
        json: () => Promise.resolve(stubParent),
      },
      threads: { ok: true, json: () => Promise.resolve({}) },
    });
    render(
      <ThreadPanel
        channelId="ch1"
        parentId="p1"
        onClose={vi.fn()}
        onSend={vi.fn()}
      />,
    );
    await waitFor(() =>
      expect(screen.getByText(stubParent.content)).toBeInTheDocument(),
    );
    // No replies rendered.
    expect(screen.queryByText(stubReply1.content)).not.toBeInTheDocument();
  });

  /* ── submit ──────────────────────────────────────────────────── */

  it("submits trimmed content on Enter without Shift", async () => {
    stubFetchOk();
    const onSend = vi.fn().mockResolvedValue(undefined);
    render(
      <ThreadPanel
        channelId="ch1"
        parentId="p1"
        onClose={vi.fn()}
        onSend={onSend}
      />,
    );
    const ta = screen.getByRole("textbox", { name: "Thread reply" });
    fireEvent.change(ta, { target: { value: "  hello world  " } });
    fireEvent.keyDown(ta, { key: "Enter", shiftKey: false });
    await waitFor(() => expect(onSend).toHaveBeenCalledTimes(1));
    expect(onSend).toHaveBeenCalledWith("hello world", []);
  });

  it("does NOT submit on Shift+Enter", () => {
    stubFetchOk();
    const onSend = vi.fn();
    render(
      <ThreadPanel
        channelId="ch1"
        parentId="p1"
        onClose={vi.fn()}
        onSend={onSend}
      />,
    );
    const ta = screen.getByRole("textbox", { name: "Thread reply" });
    fireEvent.change(ta, { target: { value: "text" } });
    fireEvent.keyDown(ta, { key: "Enter", shiftKey: true });
    expect(onSend).not.toHaveBeenCalled();
  });

  it("does NOT submit on empty or whitespace-only input", () => {
    stubFetchOk();
    const onSend = vi.fn();
    const { rerender } = render(
      <ThreadPanel
        channelId="ch1"
        parentId="p1"
        onClose={vi.fn()}
        onSend={onSend}
      />,
    );
    const ta = screen.getByRole("textbox", { name: "Thread reply" });
    fireEvent.change(ta, { target: { value: "   " } });
    fireEvent.keyDown(ta, { key: "Enter", shiftKey: false });
    expect(onSend).not.toHaveBeenCalled();
  });

  it("clears input on successful send", async () => {
    stubFetchOk();
    const onSend = vi.fn().mockResolvedValue(undefined);
    render(
      <ThreadPanel
        channelId="ch1"
        parentId="p1"
        onClose={vi.fn()}
        onSend={onSend}
      />,
    );
    const ta = screen.getByRole("textbox", {
      name: "Thread reply",
    }) as HTMLTextAreaElement;
    fireEvent.change(ta, { target: { value: "done" } });
    fireEvent.keyDown(ta, { key: "Enter" });
    await waitFor(() => {
      expect(ta.value).toBe("");
    });
  });

  it("does NOT clear input on failed send", async () => {
    stubFetchOk();
    const onSend = vi.fn().mockRejectedValue(new Error("network error"));
    render(
      <ThreadPanel
        channelId="ch1"
        parentId="p1"
        onClose={vi.fn()}
        onSend={onSend}
      />,
    );
    const ta = screen.getByRole("textbox", {
      name: "Thread reply",
    }) as HTMLTextAreaElement;
    fireEvent.change(ta, { target: { value: "draft" } });
    fireEvent.keyDown(ta, { key: "Enter" });
    await waitFor(() =>
      expect(screen.getByText("network error")).toBeInTheDocument(),
    );
    expect(ta.value).toBe("draft");
  });

  it("disables textarea while sending", async () => {
    stubFetchOk();
    // Create a promise we control so we can inspect the mid-send state.
    let resolveSend!: (v: void) => void;
    const onSend = vi
      .fn()
      .mockImplementation(
        () => new Promise<void>((r) => { resolveSend = r; }),
      );
    render(
      <ThreadPanel
        channelId="ch1"
        parentId="p1"
        onClose={vi.fn()}
        onSend={onSend}
      />,
    );
    const ta = screen.getByRole("textbox", { name: "Thread reply" });
    fireEvent.change(ta, { target: { value: "sending" } });
    fireEvent.keyDown(ta, { key: "Enter" });
    // Mid-send: textarea should be disabled.
    await waitFor(() => expect(ta).toBeDisabled());
    resolveSend();
    await waitFor(() => expect(ta).not.toBeDisabled());
  });

  it("ignores a second Enter while a send is still in-flight", async () => {
    stubFetchOk();
    let resolveSend!: (v: void) => void;
    const onSend = vi
      .fn()
      .mockImplementation(
        () => new Promise<void>((r) => { resolveSend = r; }),
      );
    render(
      <ThreadPanel
        channelId="ch1"
        parentId="p1"
        onClose={vi.fn()}
        onSend={onSend}
      />,
    );
    const ta = screen.getByRole("textbox", { name: "Thread reply" });
    fireEvent.change(ta, { target: { value: "first attempt" } });
    fireEvent.keyDown(ta, { key: "Enter" });
    await waitFor(() => expect(ta).toBeDisabled());
    // Fire a second Enter while sending is still true — should be ignored.
    fireEvent.keyDown(ta, { key: "Enter" });
    expect(onSend).toHaveBeenCalledTimes(1);
    resolveSend();
    await waitFor(() => expect(ta).not.toBeDisabled());
  });

  it("shows send error message on failure", async () => {
    stubFetchOk();
    const onSend = vi.fn().mockRejectedValue(new Error("boom"));
    render(
      <ThreadPanel
        channelId="ch1"
        parentId="p1"
        onClose={vi.fn()}
        onSend={onSend}
      />,
    );
    const ta = screen.getByRole("textbox", { name: "Thread reply" });
    fireEvent.change(ta, { target: { value: "test" } });
    fireEvent.keyDown(ta, { key: "Enter" });
    await waitFor(() => expect(screen.getByText("boom")).toBeInTheDocument());
    const alert = screen.getByRole("alert");
    expect(alert).toHaveTextContent("boom");
  });

  it("shows fallback send error when rejection has no message", async () => {
    stubFetchOk();
    const onSend = vi.fn().mockRejectedValue("raw string");
    render(
      <ThreadPanel
        channelId="ch1"
        parentId="p1"
        onClose={vi.fn()}
        onSend={onSend}
      />,
    );
    const ta = screen.getByRole("textbox", { name: "Thread reply" });
    fireEvent.change(ta, { target: { value: "test" } });
    fireEvent.keyDown(ta, { key: "Enter" });
    await waitFor(() =>
      expect(
        screen.getByText("couldn't send reply"),
      ).toBeInTheDocument(),
    );
  });

  /* ── close ───────────────────────────────────────────────────── */

  it("calls onClose when close button is clicked", async () => {
    stubFetchOk();
    const onClose = vi.fn();
    render(
      <ThreadPanel
        channelId="ch1"
        parentId="p1"
        onClose={onClose}
        onSend={vi.fn()}
      />,
    );
    fireEvent.click(screen.getByRole("button", { name: "Close thread" }));
    expect(onClose).toHaveBeenCalledTimes(1);
  });

  it("calls onClose when Back button is clicked in fullscreen mode", () => {
    stubFetchOk();
    const onClose = vi.fn();
    render(
      <ThreadPanel
        channelId="ch1"
        parentId="p1"
        onClose={onClose}
        onSend={vi.fn()}
        isFullscreen
      />,
    );
    fireEvent.click(screen.getByRole("button", { name: "Back" }));
    expect(onClose).toHaveBeenCalledTimes(1);
  });

  /* ── edge: no parent loaded yet ─────────────────────────────── */

  it("does not render the parent section while parent is still loading", () => {
    // Mock fetch that never resolves → parent stays null.
    vi.stubGlobal(
      "fetch",
      vi.fn().mockImplementation(() => new Promise(() => {})),
    );
    render(
      <ThreadPanel
        channelId="ch1"
        parentId="p1"
        onClose={vi.fn()}
        onSend={vi.fn()}
      />,
    );
    // Parent section should not exist yet.
    expect(
      document.querySelector(".border-shell-border"),
    ).not.toBeInTheDocument();
  });
});
