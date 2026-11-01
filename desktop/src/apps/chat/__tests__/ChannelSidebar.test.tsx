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
     agentPresence: {},
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

  it("a setup note replaces the empty state and drops Open Agents while busy", () => {
    render(<ChannelSidebar {...buildProps({ allEmpty: true, emptyNote: { title: "Setting up your assistant", detail: "Soon.", tone: "busy" } })} />);
    expect(screen.getByText("Setting up your assistant")).toBeInTheDocument();
    expect(screen.getByText("Soon.")).toBeInTheDocument();
    expect(screen.queryByText("Deploy an agent to start chatting")).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /Open Agents/i })).not.toBeInTheDocument();
  });

  it("an error note keeps Open Agents", () => {
    render(<ChannelSidebar {...buildProps({ allEmpty: true, emptyNote: { title: "Your assistant isn't ready", detail: "Failed.", tone: "error" } })} />);
    expect(screen.getByText("Failed.")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /Open Agents/i })).toBeInTheDocument();
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

  it("renders Globe icon for dm-remote channels", () => {
    const ch = makeChannel({ id: "dmr-1", name: "remote-peer", type: "dm-remote", members: ["user", "hub:peer"] });
    const sections = [makeSection("Remote", [ch])];
    render(
      <ChannelSidebar {...buildProps({ sections })} />,
    );
    expect(screen.getByText("remote-peer")).toBeInTheDocument();
    const channelRow = screen.getByLabelText(`Channel ${ch.name}`);
    expect(channelRow.querySelector('[data-lucide="globe"]')).toBeTruthy();
  });
});

/* ------------------------------------------------------------------ */
/*  Mobile layout                                                      */
/* ------------------------------------------------------------------ */

