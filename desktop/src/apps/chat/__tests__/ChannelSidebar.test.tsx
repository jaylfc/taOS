import { describe, it, expect, vi } from "vitest";
import { render, screen, fireEvent } from "@testing-library/react";
import { ChannelSidebar, type ChannelSidebarProps } from "../ChannelSidebar";
import type { Channel, SectionDef } from "../ChannelSidebar";

/* ------------------------------------------------------------------ */
/*  Test factories                                                     */
/* ------------------------------------------------------------------ */

function makeChannel(overrides: Partial<Channel> = {}): Channel {
  return {
    id: overrides.id ?? "ch-1",
    name: overrides.name ?? "general",
    type: overrides.type ?? "topic",
    ...overrides,
  };
}

function makeDmChannel(overrides: Partial<Channel> = {}): Channel {
  return makeChannel({
    id: "dm-1",
    name: "alice",
    type: "dm",
    members: ["user", "alice"],
    ...overrides,
  });
}

function makeGroupChannel(overrides: Partial<Channel> = {}): Channel {
  return makeChannel({
    id: "grp-1",
    name: "engineering",
    type: "group",
    members: ["user", "bob", "carol"],
    ...overrides,
  });
}

function makeA2aChannel(overrides: Partial<Channel> = {}): Channel {
  return makeChannel({
    id: "a2a-1",
    name: "coord",
    type: "topic",
    settings: { kind: "a2a" },
    ...overrides,
  });
}

function makeSection(label: string, channels: Channel[]): SectionDef {
  return { label, icon: <span data-testid={`icon-${label}`} />, items: channels };
}

function buildProps(overrides: Partial<ChannelSidebarProps> = {}): ChannelSidebarProps {
  return {
    isMobile: false,
    wsStatus: "connected",
    allEmpty: false,
    sections: [],
    collapsedSections: {},
    onToggleSection: vi.fn(),
    visibleInSection: (items) => items,
    selectedChannel: null,
    onSelectChannel: vi.fn(),
    unread: {},
    nowMs: Date.now(),
    liveAgents: [],
    archivedAgents: [],
    archivedChannels: [],
    archivedExpanded: false,
    onToggleArchived: vi.fn(),
    scope: undefined,
    projectGroups: [],
    projectsExpanded: false,
    onToggleProjects: vi.fn(),
    projectChannelExpanded: {},
    onToggleProjectChannel: vi.fn(),
    onOpenAgentsApp: vi.fn(),
    onRestoreArchivedChannel: vi.fn(),
    onDeleteArchivedChannel: vi.fn(),
    bus: { channels: [], available: false, loaded: false },
    busSelected: null,
    onSelectBusChannel: vi.fn(),
    formatRelativeTime: (ts) => String(ts),
    thinkingChannelIds: [],
    ...overrides,
  };
}

/* ------------------------------------------------------------------ */
/*  Desktop layout                                                     */
/* ------------------------------------------------------------------ */

