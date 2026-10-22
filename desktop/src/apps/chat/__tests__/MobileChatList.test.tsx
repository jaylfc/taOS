import { describe, it, expect, vi } from "vitest";
import { render, screen, fireEvent, within } from "@testing-library/react";
import { MobileChatList, type MobileChatListProps } from "../MobileChatList";
import type { Channel } from "../types";

function ch(overrides: Partial<Channel> & { id: string; name: string }): Channel {
  return { type: "topic", ...overrides };
}

function buildProps(overrides: Partial<MobileChatListProps> = {}): MobileChatListProps {
  return {
    sections: [],
    projectGroups: [],
    selectedChannel: null,
    onSelectChannel: vi.fn(),
    unread: {},
    nowMs: Date.now(),
    formatRelativeTime: (ts) => String(ts),
    bus: { channels: [], available: false, loaded: true },
    busSelected: null,
    onSelectBusChannel: vi.fn(),
    ...overrides,
  };
}

function rowNames(): string[] {
  return screen
    .getAllByRole("button", { name: /^Chat / })
    .map((b) => b.getAttribute("aria-label")!.replace(/^Chat /, ""));
}

describe("MobileChatList", () => {
  it("sorts rows newest last_message_at first across DM, group and project channels", () => {
    const props = buildProps({
      sections: [
        {
          items: [
            ch({ id: "dm1", name: "alice", type: "dm", members: ["user", "alice"], last_message_at: "2026-10-01T10:00:00Z" }),
            ch({ id: "g1", name: "crew", type: "group", last_message_at: "2026-10-03T10:00:00Z" }),
          ],
        },
      ],
      projectGroups: [
        { id: "p1", name: "Strata", channels: [ch({ id: "pc1", name: "strata-dev", project_id: "p1", last_message_at: "2026-10-02T10:00:00Z" })] },
      ],
    });
    render(<MobileChatList {...props} />);
    expect(rowNames()).toEqual(["crew", "strata-dev", "alice"]);
  });

  it("renders a channel present in a section and a project group once", () => {
    const shared = ch({ id: "same", name: "shared-chan", project_id: "p1", last_message_at: "2026-10-01T10:00:00Z" });
    const props = buildProps({
      sections: [{ items: [shared] }],
      projectGroups: [{ id: "p1", name: "Strata", channels: [shared] }],
    });
    render(<MobileChatList {...props} />);
    expect(screen.getAllByRole("button", { name: "Chat shared-chan" })).toHaveLength(1);
  });

  it("Agents chip shows only dm rows and is aria-pressed; a chip with no matches is not rendered", () => {
    const props = buildProps({
      sections: [
        {
          items: [
            ch({ id: "dm1", name: "alice", type: "dm", members: ["user", "alice"] }),
            ch({ id: "t1", name: "random", type: "topic" }),
          ],
        },
      ],
    });
    render(<MobileChatList {...props} />);
    expect(screen.queryByRole("button", { name: "Projects" })).toBeNull();
    const agents = screen.getByRole("button", { name: "Agents" });
    expect(agents).toHaveAttribute("aria-pressed", "false");
    fireEvent.click(agents);
    expect(agents).toHaveAttribute("aria-pressed", "true");
    expect(rowNames()).toEqual(["alice"]);
  });

  it("search 'pizza' keeps only the row whose lastPreview contains Pizza, and shows No chats match for nothing", () => {
    const props = buildProps({
      sections: [
        {
          items: [
            ch({ id: "a", name: "kitchen", lastPreview: "Pizza tonight?" }),
            ch({ id: "b", name: "garage", lastPreview: "wrench set" }),
          ],
        },
      ],
    });
    render(<MobileChatList {...props} />);
    const search = screen.getByRole("searchbox", { name: "Search chats" });
    fireEvent.change(search, { target: { value: "pizza" } });
    expect(rowNames()).toEqual(["kitchen"]);
    fireEvent.change(search, { target: { value: "zzz-no-match" } });
    expect(screen.getByText("No chats match")).toBeInTheDocument();
  });

  it("shows the unread count, and No messages yet when there is no preview", () => {
    const props = buildProps({
      sections: [{ items: [ch({ id: "a", name: "busy", lastPreview: "hi" }), ch({ id: "b", name: "quiet" })] }],
      unread: { a: 3 },
    });
    render(<MobileChatList {...props} />);
    expect(within(screen.getByRole("button", { name: "Chat busy" })).getByText("3")).toBeInTheDocument();
    expect(within(screen.getByRole("button", { name: "Chat quiet" })).getByText("No messages yet")).toBeInTheDocument();
  });

  it("selecting a row calls onSelectChannel with its id", () => {
    const onSelectChannel = vi.fn();
    const props = buildProps({ sections: [{ items: [ch({ id: "a", name: "busy" })] }], onSelectChannel });
    render(<MobileChatList {...props} />);
    fireEvent.click(screen.getByRole("button", { name: "Chat busy" }));
    expect(onSelectChannel).toHaveBeenCalledWith("a");
  });

  it("bus channel names appear only after Agent activity is opened; no row without bus channels", () => {
    const bus = {
      channels: [{ channel: "coord-alpha" }, { channel: "coord-beta" }],
      available: true,
      loaded: true,
    };
    const props = buildProps({ sections: [{ items: [ch({ id: "a", name: "busy" })] }], bus });
    const { unmount } = render(<MobileChatList {...props} />);
    expect(screen.queryByText("coord-alpha")).toBeNull();
    const toggle = screen.getByRole("button", { name: /Agent activity/ });
    expect(toggle).toHaveAttribute("aria-expanded", "false");
    expect(screen.getByText("2 channels")).toBeInTheDocument();
    fireEvent.click(toggle);
    expect(toggle).toHaveAttribute("aria-expanded", "true");
    expect(screen.getByText("coord-alpha")).toBeInTheDocument();
    unmount();

    render(<MobileChatList {...buildProps({ sections: [{ items: [ch({ id: "a", name: "busy" })] }] })} />);
    expect(screen.queryByRole("button", { name: /Agent activity/ })).toBeNull();
  });

  it("sortKey falls back to created_at when last_message_at is empty string", () => {
    const props = buildProps({
      sections: [
        {
          items: [
            ch({ id: "a", name: "alpha", created_at: "2026-10-01T10:00:00Z", last_message_at: "" }),
            ch({ id: "b", name: "beta", created_at: "2026-10-03T10:00:00Z", last_message_at: "" }),
          ],
        },
      ],
    });
    render(<MobileChatList {...props} />);
    expect(rowNames()).toEqual(["beta", "alpha"]);
  });

  it("search matches project name in project channels", () => {
    const props = buildProps({
      sections: [],
      projectGroups: [
        { id: "p1", name: "Strata", channels: [ch({ id: "pc1", name: "general", project_id: "p1" })] },
      ],
    });
    render(<MobileChatList {...props} />);
    const search = screen.getByRole("searchbox", { name: "Search chats" });
    fireEvent.change(search, { target: { value: "strata" } });
    expect(rowNames()).toEqual(["general"]);
  });

  it("resets filter to all when active filter has no matches after rows change", () => {
    const { rerender } = render(
      <MobileChatList
        {...buildProps({
          sections: [
            {
              items: [
                ch({ id: "dm1", name: "alice", type: "dm", members: ["user", "alice"] }),
                ch({ id: "t1", name: "random", type: "topic" }),
              ],
            },
          ],
        })}
      />,
    );
    const agentsChip = screen.getByRole("button", { name: "Agents" });
    fireEvent.click(agentsChip);
    expect(agentsChip).toHaveAttribute("aria-pressed", "true");
    expect(rowNames()).toEqual(["alice"]);

    rerender(
      <MobileChatList
        {...buildProps({
          sections: [
            {
              items: [ch({ id: "t1", name: "random", type: "topic" })],
            },
          ],
        })}
      />,
    );
    expect(screen.getByRole("button", { name: "All" })).toHaveAttribute("aria-pressed", "true");
    expect(screen.queryByRole("button", { name: "Agents" })).not.toBeInTheDocument();
    expect(rowNames()).toEqual(["random"]);
  });

  it("excludes a2a-kind channels from All and Projects filters", () => {
    const props = buildProps({
      sections: [
        {
          items: [
            ch({
              id: "a2a-chan",
              name: "a2a",
              type: "group",
              settings: { kind: "a2a" },
              last_message_at: "2026-10-01T10:00:00Z",
            }),
            ch({ id: "g1", name: "real-group", type: "group", last_message_at: "2026-10-02T10:00:00Z" }),
          ],
        },
      ],
      projectGroups: [
        { id: "p1", name: "Strata", channels: [ch({ id: "pc1", name: "strata-dev", project_id: "p1", settings: { kind: "a2a" }, last_message_at: "2026-10-03T10:00:00Z" })] },
      ],
    });
    render(<MobileChatList {...props} />);
    expect(rowNames()).toEqual(["real-group"]);
    expect(screen.queryByRole("button", { name: "Chat a2a" })).toBeNull();
    expect(screen.queryByRole("button", { name: "Chat strata-dev" })).toBeNull();
  });

  it("renders a group named a2a when it has no settings.kind", () => {
    const props = buildProps({
      sections: [
        {
          items: [
            ch({ id: "g1", name: "a2a", type: "group", last_message_at: "2026-10-01T10:00:00Z" }),
          ],
        },
      ],
    });
    render(<MobileChatList {...props} />);
    expect(rowNames()).toEqual(["a2a"]);
    expect(screen.getByRole("button", { name: "Chat a2a" })).toBeInTheDocument();
  });

  it("People chip splits human DMs from agent DMs when agentNames is provided", () => {
    const props = buildProps({
      agentNames: ["juniper"],
      sections: [
        {
          items: [
            ch({ id: "dm-agent", name: "juniper", type: "dm", members: ["user", "juniper"], last_message_at: "2026-10-01T10:00:00Z" }),
            ch({ id: "dm-human", name: "mum", type: "dm", members: ["user", "mum"], last_message_at: "2026-10-02T10:00:00Z" }),
            ch({ id: "dm-remote", name: "peer", type: "dm-remote", members: ["user", "hub:peer"], last_message_at: "2026-10-03T10:00:00Z" }),
            ch({ id: "group", name: "crew", type: "group", last_message_at: "2026-10-04T10:00:00Z" }),
          ],
        },
      ],
    });
    render(<MobileChatList {...props} />);

    const peopleChip = screen.getByRole("button", { name: "People" });
    expect(peopleChip).toBeInTheDocument();

    fireEvent.click(peopleChip);
    expect(peopleChip).toHaveAttribute("aria-pressed", "true");
    expect(rowNames()).toEqual(["peer", "mum"]);

    const agentsChip = screen.getByRole("button", { name: "Agents" });
    fireEvent.click(agentsChip);
    expect(agentsChip).toHaveAttribute("aria-pressed", "true");
    expect(rowNames()).toEqual(["juniper"]);

    const allChip = screen.getByRole("button", { name: "All" });
    fireEvent.click(allChip);
    expect(allChip).toHaveAttribute("aria-pressed", "true");
    expect(rowNames()).toEqual(["crew", "peer", "mum", "juniper"]);
  });

  it("without agentNames all DMs stay under Agents and People chip is absent (back-compat)", () => {
    const props = buildProps({
      sections: [
        {
          items: [
            ch({ id: "dm-agent", name: "juniper", type: "dm", members: ["user", "juniper"], last_message_at: "2026-10-01T10:00:00Z" }),
            ch({ id: "dm-human", name: "mum", type: "dm", members: ["user", "mum"], last_message_at: "2026-10-02T10:00:00Z" }),
            ch({ id: "dm-remote", name: "peer", type: "dm-remote", members: ["user", "hub:peer"], last_message_at: "2026-10-03T10:00:00Z" }),
          ],
        },
      ],
    });
    render(<MobileChatList {...props} />);

    expect(screen.queryByRole("button", { name: "People" })).toBeNull();

    const agentsChip = screen.getByRole("button", { name: "Agents" });
    fireEvent.click(agentsChip);
    expect(agentsChip).toHaveAttribute("aria-pressed", "true");
    expect(rowNames()).toEqual(["peer", "mum", "juniper"]);
  });
});
