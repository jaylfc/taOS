import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, fireEvent } from "@testing-library/react";
import { AgentSessionControls } from "../AgentSessionControls";
import {
  sortConversations,
  fetchConversations,
  createConversation,
  type AgentConversation,
} from "../agent-sessions";

beforeEach(() => {
  vi.restoreAllMocks();
});

const OLDER: AgentConversation = {
  id: "ch-old",
  agent: "mary",
  title: "why is the model stuck",
  model: "poolside/laguna-s-2.1:free",
  created_at: 1_700_000_000,
  last_message_at: 1_700_000_100,
};
const NEWER: AgentConversation = {
  id: "ch-new",
  agent: "mary",
  title: "fresh start",
  model: "kilo/code",
  created_at: 1_700_100_000,
  last_message_at: 1_700_100_100,
};

describe("sortConversations", () => {
  it("orders most recent activity first", () => {
    const sorted = sortConversations([OLDER, NEWER]);
    expect(sorted.map((c) => c.id)).toEqual(["ch-new", "ch-old"]);
  });

  it("falls back to created_at when there is no message yet", () => {
    const empty: AgentConversation = {
      id: "ch-empty",
      agent: "mary",
      title: "New conversation",
      created_at: 1_700_200_000,
      last_message_at: null,
    };
    const sorted = sortConversations([NEWER, empty]);
    expect(sorted[0].id).toBe("ch-empty");
  });
});

describe("conversation API", () => {
  it("fetchConversations returns the list most-recent-first", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue({
        ok: true,
        json: async () => ({ conversations: [OLDER, NEWER] }),
      }),
    );
    const list = await fetchConversations("mary");
    expect(list.map((c) => c.id)).toEqual(["ch-new", "ch-old"]);
  });

  it("createConversation starts a fresh session and the old one stays listed", async () => {
    const created: AgentConversation = {
      id: "ch-fresh",
      agent: "mary",
      title: "New conversation",
      model: "kilo/code",
      created_at: 1_700_300_000,
      last_message_at: null,
    };
    const fetchMock = vi
      .fn()
      .mockResolvedValueOnce({
        ok: true,
        json: async () => ({ conversation: created }),
      })
      .mockResolvedValueOnce({
        ok: true,
        json: async () => ({ conversations: [OLDER, created] }),
      });
    vi.stubGlobal("fetch", fetchMock);

    const conv = await createConversation("mary");
    expect(conv.id).toBe("ch-fresh");
    expect(fetchMock.mock.calls[0][1]).toMatchObject({ method: "POST" });

    // The previous conversation is preserved and still listed.
    const list = await fetchConversations("mary");
    expect(list.map((c) => c.id)).toContain("ch-old");
    expect(list[0].id).toBe("ch-fresh");
  });

  it("createConversation sends the project scope when given one", async () => {
    const fetchMock = vi.fn().mockResolvedValue({
      ok: true,
      json: async () => ({ conversation: { id: "ch-p", agent: "mary", title: "New conversation" } }),
    });
    vi.stubGlobal("fetch", fetchMock);
    await createConversation("mary", "prj-7");
    const body = JSON.parse(fetchMock.mock.calls[0][1].body);
    expect(body).toEqual({ project_id: "prj-7" });
  });
});

describe("AgentSessionControls", () => {
  it("lists previous conversations most-recent-first with titles", () => {
    render(
      <AgentSessionControls
        slug="mary"
        conversations={sortConversations([OLDER, NEWER])}
        currentChannelId="ch-new"
        onSelect={vi.fn()}
        onNewChat={vi.fn()}
      />,
    );
    const options = screen.getAllByRole("option") as HTMLOptionElement[];
    expect(options.map((o) => o.value)).toEqual(["ch-new", "ch-old"]);
    expect(options[0].textContent).toContain("fresh start");
    expect(options[1].textContent).toContain("why is the model stuck");
  });

  it("shows the model the current conversation is bound to", () => {
    render(
      <AgentSessionControls
        slug="mary"
        conversations={[NEWER, OLDER]}
        currentChannelId="ch-old"
        onSelect={vi.fn()}
        onNewChat={vi.fn()}
      />,
    );
    expect(
      screen.getByText(/poolside\/laguna-s-2\.1:free/),
    ).toBeInTheDocument();
  });

  it("switches conversations via the selector", () => {
    const onSelect = vi.fn();
    render(
      <AgentSessionControls
        slug="mary"
        conversations={[NEWER, OLDER]}
        currentChannelId="ch-new"
        onSelect={onSelect}
        onNewChat={vi.fn()}
      />,
    );
    fireEvent.change(screen.getByLabelText(/Conversations with mary/), {
      target: { value: "ch-old" },
    });
    expect(onSelect).toHaveBeenCalledWith("ch-old");
  });

  it("New chat button fires onNewChat", () => {
    const onNewChat = vi.fn();
    render(
      <AgentSessionControls
        slug="mary"
        conversations={[NEWER]}
        currentChannelId="ch-new"
        onSelect={vi.fn()}
        onNewChat={onNewChat}
      />,
    );
    fireEvent.click(screen.getByRole("button", { name: /new chat/i }));
    expect(onNewChat).toHaveBeenCalledTimes(1);
  });
});