describe("ChannelSidebar — desktop", () => {
  it("renders connection status 'Connected' when wsStatus is connected", () => {
    render(<ChannelSidebar {...buildProps({ wsStatus: "connected" })} />);
    expect(screen.getByText("Connected")).toBeInTheDocument();
  });

  it("renders connection status 'Connecting...' when wsStatus is connecting", () => {
    render(<ChannelSidebar {...buildProps({ wsStatus: "connecting" })} />);
    expect(screen.getByText("Connecting...")).toBeInTheDocument();
  });

  it("renders connection status 'Offline' when wsStatus is disconnected", () => {
    render(<ChannelSidebar {...buildProps({ wsStatus: "disconnected" })} />);
    expect(screen.getByText("Offline")).toBeInTheDocument();
  });

  it("renders empty state when allEmpty is true", () => {
    render(<ChannelSidebar {...buildProps({ allEmpty: true })} />);
    expect(screen.getByText("No conversations yet")).toBeInTheDocument();
    expect(screen.getByText("Deploy an agent to start chatting")).toBeInTheDocument();
  });

  it("renders 'Open Agents' button in empty state and calls onOpenAgentsApp on click", () => {
    const onOpenAgentsApp = vi.fn();
    render(<ChannelSidebar {...buildProps({ allEmpty: true, onOpenAgentsApp })} />);
    fireEvent.click(screen.getByRole("button", { name: /Open Agents/i }));
    expect(onOpenAgentsApp).toHaveBeenCalledOnce();
  });

  it("renders section headers", () => {
    const sections = [makeSection("Topics", [makeChannel()])];
    render(<ChannelSidebar {...buildProps({ sections })} />);
    expect(screen.getByText("Topics")).toBeInTheDocument();
  });

  it("calls onToggleSection when a section header is clicked", () => {
    const onToggleSection = vi.fn();
    const sections = [makeSection("Topics", [makeChannel()])];
    render(<ChannelSidebar {...buildProps({ sections, onToggleSection })} />);
    fireEvent.click(screen.getByText("Topics"));
    expect(onToggleSection).toHaveBeenCalledWith("Topics");
  });

  it("shows 'None yet' when a section has no items and is expanded", () => {
    const sections = [makeSection("Topics", [])];
    render(
      <ChannelSidebar
        {...buildProps({ sections, collapsedSections: { Topics: false } })}
      />,
    );
    expect(screen.getByText("None yet")).toBeInTheDocument();
  });

  it("does not show 'None yet' when section is collapsed", () => {
    const sections = [makeSection("Topics", [])];
    const { container } = render(
      <ChannelSidebar
        {...buildProps({ sections, collapsedSections: { Topics: true } })}
      />,
    );
    // The text "None yet" should NOT be visible because the section body is hidden
    expect(screen.queryByText("None yet")).not.toBeInTheDocument();
  });

  it("renders channel names in sections", () => {
    const ch = makeChannel({ id: "ch-1", name: "general" });
    const sections = [makeSection("Topics", [ch])];
    render(<ChannelSidebar {...buildProps({ sections })} />);
    expect(screen.getByText("general")).toBeInTheDocument();
  });

  it("calls onSelectChannel when a channel button is clicked", () => {
    const onSelectChannel = vi.fn();
    const ch = makeChannel({ id: "ch-1", name: "general" });
    const sections = [makeSection("Topics", [ch])];
    render(
      <ChannelSidebar {...buildProps({ sections, onSelectChannel })} />,
    );
    fireEvent.click(screen.getByText("general"));
    expect(onSelectChannel).toHaveBeenCalledWith("ch-1");
  });

  it("sets aria-pressed on the selected channel", () => {
    const ch = makeChannel({ id: "ch-1" });
    const sections = [makeSection("Topics", [ch])];
    render(
      <ChannelSidebar {...buildProps({ sections, selectedChannel: "ch-1" })} />,
    );
    // Use aria-label to find the channel button then check aria-pressed
    const btn = screen.getByRole("button", { name: `Channel ${ch.name}` });
    expect(btn).toHaveAttribute("aria-pressed", "true");
  });

  it("shows unread badge when count > 0", () => {
    const ch = makeChannel({ id: "ch-1" });
    const sections = [makeSection("Topics", [ch])];
    render(
      <ChannelSidebar {...buildProps({ sections, unread: { "ch-1": 5 } })} />,
    );
    expect(screen.getByText("5")).toBeInTheDocument();
  });

  it("does not show unread badge when count is 0", () => {
    const ch = makeChannel({ id: "ch-1" });
    const sections = [makeSection("Topics", [ch])];
    const { container } = render(
      <ChannelSidebar {...buildProps({ sections, unread: { "ch-1": 0 } })} />,
    );
    // The unread badge span should not exist
    const badges = container.querySelectorAll(".bg-unread");
    expect(badges.length).toBe(0);
  });
});

/* ------------------------------------------------------------------ */
/*  Channel type variants (desktop)                                     */
/* ------------------------------------------------------------------ */