describe("ChannelSidebar — mobile", () => {
  it("renders connection status with mobile inline styles when connecting", () => {
    const { container } = render(
      <ChannelSidebar {...buildProps({ isMobile: true, wsStatus: "connecting" })} />,
    );
    expect(screen.getByText("Connecting…")).toBeInTheDocument();
    // Mobile uses inline styles — verify the wrapper div padding via longhands
    // (jsdom re-serialises shorthands; asserting longhands is stable across versions)
    const mobileContainer = container.firstChild as HTMLElement;
    expect(mobileContainer.style.paddingTop).toBe("8px");
    expect(mobileContainer.style.paddingRight).toBe("0px");
    expect(mobileContainer.style.paddingBottom).toBe("16px");
  });

  it("does not render connection status text when wsStatus is connected", () => {
    render(<ChannelSidebar {...buildProps({ isMobile: true, wsStatus: "connected" })} />);
    expect(screen.queryByText("Connected")).not.toBeInTheDocument();
  });

  it("renders MobileChatList search input on mobile", () => {
    render(<ChannelSidebar {...buildProps({ isMobile: true, wsStatus: "connected" })} />);
    expect(screen.getByLabelText("Search chats")).toBeInTheDocument();
  });

  it("renders empty state on mobile", () => {
    render(
      <ChannelSidebar {...buildProps({ isMobile: true, allEmpty: true })} />,
    );
    expect(screen.getByText("No conversations yet")).toBeInTheDocument();
  });

  it("on mobile a setup note is announced: status while busy, alert on error", () => {
    const { unmount } = render(
      <ChannelSidebar {...buildProps({ isMobile: true, allEmpty: true, emptyNote: { title: "Setting up your assistant", detail: "Soon.", tone: "busy" } })} />,
    );
    expect(screen.getByRole("status")).toHaveTextContent("Setting up your assistantSoon.");
    expect(screen.queryByRole("button", { name: /Open Agents/i })).not.toBeInTheDocument();
    unmount();
    render(
      <ChannelSidebar {...buildProps({ isMobile: true, allEmpty: true, emptyNote: { title: "Your assistant isn't ready", detail: "Failed.", tone: "error" } })} />,
    );
    expect(screen.getByRole("alert")).toHaveTextContent("Failed.");
    expect(screen.getByRole("button", { name: /Open Agents/i })).toBeInTheDocument();
  });

  it("on mobile the plain empty state is not a live region", () => {
    render(<ChannelSidebar {...buildProps({ isMobile: true, allEmpty: true })} />);
    expect(screen.queryByRole("status")).not.toBeInTheDocument();
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
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
    fireEvent.click(screen.getByRole("button", { name: `Chat ${ch.name}` }));
    expect(onSelectChannel).toHaveBeenCalledWith("ch-1");
  });

  it("renders People chip on mobile when liveAgents are present and filters people DMs", () => {
    const onSelectChannel = vi.fn();
    const dmAgent = makeDmChannel({ id: "dm-juniper", name: "juniper", members: ["user", "juniper"] });
    const dmPerson = makeDmChannel({ id: "dm-mum", name: "mum", members: ["user", "mum"] });
    const sections = [makeSection("DMs", [dmAgent, dmPerson])];
    render(
      <ChannelSidebar
        {...buildProps({
          isMobile: true,
          sections,
          liveAgents: [{ name: "juniper" }],
          onSelectChannel,
          agentRosterReady: true,
        })}
      />,
    );
    // People chip should render
    const peopleChip = screen.getByRole("button", { name: "People" });
    expect(peopleChip).toBeInTheDocument();
    // Click People chip
    fireEvent.click(peopleChip);
    // Should show "mum" (person DM) and not "juniper" (agent DM)
    expect(screen.getByText("mum")).toBeInTheDocument();
    expect(screen.queryByText("juniper")).not.toBeInTheDocument();
  });

  /* RED-FIRST: test (i) - when liveAgents and archivedAgents are both empty,
     agentNames is undefined, so all DMs stay under Agents (back-compat) */
  it("mobile, liveAgents=[] and archivedAgents=[], one dm channel: Agents chip shown, no People chip", () => {
    const onSelectChannel = vi.fn();
    const dm = makeDmChannel({ id: "dm-someagent", name: "some-agent", members: ["user", "some-agent"] });
    const sections = [makeSection("DMs", [dm])];
    render(
      <ChannelSidebar
        {...buildProps({
          isMobile: true,
          sections,
          liveAgents: [],
          archivedAgents: [],
          agentRosterReady: false,
          onSelectChannel,
        })}
      />
    );
    // Agents chip should be shown (back-compat: all DMs are agents)
    const agentsChip = screen.getByRole("button", { name: "Agents" });
    expect(agentsChip).toBeInTheDocument();
    // People chip should NOT be shown (no agentNames = back-compat)
    const peopleChip = screen.queryByRole("button", { name: "People" });
    expect(peopleChip).not.toBeInTheDocument();
    // Clicking Agents chip should show the DM
    fireEvent.click(agentsChip);
    expect(screen.getByText("some-agent")).toBeInTheDocument();
  });

  /* RED-FIRST: test (ii) - with live agent + archived agent,
      archived agent's name is included in agentNames,
      so DM with archived agent is classified as agent DM, not people DM */
  it("mobile, liveAgents + archivedAgents: archived agent DM classified as agent, People chip does NOT list it", () => {
    const onSelectChannel = vi.fn();
    const dmArchived = makeDmChannel({ id: "dm-oldbot", name: "old-bot", members: ["user", "old-bot"] });
    const dmPerson = makeDmChannel({ id: "dm-mum", name: "mum", members: ["user", "mum"] });
    const sections = [makeSection("DMs", [dmArchived, dmPerson])];
    render(
      <ChannelSidebar
        {...buildProps({
          isMobile: true,
          sections,
          liveAgents: [{ name: "some-agent" }],
          archivedAgents: [{ id: "a1", archived_slug: "old-bot-x", original: { name: "old-bot" } }],
          agentRosterReady: true,
          onSelectChannel,
        })}
      />,
    );
    // Both "Agents" and "People" chips should render (there are DMs in both categories)
    const agentsChip = screen.getByRole("button", { name: "Agents" });
    expect(agentsChip).toBeInTheDocument();
    const peopleChip = screen.getByRole("button", { name: "People" });
    expect(peopleChip).toBeInTheDocument();
    // Click People chip - should NOT list the old-bot DM (it's now an agent DM)
    fireEvent.click(peopleChip);
    expect(screen.queryByText("old-bot")).not.toBeInTheDocument();
    expect(screen.getByText("mum")).toBeInTheDocument();
    // Click Agents chip - should list the old-bot DM
    fireEvent.click(agentsChip);
    expect(screen.getByText("old-bot")).toBeInTheDocument();
  });

  /* RED-FIRST: test (iii) - mobile, agentRosterReady false,
      liveAgents=[{name:"some-agent"}], dms with members ["user","old-bot"] and ["user","mum"]: no "People" chip button is shown */
  it("mobile, agentRosterReady false, liveAgents=[{name:\"some-agent\"}], dms with members [\"user\",\"old-bot\"] and [\"user\",\"mum\"]: no \"People\" chip button is shown", () => {
    const onSelectChannel = vi.fn();
    const dmAgent = makeDmChannel({ id: "dm-agent", name: "some-agent", members: ["user", "some-agent"] });
    const dmPerson = makeDmChannel({ id: "dm-person", name: "old-bot", members: ["user", "old-bot"] });
    const sections = [makeSection("DMs", [dmAgent, dmPerson])];
    render(
      <ChannelSidebar
        {...buildProps({
          isMobile: true,
          sections,
          liveAgents: [{ name: "some-agent" }],
          archivedAgents: [],
          agentRosterReady: false,
          onSelectChannel,
        })}
      />,
    );
    // People chip should NOT be shown when agentRosterReady is false
    const peopleChip = screen.queryByRole("button", { name: "People" });
    expect(peopleChip).not.toBeInTheDocument();
    // Both DMs should appear under Agents (back-compat when roster not ready)
    const agentsChip = screen.getByRole("button", { name: "Agents" });
    expect(agentsChip).toBeInTheDocument();
    fireEvent.click(agentsChip);
    expect(screen.getByText("some-agent")).toBeInTheDocument();
    expect(screen.getByText("old-bot")).toBeInTheDocument();
  });

  /* RED-FIRST: test (iv) - mobile, agentRosterReady true, liveAgents=[] and archivedAgents=[], dm with members ["user","mum"]: a "People" chip is shown and lists "mum" */
  it("mobile, agentRosterReady true, liveAgents=[] and archivedAgents=[], dm with members [\"user\",\"mum\"]: a \"People\" chip is shown and lists \"mum\"", () => {
    const onSelectChannel = vi.fn();
    const dmPerson = makeDmChannel({ id: "dm-person", name: "mum", members: ["user", "mum"] });
    const sections = [makeSection("DMs", [dmPerson])];
    render(
      <ChannelSidebar
        {...buildProps({
          isMobile: true,
          sections,
          liveAgents: [],
          archivedAgents: [],
          agentRosterReady: true,
          onSelectChannel,
        })}
      />,
    );
    // People chip should be shown when roster is ready (even if empty)
    const peopleChip = screen.getByRole("button", { name: "People" });
    expect(peopleChip).toBeInTheDocument();
    // Agents chip should NOT be shown (no agents)
    const agentsChip = screen.queryByRole("button", { name: "Agents" });
    expect(agentsChip).not.toBeInTheDocument();
    // Clicking People chip should show the person DM
    fireEvent.click(peopleChip);
    expect(screen.getByText("mum")).toBeInTheDocument();
  });

  it("mobile, archived agent known only by archived_slug: its DM is classified as agent", () => {
    const dmSlug = makeDmChannel({ id: "dm-slug", name: "old-bot-x", members: ["user", "old-bot-x"] });
    const dmPerson = makeDmChannel({ id: "dm-mum", name: "mum", members: ["user", "mum"] });
    const sections = [makeSection("DMs", [dmSlug, dmPerson])];
    render(
      <ChannelSidebar
        {...buildProps({
          isMobile: true,
          sections,
          liveAgents: [{ name: "some-agent" }],
          archivedAgents: [{ id: "a1", archived_slug: "old-bot-x" }],
          agentRosterReady: true,
          onSelectChannel: vi.fn(),
        })}
      />
    );
    fireEvent.click(screen.getByRole("button", { name: "People" }));
    expect(screen.queryByText("old-bot-x")).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Agents" }));
    expect(screen.getByText("old-bot-x")).toBeInTheDocument();
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

  it("renders project channels in unified list on mobile", () => {
    render(
      <ChannelSidebar
        {...buildProps({ isMobile: true, projectGroups: [projectGroup] })}
      />,
    );
    // Project channels appear in the unified MobileChatList
    expect(screen.getByText("proj-general")).toBeInTheDocument();
    expect(screen.getByText("proj-dev")).toBeInTheDocument();
  });

  it("shows a working dot on a working bound project channel (desktop)", () => {
    render(
      <ChannelSidebar
        {...buildProps({
          projectGroups: [projectGroup],
          agentPresence: { "pc-1": "working" },
        })}
      />,
    );
    const btn = screen.getByRole("button", { name: "Channel proj-general" });
    const dot = btn.querySelector(".taos-status-pulse");
    expect(dot).toBeTruthy();
    expect(dot).toHaveClass("bg-amber-400");
  });

  it("does not show presence dot on mobile for project channel (intentionally omitted)", () => {
    render(
      <ChannelSidebar
        {...buildProps({
          isMobile: true,
          projectGroups: [projectGroup],
          agentPresence: { "pc-1": "working" },
        })}
      />,
    );
    const btn = screen.getByRole("button", { name: "Chat proj-general" });
    expect(btn.querySelector(".taos-status-pulse")).toBeNull();
    expect(btn.querySelector('[aria-label^="Agent is"]')).toBeNull();
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
    // Bus section is collapsible on mobile — expand it first
    fireEvent.click(screen.getByText("Agent activity"));
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
/*  Agent presence dots                                                 */
/* ------------------------------------------------------------------ */

describe("ChannelSidebar — presence dots", () => {
  it("shows a pulsing amber 'working' dot on a working agent channel (desktop)", () => {
    const ch = makeChannel({ id: "ch-1", name: "session-alice" });
    const sections = [makeSection("Agents-DMs", [ch])];
    const { container } = render(
      <ChannelSidebar
        {...buildProps({ sections, agentPresence: { "ch-1": "working" } })}
      />,
    );
    const btn = screen.getByRole("button", { name: `Channel ${ch.name}` });
    const dot = btn.querySelector(".taos-status-pulse");
    expect(dot).toBeTruthy();
    expect(dot).toHaveClass("bg-amber-400");
  });

  it("shows a solid green 'live' dot on a live agent channel (desktop)", () => {
    const ch = makeChannel({ id: "ch-1", name: "session-alice" });
    const sections = [makeSection("Agents-DMs", [ch])];
    render(
      <ChannelSidebar
        {...buildProps({ sections, agentPresence: { "ch-1": "live" } })}
      />,
    );
    const btn = screen.getByRole("button", { name: `Channel ${ch.name}` });
    expect(btn.querySelector(".taos-status-pulse")).toBeNull();
    const dot = btn.querySelector('[aria-label="Agent is live"]');
    expect(dot).toBeTruthy();
    expect(dot).toHaveClass("bg-emerald-400");
  });

  it("shows a muted gray 'idle' dot on an idle agent channel (desktop)", () => {
    const ch = makeChannel({ id: "ch-1", name: "session-alice" });
    const sections = [makeSection("Agents-DMs", [ch])];
    render(
      <ChannelSidebar
        {...buildProps({ sections, agentPresence: { "ch-1": "idle" } })}
      />,
    );
    const btn = screen.getByRole("button", { name: `Channel ${ch.name}` });
    expect(btn.querySelector(".taos-status-pulse")).toBeNull();
    const dot = btn.querySelector('[aria-label="Agent is idle"]');
    expect(dot).toBeTruthy();
    expect(dot).toHaveClass("bg-shell-text-tertiary");
  });

  it("does not show a presence dot when channel has no presence entry", () => {
    const ch = makeChannel({ id: "ch-1", name: "general" });
    const sections = [makeSection("Topics", [ch])];
    render(
      <ChannelSidebar {...buildProps({ sections, agentPresence: {} })} />,
    );
    const btn = screen.getByRole("button", { name: `Channel ${ch.name}` });
    expect(btn.querySelector(".taos-status-pulse")).toBeNull();
    expect(btn.querySelector('[aria-label^="Agent is"]')).toBeNull();
  });

  it("does not show presence dot on mobile (intentionally omitted)", () => {
    const ch = makeChannel({ id: "ch-1", name: "session-bob" });
    const sections = [makeSection("Agents-DMs", [ch])];
    render(
      <ChannelSidebar
        {...buildProps({ isMobile: true, sections, agentPresence: { "ch-1": "working" } })}
      />,
    );
    const btn = screen.getByRole("button", { name: `Chat ${ch.name}` });
    expect(btn.querySelector(".taos-status-pulse")).toBeNull();
    expect(btn.querySelector('[aria-label^="Agent is"]')).toBeNull();
  });

  it("clears presence dot when channel leaves the presence map", () => {
    const ch = makeChannel({ id: "ch-1", name: "session-carol" });
    const sections = [makeSection("Agents-DMs", [ch])];
    const { rerender } = render(
      <ChannelSidebar
        {...buildProps({ sections, agentPresence: { "ch-1": "working" } })}
      />,
    );
    let btn = screen.getByRole("button", { name: `Channel ${ch.name}` });
    expect(btn.querySelector(".taos-status-pulse")).toBeTruthy();

    rerender(
      <ChannelSidebar
        {...buildProps({ sections, agentPresence: {} })}
      />,
    );
    btn = screen.getByRole("button", { name: `Channel ${ch.name}` });
    expect(btn.querySelector(".taos-status-pulse")).toBeNull();
  });

  it("does not show live dot on mobile when channel is live (intentionally omitted)", () => {
    const ch = makeChannel({ id: "ch-1", name: "session-dave" });
    const sections = [makeSection("Agents-DMs", [ch])];
    render(
      <ChannelSidebar
        {...buildProps({ isMobile: true, sections, agentPresence: { "ch-1": "live" } })}
      />,
    );
    const btn = screen.getByRole("button", { name: `Chat ${ch.name}` });
    expect(btn.querySelector(".taos-status-pulse")).toBeNull();
    expect(btn.querySelector('[aria-label="Agent is live"]')).toBeNull();
  });
});

/* ------------------------------------------------------------------ */
/*  Active-channel accent rail                                          */
/* ------------------------------------------------------------------ */

describe("ChannelSidebar — accent rail", () => {
  it("applies accent-left border to the selected channel (desktop)", () => {
    const ch = makeChannel({ id: "ch-1", name: "general" });
    const sections = [makeSection("Channels", [ch])];
    render(
      <ChannelSidebar
        {...buildProps({ sections, selectedChannel: "ch-1" })}
      />,
    );
    const btn = screen.getByRole("button", { name: `Channel ${ch.name}` });
    expect(btn.className).toContain("border-l-2");
    expect(btn.className).toContain("border-accent-line");
  });

  it("does not apply accent-left border to unselected channel (desktop)", () => {
    const ch = makeChannel({ id: "ch-1", name: "general" });
    const sections = [makeSection("Channels", [ch])];
    render(
      <ChannelSidebar
        {...buildProps({ sections, selectedChannel: null })}
      />,
    );
    const btn = screen.getByRole("button", { name: `Channel ${ch.name}` });
    expect(btn.className).not.toContain("border-l-2");
    expect(btn.className).not.toContain("border-accent-line");
  });

  it("highlights selected channel with active background on mobile", () => {
    const ch = makeChannel({ id: "ch-1", name: "general" });
    const sections = [makeSection("Channels", [ch])];
    render(
      <ChannelSidebar
        {...buildProps({ isMobile: true, sections, selectedChannel: "ch-1" })}
      />,
    );
    const btn = screen.getByRole("button", { name: `Chat ${ch.name}` });
    expect(btn.style.background).toBe("var(--color-shell-surface-active)");
  });

  it("does not highlight unselected channel on mobile", () => {
    const ch = makeChannel({ id: "ch-1", name: "general" });
    const sections = [makeSection("Channels", [ch])];
    render(
      <ChannelSidebar
        {...buildProps({ isMobile: true, sections, selectedChannel: null })}
      />,
    );
    const btn = screen.getByRole("button", { name: `Chat ${ch.name}` });
    expect(btn.style.background).not.toBe("var(--color-shell-surface-active)");
  });
});

/* ------------------------------------------------------------------ */
/*  Selected-channel accent rail                                       */
/* ------------------------------------------------------------------ */

describe("ChannelSidebar — selected accent rail", () => {
  const projectGroup = {
    id: "proj-1",
    name: "My Project",
    channels: [makeChannel({ id: "pc-1", name: "proj-general" })],
  };

  it("shows the accent rail on the selected project channel (desktop)", () => {
    render(
      <ChannelSidebar
        {...buildProps({ projectGroups: [projectGroup], selectedChannel: "pc-1" })}
      />,
    );
    const btn = screen.getByRole("button", { name: "Channel proj-general" });
    expect(btn).toHaveClass("border-l-2", "border-accent-line");
  });

  it("highlights selected project channel with active background on mobile", () => {
    render(
      <ChannelSidebar
        {...buildProps({
          isMobile: true,
          projectGroups: [projectGroup],
          selectedChannel: "pc-1",
        })}
      />,
    );
    const btn = screen.getByRole("button", { name: "Chat proj-general" });
    expect(btn.style.background).toBe("var(--color-shell-surface-active)");
  });

  it("shows the accent rail on the selected archived channel (desktop)", () => {
    render(
      <ChannelSidebar
        {...buildProps({
          archivedChannels: [makeChannel({ id: "arc-1", name: "old-chan" })],
          archivedExpanded: true,
          selectedChannel: "arc-1",
        })}
      />,
    );
    const btn = screen.getByRole("button", { name: "Archived channel old-chan" });
    expect(btn).toHaveClass("border-l-2", "border-accent-line");
  });

  it("highlights selected archived channel with active background on mobile", () => {
    render(
      <ChannelSidebar
        {...buildProps({
          isMobile: true,
          archivedChannels: [makeChannel({ id: "arc-1", name: "old-chan" })],
          archivedExpanded: true,
          selectedChannel: "arc-1",
        })}
      />,
    );
    const btn = screen.getByRole("button", { name: "Archived channel old-chan" });
    expect(btn.style.background).toBe("var(--color-shell-surface-active)");
  });
});
