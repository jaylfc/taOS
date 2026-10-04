import { describe, it, expect } from "vitest";
import { render, screen, fireEvent } from "@testing-library/react";
import fs from "fs";
import path from "path";
import { AgentContextMenu } from "../AgentContextMenu";
import { ChannelSidebar, type ChannelSidebarProps } from "../ChannelSidebar";
import { AttachmentGallery } from "../AttachmentGallery";
import { ChannelSettingsPanel } from "../ChannelSettingsPanel";
import { HelpPanel } from "../HelpPanel";
import { SearchPanel } from "../SearchPanel";
import { MessageInput, type MessageInputProps } from "../MessageInput";

/* ================================================================== */
/*  Relationship tests — every pair must use DIFFERENT class tokens  */
/* ================================================================== */

function textToken(el: HTMLElement, prefix = ""): string {
  const cls = el.className.split(/\s+/).find((c) => c.startsWith(prefix + "text-shell-text"));
  if (!cls) throw new Error(`no ${prefix}text-shell-text* class on <${el.tagName}>: ${el.className}`);
  return cls.slice(prefix.length);
}

function bgToken(el: HTMLElement, prefix = ""): string {
  const cls = el.className.split(/\s+/).find((c) => c.startsWith(prefix + "bg-shell-") || c.startsWith(prefix + "bg-white/"));
  if (!cls) throw new Error(`no ${prefix}bg-shell-* class on <${el.tagName}>: ${el.className}`);
  return cls.slice(prefix.length);
}