describe("ChannelSidebar — channel type icons", () => {
  it("renders Users icon for group channels", () => {
    const ch = makeGroupChannel();
    const sections = [makeSection("Groups", [ch])];
    const { container } = render(
      <ChannelSidebar {...buildProps({ sections })} />,
    );
    // The Users icon is rendered inside a span with aria-hidden="true"
    // We verify the component renders without error — the icon itself is from lucide
    expect(screen.getByText(ch.name)).toBeInTheDocument();
    // Group icon renders a span with specific classes
    const icons = container.querySelectorAll(
      ".grid.place-items-center.w-\\[30px\\].h-\\[30px\\]",
    );
    expect(icons.length).toBeGreaterThan(0);
  });

  it("renders Hash icon for topic channels", () => {
    const ch = makeChannel({ type: "topic" });
    const sections = [makeSection("Topics", [ch])];
    const { container } = render(
      <ChannelSidebar {...buildProps({ sections })} />,
    );
    expect(screen.getByText(ch.name)).toBeInTheDocument();
    // Verify the lucide Hash SVG icon is rendered inside the channel row
    const channelRow = screen.getByLabelText(`Channel ${ch.name}`);
    expect(channelRow.querySelector("svg")).toBeTruthy();
  });

  it("renders MessageAvatar for DM channels with an agent member", () => {
    const ch = makeDmChannel();
    const sections = [makeSection("DMs", [ch])];
    const { container } = render(
      <ChannelSidebar {...buildProps({ sections })} />,
    );
    expect(screen.getByText(ch.name)).toBeInTheDocument();
    // Verify the MessageAvatar is rendered inside the DM channel row
    // (it renders a div with aria-hidden="true")
    const channelRow = screen.getByLabelText(`Channel ${ch.name}`);
    expect(
      channelRow.querySelector("[aria-hidden]"),
    ).toBeTruthy();
  });

  it("renders Bot icon for A2A channels", () => {
    const ch = makeA2aChannel();
    const sections = [makeSection("Channels", [ch])];
    const { container } = render(
      <ChannelSidebar {...buildProps({ sections })} />,
    );
    expect(screen.getByText(ch.name)).toBeInTheDocument();
    // A2A channels show a span with Bot icon and specific styling
    const a2aSpan = container.querySelector(".bg-accent-soft");
    expect(a2aSpan).toBeTruthy();
  });
});

/* ------------------------------------------------------------------ */
/*  Mobile layout                                                      */
/* ------------------------------------------------------------------ */

describe("ChannelSidebar — mobile", () => {
  it("renders connection status with mobile inline styles", () => {
    const { container } = render(
      <ChannelSidebar {...buildProps({ isMobile: true, wsStatus: "connected" })} />,
    );
    expect(screen.getByText("Connected")).toBeInTheDocument();
    // Mobile uses inline styles — verify the wrapper div padding via longhands
    // (jsdom re-serialises shorthands; asserting longhands is stable across versions)
    const mobileContainer = container.firstChild as HTMLElement;
    expect(mobileContainer.style.paddingTop).toBe("8px");
    expect(mobileContainer.style.paddingRight).toBe("0px");
    expect(mobileContainer.style.paddingBottom).toBe("16px");
  });

  it("renders empty state on mobile", () => {
    render(
      <ChannelSidebar {...buildProps({ isMobile: true, allEmpty: true })} />,
    );
    expect(screen.getByText("No conversations yet")).toBeInTheDocument();
  });

  it("calls onOpenAgentsApp from mobile empty state", () => {
    const onOpenAgentsApp = vi.fn();
    render(
      <ChannelSidebar
        {...buildProps({ isMobile: true, allEmpty: true, onOpenAgentsApp })}
      />,
    );
    // Mobile empty state uses a <button> not the Button component
    fireEvent.click(screen.getByText("Open Agents"));
    expect(onOpenAgentsApp).toHaveBeenCalledOnce();
  });

  it("renders channels with mobile inline styles", () => {
    const ch = makeChannel({ id: "ch-1", name: "general" });
    const sections = [makeSection("Topics", [ch])];
    render(
      <ChannelSidebar {...buildProps({ isMobile: true, sections })} />,
    );
    expect(screen.getByText("general")).toBeInTheDocument();
  });

  it("shows unread badge on mobile", () => {
    const ch = makeChannel({ id: "ch-1" });
    const sections = [makeSection("Topics", [ch])];
    render(
      <ChannelSidebar
        {...buildProps({ isMobile: true, sections, unread: { "ch-1": 3 } })}
      />,
    );
    expect(screen.getByText("3")).toBeInTheDocument();
  });

  it("renders lastPreview text on mobile", () => {
    const ch = makeChannel({ id: "ch-1", lastPreview: "Hey, how are you?" });
    const sections = [makeSection("Topics", [ch])];
    render(
      <ChannelSidebar {...buildProps({ isMobile: true, sections })} />,
    );
    expect(screen.getByText("Hey, how are you?")).toBeInTheDocument();
  });

  it("renders relative time on mobile when last_message_at is set", () => {
    const ch = makeChannel({ id: "ch-1", last_message_at: "2026-01-01T00:00:00Z" });
    const sections = [makeSection("Topics", [ch])];
    render(
      <ChannelSidebar {...buildProps({ isMobile: true, sections })} />,
    );
    // formatRelativeTime default just stringifies — check it rendered something
    expect(screen.getByText("2026-01-01T00:00:00Z")).toBeInTheDocument();
  });

  it("calls onSelectChannel on mobile channel click", () => {
    const onSelectChannel = vi.fn();
    const ch = makeChannel({ id: "ch-1" });
    const sections = [makeSection("Topics", [ch])];
    render(
      <ChannelSidebar
        {...buildProps({ isMobile: true, sections, onSelectChannel })}
      />,
    );
    fireEvent.click(screen.getByRole("button", { name: `Channel ${ch.name}` }));
    expect(onSelectChannel).toHaveBeenCalledWith("ch-1");
  });
});

/* ------------------------------------------------------------------ */
/*  Archived section                                                   */
/* ------------------------------------------------------------------ */