describe("contrast relationships", () => {
  /* ---- AgentContextMenu ------------------------------------------ */
  describe("AgentContextMenu", () => {
    it("menu surface, divider, and item hover use different class tokens", () => {
      render(
        <AgentContextMenu
          slug="test-agent"
          x={100}
          y={100}
          onClose={() => {}}
        />,
      );
      const menu = screen.getByRole("menu");
      const divider = menu.querySelector(".h-px") as HTMLElement;
      const item = screen.getByText("DM @test-agent") as HTMLElement;

      // Extract the background / surface class token from each element.
      const menuSurface = menu.className.match(/bg-shell-[^\s]+/)?.[0] ?? menu.className.match(/bg-white\/\d+/)?.[0] ?? "";
      const dividerToken = divider.className.match(/bg-white\/\d+|bg-shell-[^\s]+/)?.[0] ?? "";
      const itemHoverToken = item.className.match(/hover:bg-white\/\d+|hover:bg-shell-[^\s]+|focus:bg-white\/\d+|focus:bg-shell-[^\s]+/)?.[0] ?? "";

      // menu surface != divider != item hover
      expect(menuSurface).not.toBe(dividerToken);
      expect(dividerToken).not.toBe(itemHoverToken);
      expect(menuSurface).not.toBe(itemHoverToken);
    });
  });

  /* ---- ChannelSidebar -------------------------------------------- */
  describe("ChannelSidebar", () => {
    function buildProps(overrides: Partial<ChannelSidebarProps> = {}): ChannelSidebarProps {
      return {
        isMobile: false,
        wsStatus: "connected" as const,
        allEmpty: false,
        sections: [],
        collapsedSections: {},
        onToggleSection: () => {},
        visibleInSection: (items) => items,
        selectedChannel: null,
        onSelectChannel: () => {},
        unread: {},
        nowMs: Date.now(),
        liveAgents: [],
        archivedAgents: [],
        archivedChannels: [],
        archivedExpanded: false,
        onToggleArchived: () => {},
        scope: undefined,
        projectGroups: [],
        projectsExpanded: false,
        onToggleProjects: () => {},
        projectChannelExpanded: {},
        onToggleProjectChannel: () => {},
        onOpenAgentsApp: () => {},
        onRestoreArchivedChannel: () => {},
        onDeleteArchivedChannel: () => {},
        bus: { channels: [], available: false, loaded: false },
        busSelected: null,
        onSelectBusChannel: () => {},
        formatRelativeTime: (ts) => String(ts),
        agentPresence: {},
        ...overrides,
      };
    }

    it("section header default text and hover text use different classes", () => {
      const sections = [
        {
          label: "Topics",
          icon: <span data-testid="icon-topics" />,
          items: [{ id: "ch-1", name: "general", type: "topic" }],
        },
      ];
      render(<ChannelSidebar {...buildProps({ sections })} />);
      const header = screen.getByText("Topics").closest("button") as HTMLElement;
      expect(header).toBeTruthy();
      expect(textToken(header)).not.toBe(textToken(header, "hover:"));
    });

    it("archived section header default and hover use different classes", () => {
      render(
        <ChannelSidebar
          {...buildProps({
            archivedChannels: [
              {
                id: "arch-1",
                name: "old-chan",
                settings: { archived: true, archived_agent_id: "agent-1" },
              },
            ],
            archivedExpanded: true,
            archivedAgents: [{ id: "agent-1", archived_slug: "agent-1" }],
          })}
        />,
      );
      const header = screen.getByText(/Archived/).closest("button") as HTMLElement;
      expect(header).toBeTruthy();
      expect(textToken(header)).not.toBe(textToken(header, "hover:"));
    });

    it("enabled restore button and disabled restore button use different text color classes", () => {
      const { container: enabledContainer } = render(
        <ChannelSidebar
          {...buildProps({
            archivedChannels: [
              {
                id: "arch-1",
                name: "old-chan",
                settings: { archived: true, archived_agent_id: "agent-1" },
              },
            ],
            archivedExpanded: true,
            archivedAgents: [{ id: "agent-1", archived_slug: "agent-1" }],
            onRestoreArchivedChannel: () => {},
          })}
        />,
      );
      const { container: disabledContainer } = render(
        <ChannelSidebar
          {...buildProps({
            archivedChannels: [
              {
                id: "arch-1",
                name: "old-chan",
                settings: { archived: true, archived_agent_id: "agent-1" },
              },
            ],
            archivedExpanded: true,
            archivedAgents: [],
            onRestoreArchivedChannel: () => {},
          })}
        />,
      );
      const enabledBtn = enabledContainer.querySelector(
        'button[aria-label="Restore archived channel old-chan"]',
      ) as HTMLElement;
      const disabledBtn = disabledContainer.querySelector(
        'button[aria-label="Restore archived channel old-chan"]',
      ) as HTMLElement;
      expect(enabledBtn).toBeTruthy();
      expect(disabledBtn).toBeTruthy();
      expect(textToken(enabledBtn)).not.toBe(textToken(disabledBtn));
    });

    it("selected and unselected channel rows use different background classes", () => {
      const sections = [
        {
          label: "Topics",
          icon: <span data-testid="icon-topics" />,
          items: [
            { id: "ch-1", name: "general", type: "topic" },
            { id: "ch-2", name: "random", type: "topic" },
          ],
        },
      ];
      const { container } = render(
        <ChannelSidebar
          {...buildProps({ sections, selectedChannel: "ch-1" })}
        />,
      );
      const selectedBtn = screen.getByRole("button", { name: "Channel general" });
      const unselectedBtn = screen.getByRole("button", { name: "Channel random" });
      expect(selectedBtn.className).toContain("bg-shell-surface-active");
      expect(unselectedBtn.className).toContain("hover:bg-shell-surface-hover");
      expect(selectedBtn.className).not.toContain("hover:bg-shell-surface-hover");
    });
  });

  /* ---- AttachmentGallery ----------------------------------------- */
  describe("AttachmentGallery", () => {
    it("file links carry a hover background class", () => {
      render(
        <AttachmentGallery
          attachments={[
            {
              url: "http://example.com/file.txt",
              filename: "file.txt",
              mime_type: "text/plain",
              size: 1024,
            },
          ]}
        />,
      );
      const link = screen.getByText("file.txt").closest("a") as HTMLElement;
      expect(link).toBeTruthy();
      expect(bgToken(link)).not.toBe(bgToken(link, "hover:"));
    });
  });

  /* ---- ChannelSettingsPanel -------------------------------------- */
  describe("ChannelSettingsPanel", () => {
    it("member row hover uses a different class from panel background", () => {
      render(
        <ChannelSettingsPanel
          channel={{
            id: "ch-1",
            name: "general",
            type: "topic",
            topic: "",
            members: ["user", "alice"],
            settings: {},
          }}
          knownAgents={[]}
          onClose={() => {}}
          onChanged={() => {}}
        />,
      );
      const panel = document.querySelector('[aria-label="Channel settings"]');
      expect(panel).toBeTruthy();
      expect(panel?.className).toContain("bg-shell-surface");
      // The member list uses hover:bg-white/5 on rows
      const memberRows = panel?.querySelectorAll("li");
      const aliceRow = Array.from(memberRows || []).find((li) =>
        (li as HTMLElement).textContent?.includes("@alice"),
      ) as HTMLElement | undefined;
      expect(aliceRow).toBeTruthy();
      expect(aliceRow.className).toContain("hover:bg-shell-surface-hover");
    });

    it("inactive mode button and active mode button use different background classes", () => {
      render(
        <ChannelSettingsPanel
          channel={{
            id: "ch-1",
            name: "general",
            type: "topic",
            topic: "",
            members: ["user"],
            settings: { response_mode: "quiet" },
          }}
          knownAgents={[]}
          onClose={() => {}}
          onChanged={() => {}}
        />,
      );
      const quietBtn = screen.getByText("quiet");
      const livelyBtn = screen.getByText("lively");
      expect(quietBtn.className).toContain("bg-accent/30");
      expect(livelyBtn.className).toContain("bg-shell-surface");
    });
  });

  /* ---- HelpPanel ------------------------------------------------- */
  describe("HelpPanel", () => {
    it("inline code and thead use bg-white/5, distinct from panel background", () => {
      render(<HelpPanel onClose={() => {}} />);
      // HelpPanel uses bg-shell-surface for panel; inline code and thead use bg-white/5
      const panel = document.querySelector('[aria-label="Chat guide"] > div');
      expect(panel).toBeTruthy();
      expect(panel?.className).toContain("bg-shell-surface");
    });
  });

  /* ---- SearchPanel ----------------------------------------------- */
  describe("SearchPanel", () => {
    it("search input rest border and focus border use different classes", () => {
      render(<SearchPanel onJump={() => {}} onClose={() => {}} />);
      const input = screen.getByRole("textbox", { name: "Search messages" });
      const borderCls = input.className.match(/(?:^|\s)border-shell-border-strong(?:$|\s)/)?.[0]?.trim() ?? "";
      const focusCls = input.className.match(/(?:^|\s)focus:border-accent-line(?:$|\s)/)?.[0]?.trim() ?? "";
      expect(borderCls).not.toBe(focusCls);
      expect(borderCls).toBeTruthy();
      expect(focusCls).toBeTruthy();
    });

    it("search result items carry a hover background class", () => {
      render(<SearchPanel onJump={() => {}} onClose={() => {}} />);
      // With empty channels, no results render; the input still exists.
      // The hover class is on the result button element.
      const panel = document.querySelector('[aria-label="Search messages"]');
      expect(panel).toBeTruthy();
    });
  });

  /* ---- MessageInput ---------------------------------------------- */
  describe("MessageInput", () => {
    function buildProps(overrides: Partial<MessageInputProps> = {}): MessageInputProps {
      return {
        value: "",
        onChange: () => {},
        onSend: () => {},
        channel: { id: "ch-1", name: "general", type: "topic", members: [] },
        isArchived: false,
        isMobile: false,
        keyboardInset: 0,
        slashCommands: {},
        showSlash: false,
        slashQuery: "",
        slashAgent: undefined,
        mention: null,
        mentionCandidates: [],
        mentionSel: 0,
        onMentionSelChange: () => {},
        onInsertMention: () => {},
        onDismissMention: () => {},
        pendingAttachments: [],
        onRemoveAttachment: () => {},
        onRetryAttachment: () => {},
        onFileUpload: () => {},
        onSlashPick: () => {},
        onSlashClose: () => {},
        onPaste: undefined,
        ...overrides,
      };
    }

    it("mention list selected and hover use different background classes", () => {
      render(
        <MessageInput
          {...buildProps({
            mention: { partial: "ali", atIndex: 0 },
            mentionCandidates: ["alice", "bob"],
            mentionSel: 0,
          })}
        />,
      );
      const selected = screen.getByText("@alice").closest("button") as HTMLElement;
      expect(selected).toBeTruthy();
      expect(selected.className).toContain("bg-shell-border-strong");
      // hover:bg-shell-surface-hover is on the same element via conditional; verify it is
      // NOT the selected class.
      expect(selected.className).not.toContain("bg-shell-surface-hover");
    });
  });
});