describe("ChannelSidebar — archived", () => {
  const archivedCh = makeChannel({
    id: "arch-1",
    name: "old-project",
    settings: { archived: true, archived_agent_id: "agent-1" },
  });

  it("does not render archived section when there are no archived channels", () => {
    const { container } = render(
      <ChannelSidebar {...buildProps({ archivedChannels: [] })} />,
    );
    expect(screen.queryByText(/Archived/)).not.toBeInTheDocument();
  });

  it("renders archived section header on desktop", () => {
    render(
      <ChannelSidebar
        {...buildProps({
          archivedChannels: [archivedCh],
          archivedExpanded: false,
        })}
      />,
    );
    expect(screen.getByText(/Archived/)).toBeInTheDocument();
  });

  it("shows archived channel count in header", () => {
    render(
      <ChannelSidebar
        {...buildProps({
          archivedChannels: [archivedCh, makeChannel({ id: "arch-2", name: "old2" })],
          archivedExpanded: false,
        })}
      />,
    );
    expect(screen.getByText(/Archived \(2\)/)).toBeInTheDocument();
  });

  it("calls onToggleArchived when header is clicked (desktop)", () => {
    const onToggleArchived = vi.fn();
    render(
      <ChannelSidebar
        {...buildProps({
          archivedChannels: [archivedCh],
          archivedExpanded: false,
          onToggleArchived,
        })}
      />,
    );
    fireEvent.click(screen.getByText(/Archived/));
    expect(onToggleArchived).toHaveBeenCalledOnce();
  });

  it("renders archived channel names when expanded (desktop)", () => {
    render(
      <ChannelSidebar
        {...buildProps({
          archivedChannels: [archivedCh],
          archivedExpanded: true,
        })}
      />,
    );
    // Find the channel via the aria-label on the Button component inside the archived section
    expect(
      screen.getByRole("button", { name: `Archived channel ${archivedCh.name}` }),
    ).toBeInTheDocument();
  });

  it("calls onRestoreArchivedChannel when restore is clicked", () => {
    const onRestoreArchivedChannel = vi.fn();
    render(
      <ChannelSidebar
        {...buildProps({
          archivedChannels: [archivedCh],
          archivedExpanded: true,
          archivedAgents: [{ id: "agent-1", archived_slug: "agent-1" }],
          onRestoreArchivedChannel,
        })}
      />,
    );
    const restoreBtn = screen.getByRole("button", {
      name: `Restore archived channel ${archivedCh.name}`,
    });
    expect(restoreBtn).not.toBeDisabled();
    fireEvent.click(restoreBtn);
    expect(onRestoreArchivedChannel).toHaveBeenCalledWith("arch-1", "old-project");
  });

  it("disables restore button when archived agent is missing", () => {
    const onRestoreArchivedChannel = vi.fn();
    render(
      <ChannelSidebar
        {...buildProps({
          archivedChannels: [archivedCh],
          archivedExpanded: true,
          archivedAgents: [],
          onRestoreArchivedChannel,
        })}
      />,
    );
    const restoreBtn = screen.getByRole("button", {
      name: `Restore archived channel ${archivedCh.name}`,
    });
    expect(restoreBtn).toBeDisabled();
  });

  it("calls onDeleteArchivedChannel when delete is clicked", () => {
    const onDeleteArchivedChannel = vi.fn();
    render(
      <ChannelSidebar
        {...buildProps({
          archivedChannels: [archivedCh],
          archivedExpanded: true,
          archivedAgents: [{ id: "agent-1", archived_slug: "agent-1" }],
          onDeleteArchivedChannel,
        })}
      />,
    );
    const deleteBtn = screen.getByRole("button", {
      name: `Permanently delete archived channel ${archivedCh.name}`,
    });
    fireEvent.click(deleteBtn);
    expect(onDeleteArchivedChannel).toHaveBeenCalledWith("arch-1");
  });

  it("renders archived section on mobile", () => {
    render(
      <ChannelSidebar
        {...buildProps({
          isMobile: true,
          archivedChannels: [archivedCh],
          archivedExpanded: true,
          archivedAgents: [{ id: "agent-1", archived_slug: "agent-1" }],
        })}
      />,
    );
    expect(screen.getByText(/Archived/)).toBeInTheDocument();
  });
});

/* ------------------------------------------------------------------ */
/*  Projects section (desktop + mobile)                                */
/* ------------------------------------------------------------------ */

describe("ChannelSidebar — projects", () => {
  const projectGroup = {
    id: "proj-1",
    name: "My Project",
    channels: [
      makeChannel({ id: "pc-1", name: "proj-general" }),
      makeChannel({ id: "pc-2", name: "proj-dev" }),
    ],
  };

  it("renders Projects section on desktop when projectGroups has items", () => {
    render(
      <ChannelSidebar
        {...buildProps({ projectGroups: [projectGroup] })}
      />,
    );
    expect(screen.getByText("Projects")).toBeInTheDocument();
  });

  it("hides Projects section when projectGroups is empty", () => {
    render(<ChannelSidebar {...buildProps({ projectGroups: [] })} />);
    expect(screen.queryByText("Projects")).not.toBeInTheDocument();
  });

  it("hides Projects section when scope has projectId", () => {
    render(
      <ChannelSidebar
        {...buildProps({
          projectGroups: [projectGroup],
          scope: { projectId: "proj-1" },
        })}
      />,
    );
    expect(screen.queryByText("Projects")).not.toBeInTheDocument();
  });

  it("renders project channel names", () => {
    render(
      <ChannelSidebar
        {...buildProps({ projectGroups: [projectGroup] })}
      />,
    );
    expect(screen.getByText("proj-general")).toBeInTheDocument();
    expect(screen.getByText("proj-dev")).toBeInTheDocument();
  });

  it("calls onSelectChannel when a project channel is clicked", () => {
    const onSelectChannel = vi.fn();
    render(
      <ChannelSidebar
        {...buildProps({
          projectGroups: [projectGroup],
          onSelectChannel,
        })}
      />,
    );
    fireEvent.click(screen.getByText("proj-general"));
    expect(onSelectChannel).toHaveBeenCalledWith("pc-1");
  });

  it("renders Projects section on mobile", () => {
    render(
      <ChannelSidebar
        {...buildProps({ isMobile: true, projectGroups: [projectGroup] })}
      />,
    );
    expect(screen.getByText(/Projects/)).toBeInTheDocument();
  });

  it("calls onToggleProjects on mobile Projects header click", () => {
    const onToggleProjects = vi.fn();
    render(
      <ChannelSidebar
        {...buildProps({
          isMobile: true,
          projectGroups: [projectGroup],
          projectsExpanded: false,
          onToggleProjects,
        })}
      />,
    );
    fireEvent.click(screen.getByText(/Projects/));
    expect(onToggleProjects).toHaveBeenCalledOnce();
  });
});

/* ------------------------------------------------------------------ */
/*  A2A bus section                                                    */
/* ------------------------------------------------------------------ */

describe("ChannelSidebar — A2A bus", () => {
  it("renders A2A bus section via A2aBusSection", () => {
    const busChannels = [
      { channel: "bus-chan", members: ["a", "b"], message_count: 10 },
    ];
    render(
      <ChannelSidebar
        {...buildProps({
          bus: { channels: busChannels, available: true, loaded: true },
          busSelected: "bus-chan",
        })}
      />,
    );
    // A2aBusSection renders the channel name
    expect(screen.getByText("bus-chan")).toBeInTheDocument();
  });

  it("renders A2A bus on mobile too", () => {
    const busChannels = [
      { channel: "mobile-bus", members: ["x"], message_count: 3 },
    ];
    render(
      <ChannelSidebar
        {...buildProps({
          isMobile: true,
          bus: { channels: busChannels, available: true, loaded: true },
        })}
      />,
    );
    expect(screen.getByText("mobile-bus")).toBeInTheDocument();
  });
});

/* ------------------------------------------------------------------ */
/*  Edge cases                                                         */
/* ------------------------------------------------------------------ */

describe("ChannelSidebar — edge cases", () => {
  it("renders multiple sections independently", () => {
    const sections = [
      makeSection("Topics", [makeChannel({ id: "a", name: "alpha" })]),
      makeSection("DMs", [makeDmChannel({ id: "b", name: "beta" })]),
      makeSection("Groups", [makeGroupChannel({ id: "c", name: "gamma" })]),
    ];
    render(<ChannelSidebar {...buildProps({ sections })} />);
    expect(screen.getByText("alpha")).toBeInTheDocument();
    expect(screen.getByText("beta")).toBeInTheDocument();
    expect(screen.getByText("gamma")).toBeInTheDocument();
  });

  it("marks a collapsed section with rotate-0 (no rotation)", () => {
    const sections = [makeSection("Topics", [makeChannel()])];
    render(
      <ChannelSidebar
        {...buildProps({ sections, collapsedSections: { Topics: true } })}
      />,
    );
    // The ChevronRight icon should NOT have the rotate-90 class
    const chevron = document.querySelector(".transition-transform");
    expect(chevron?.className).not.toContain("rotate-90");
  });

  it("marks an expanded section with rotate-90 class", () => {
    const sections = [makeSection("Topics", [makeChannel()])];
    const { container } = render(
      <ChannelSidebar
        {...buildProps({ sections, collapsedSections: { Topics: false } })}
      />,
    );
    // The section header button contains the ChevronRight icon with the rotate-90 class
    const headerBtn = screen.getByText("Topics").closest("button")!;
    const svg = headerBtn.querySelector("svg");
    expect(svg?.className.baseVal || svg?.getAttribute("class")).toContain(
      "rotate-90",
    );
  });

  it("uses visibleInSection filter for channel lists", () => {
    const channels = [makeChannel({ id: "a" }), makeChannel({ id: "b" })];
    const sections = [makeSection("Topics", channels)];
    const visibleInSection = vi.fn().mockReturnValue([channels[0]]);
    render(
      <ChannelSidebar {...buildProps({ sections, visibleInSection })} />,
    );
    // visibleInSection is called; it filters — only "a" renders
    expect(visibleInSection).toHaveBeenCalled();
  });

  it("renders connection status on mobile for all states", () => {
    const { rerender } = render(
      <ChannelSidebar {...buildProps({ isMobile: true, wsStatus: "connecting" })} />,
    );
    expect(screen.getByText("Connecting…")).toBeInTheDocument();

    rerender(
      <ChannelSidebar {...buildProps({ isMobile: true, wsStatus: "disconnected" })} />,
    );
    expect(screen.getByText("Offline")).toBeInTheDocument();
  });

  it("renders A2A channel with title tooltip on desktop", () => {
    const ch = makeA2aChannel();
    const sections = [makeSection("Channels", [ch])];
    render(<ChannelSidebar {...buildProps({ sections })} />);
    const btn = screen.getByRole("button", { name: `Channel ${ch.name}` });
    expect(btn).toHaveAttribute(
      "title",
      "Agent coordination — mention @<slug> to hand off.",
    );
  });

  it("does not set tooltip on non-A2A channels", () => {
    const ch = makeChannel();
    const sections = [makeSection("Topics", [ch])];
    render(<ChannelSidebar {...buildProps({ sections })} />);
    const btn = screen.getByRole("button", { name: `Channel ${ch.name}` });
    expect(btn).not.toHaveAttribute("title");
  });
});