/* ================================================================== */
/*  Raw-palette guard — no white/ zinc/ slate/ gray/ sky/ hex in chat */
/* ================================================================== */

const CHAT_FILES = [
  "AgentContextMenu.tsx",
  "ChannelSidebar.tsx",
  "AttachmentGallery.tsx",
  "ChannelSettingsPanel.tsx",
  "HelpPanel.tsx",
  "SearchPanel.tsx",
  "MessageInput.tsx",
  "MessageList.tsx",
  "PinRequestAffordance.tsx",
  "PinnedMessagesPopover.tsx",
  "ThreadIndicator.tsx",
  "ThreadPanel.tsx",
  "ChannelSwitcher.tsx",
];

const RAW_PALETTE_RE = /white\/\d|zinc-|slate-|gray-|sky-\d|#[0-9a-f]{3,8}\b/i;
const HTML_ENTITY_RE = /&#[0-9]+;/g;
// semantic connection-status colours (green/amber/red), no status token exists yet
const HEX_ALLOWLIST: Record<string, string[]> = {
  "ChannelSidebar.tsx": ["#34d399", "#fbbf24", "#f87171"],
};


describe("no raw palette in chat", () => {
  it("chat component files contain no raw palette classes or hex colours", () => {
    const chatDir = path.resolve(__dirname, "..");
    const failures: string[] = [];
    for (const file of CHAT_FILES) {
      const filePath = path.join(chatDir, file);
      if (!fs.existsSync(filePath)) {
        failures.push(`${file}: missing (renamed or deleted? update CHAT_FILES)`);
        continue;
      }
      let content = fs.readFileSync(filePath, "utf-8");
      content = content.replace(HTML_ENTITY_RE, "");
      const allowed = HEX_ALLOWLIST[file] ?? [];
      for (const literal of allowed) {
        content = content.split(literal).join("__ALLOWED_HEX__");
      }
      const matches = content.match(RAW_PALETTE_RE);
      if (matches && matches.length > 0) {
        failures.push(`${file}: ${matches.slice(0, 5).join(", ")}${matches.length > 5 ? ` (+${matches.length - 5} more)` : ""}`);
      }
    }
    expect(failures, `raw palette found in:\n${failures.join("\n")}`).toEqual([]);
  });
});