/* ------------------------------------------------------------------ */
/*  Live thinking badge                                                 */
/* ------------------------------------------------------------------ */

describe("ChannelSidebar — thinking badge", () => {
  it("shows a pulsing amber dot on a thinking channel (desktop)", () => {
    const ch = makeChannel({ id: "ch-1", name: "session-alice" });
    const sections = [makeSection("Live", [ch])];
    const { container } = render(
      <ChannelSidebar
        {...buildProps({ sections, thinkingChannelIds: ["ch-1"] })}
      />,
    );
    const btn = screen.getByRole("button", { name: `Channel ${ch.name}` });
    const dot = btn.querySelector(".taos-status-pulse");
    expect(dot).toBeTruthy();
    expect(dot).toHaveClass("bg-amber-400");
  });

  it("does not show thinking dot on non-thinking channels (desktop)", () => {
    const ch = makeChannel({ id: "ch-1", name: "general" });
    const sections = [makeSection("Topics", [ch])];
    render(
      <ChannelSidebar
        {...buildProps({ sections, thinkingChannelIds: [] })}
      />,
    );
    const btn = screen.getByRole("button", { name: `Channel ${ch.name}` });
    expect(btn.querySelector(".taos-status-pulse")).toBeNull();
  });

  it("shows thinking dot on mobile when channel is thinking", () => {
    const ch = makeChannel({ id: "ch-1", name: "session-bob" });
    const sections = [makeSection("Live", [ch])];
    const { container } = render(
      <ChannelSidebar
        {...buildProps({ isMobile: true, sections, thinkingChannelIds: ["ch-1"] })}
      />,
    );
    const btn = screen.getByRole("button", { name: `Channel ${ch.name}` });
    const dot = btn.querySelector(".taos-status-pulse");
    expect(dot).toBeTruthy();
    expect(dot).toHaveClass("bg-amber-400");
  });

  it("clears thinking dot when channel leaves thinkingChannelIds", () => {
    const ch = makeChannel({ id: "ch-1", name: "session-carol" });
    const sections = [makeSection("Live", [ch])];
    const { rerender } = render(
      <ChannelSidebar
        {...buildProps({ sections, thinkingChannelIds: ["ch-1"] })}
      />,
    );
    let btn = screen.getByRole("button", { name: `Channel ${ch.name}` });
    expect(btn.querySelector(".taos-status-pulse")).toBeTruthy();

    rerender(
      <ChannelSidebar
        {...buildProps({ sections, thinkingChannelIds: [] })}
      />,
    );
    btn = screen.getByRole("button", { name: `Channel ${ch.name}` });
    expect(btn.querySelector(".taos-status-pulse")).toBeNull();
  });
});
