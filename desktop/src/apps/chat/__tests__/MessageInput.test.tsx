import { describe, it, expect, vi } from "vitest";
import { render, screen, fireEvent } from "@testing-library/react";
import { MessageInput } from "../MessageInput";
import type { Channel } from "../types";
import type { SlashCommandsBySlug } from "../SlashMenu";
import type { PendingAttachment } from "../AttachmentsBar";

const baseChannel: Channel = {
  id: "ch-1",
  name: "general",
  type: "topic",
  members: ["user", "tom"],
};

const emptySlashCommands: SlashCommandsBySlug = {};

const noMentionProps = {
  mention: null,
  mentionCandidates: [],
  mentionSel: 0,
  onMentionSelChange: vi.fn(),
  onInsertMention: vi.fn(),
  onDismissMention: vi.fn(),
};

const noAttachmentsProps = {
  pendingAttachments: [] as PendingAttachment[],
  onRemoveAttachment: vi.fn(),
  onRetryAttachment: vi.fn(),
};

function defaultProps(overrides: Partial<Parameters<typeof MessageInput>[0]> = {}) {
  return {
    value: "",
    onChange: vi.fn(),
    onSend: vi.fn(),
    channel: baseChannel,
    isArchived: false,
    isMobile: false,
    keyboardInset: 0,
    slashCommands: emptySlashCommands,
    showSlash: false,
    slashQuery: "",
    slashAgent: undefined,
    ...noMentionProps,
    ...noAttachmentsProps,
    onFileUpload: vi.fn(),
    onSlashPick: vi.fn(),
    onSlashClose: vi.fn(),
    ...overrides,
  };
}

describe("MessageInput", () => {
  /* ---- basic rendering ---- */

  it("renders textarea, send button, and file upload button", () => {
    render(<MessageInput {...defaultProps()} />);
    expect(screen.getByRole("textbox", { name: /message input/i })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /send message/i })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /upload file/i })).toBeInTheDocument();
  });

  it("shows channel name in placeholder", () => {
    render(<MessageInput {...defaultProps({ channel: { ...baseChannel, name: "general" } })} />);
    expect(screen.getByPlaceholderText("Message #general...")).toBeInTheDocument();
  });

  it("renders without error when channel is undefined", () => {
    // channel is typed as Channel | undefined — the component uses
    // channel?.name ?? "" so it should degrade gracefully.
    expect(() =>
      render(<MessageInput {...defaultProps({ channel: undefined })} />),
    ).not.toThrow();
    expect(screen.getByPlaceholderText("Message #...")).toBeInTheDocument();
  });

  it("shows 'This chat is archived' placeholder when channel is archived", () => {
    render(<MessageInput {...defaultProps({ isArchived: true })} />);
    expect(
      screen.getByPlaceholderText("This chat is archived"),
    ).toBeInTheDocument();
  });

  /* ---- text input ---- */

  it("calls onChange when user types in the textarea", () => {
    const onChange = vi.fn();
    render(<MessageInput {...defaultProps({ onChange })} />);
    fireEvent.change(screen.getByRole("textbox", { name: /message input/i }), {
      target: { value: "hello" },
    });
    expect(onChange).toHaveBeenCalledWith("hello");
  });

  /* ---- send on Enter ---- */

  it("calls onSend when Enter is pressed without Shift", () => {
    const onSend = vi.fn();
    render(<MessageInput {...defaultProps({ onSend })} />);
    fireEvent.keyDown(screen.getByRole("textbox", { name: /message input/i }), {
      key: "Enter",
      shiftKey: false,
    });
    expect(onSend).toHaveBeenCalled();
  });

  it("does NOT call onSend when Shift+Enter is pressed", () => {
    const onSend = vi.fn();
    render(<MessageInput {...defaultProps({ onSend })} />);
    fireEvent.keyDown(screen.getByRole("textbox", { name: /message input/i }), {
      key: "Enter",
      shiftKey: true,
    });
    expect(onSend).not.toHaveBeenCalled();
  });

  it("does NOT call onSend when a non-Enter key is pressed", () => {
    const onSend = vi.fn();
    render(<MessageInput {...defaultProps({ onSend })} />);
    fireEvent.keyDown(screen.getByRole("textbox", { name: /message input/i }), {
      key: "a",
    });
    expect(onSend).not.toHaveBeenCalled();
  });

  it("calls onSend when send button is clicked", () => {
    const onSend = vi.fn();
    render(<MessageInput {...defaultProps({ value: "hello", onSend })} />);
    fireEvent.click(screen.getByRole("button", { name: /send message/i }));
    expect(onSend).toHaveBeenCalled();
  });

  /* ---- send button disabled states ---- */

  it("disables send button when value is empty and no attachments", () => {
    render(<MessageInput {...defaultProps({ value: "" })} />);
    expect(screen.getByRole("button", { name: /send message/i })).toBeDisabled();
  });

  it("disables send button when channel is archived", () => {
    render(<MessageInput {...defaultProps({ value: "hello", isArchived: true })} />);
    expect(screen.getByRole("button", { name: /send message/i })).toBeDisabled();
  });

  it("disables send button when an attachment is still uploading", () => {
    render(
      <MessageInput
        {...defaultProps({
          value: "hello",
          pendingAttachments: [
            { id: "a1", filename: "img.png", size: 100, uploading: true },
          ],
        })}
      />,
    );
    expect(screen.getByRole("button", { name: /send message/i })).toBeDisabled();
  });

  it("enables send button when value has non-whitespace text", () => {
    render(<MessageInput {...defaultProps({ value: "hello" })} />);
    expect(screen.getByRole("button", { name: /send message/i })).not.toBeDisabled();
  });

  it("enables send button when there are completed (non-uploading) attachments", () => {
    render(
      <MessageInput
        {...defaultProps({
          value: "",
          pendingAttachments: [
            { id: "a1", filename: "img.png", size: 100, uploading: false },
          ],
        })}
      />,
    );
    expect(screen.getByRole("button", { name: /send message/i })).not.toBeDisabled();
  });

  it("enables send button when value is whitespace-only but completed attachments exist", () => {
    render(
      <MessageInput
        {...defaultProps({
          value: "   ",
          pendingAttachments: [
            { id: "a1", filename: "img.png", size: 100, uploading: false },
          ],
        })}
      />,
    );
    expect(screen.getByRole("button", { name: /send message/i })).not.toBeDisabled();
  });

  /* ---- archived: disabled inputs ---- */

  it("disables textarea when channel is archived", () => {
    render(<MessageInput {...defaultProps({ isArchived: true })} />);
    expect(
      screen.getByRole("textbox", { name: /message input/i }),
    ).toBeDisabled();
  });

  it("disables file upload button when channel is archived", () => {
    render(<MessageInput {...defaultProps({ isArchived: true })} />);
    expect(screen.getByRole("button", { name: /upload file/i })).toBeDisabled();
  });

  it("does not call onChange on input when channel is archived", () => {
    const onChange = vi.fn();
    render(<MessageInput {...defaultProps({ isArchived: true, onChange })} />);
    fireEvent.change(screen.getByRole("textbox", { name: /message input/i }), {
      target: { value: "hello" },
    });
    expect(onChange).not.toHaveBeenCalled();
  });

  it("does not call onSend on Enter when channel is archived", () => {
    const onSend = vi.fn();
    render(<MessageInput {...defaultProps({ isArchived: true, onSend })} />);
    fireEvent.keyDown(screen.getByRole("textbox", { name: /message input/i }), {
      key: "Enter",
    });
    expect(onSend).not.toHaveBeenCalled();
  });

  /* ---- file upload ---- */

  it("calls onFileUpload when paperclip button is clicked", () => {
    const onFileUpload = vi.fn();
    render(<MessageInput {...defaultProps({ onFileUpload })} />);
    fireEvent.click(screen.getByRole("button", { name: /upload file/i }));
    expect(onFileUpload).toHaveBeenCalled();
  });

  /* ---- slash menu ---- */

  it("shows SlashMenu when showSlash is true", () => {
    const commands: SlashCommandsBySlug = {
      tom: [{ name: "help", description: "Show help" }],
    };
    render(
      <MessageInput
        {...defaultProps({
          slashCommands: commands,
          showSlash: true,
          slashQuery: "",
        })}
      />,
    );
    expect(screen.getByRole("listbox", { name: /slash commands/i })).toBeInTheDocument();
    expect(screen.getByText("/help")).toBeInTheDocument();
  });

  it("does not show SlashMenu when showSlash is false", () => {
    render(<MessageInput {...defaultProps({ showSlash: false })} />);
    expect(
      screen.queryByRole("listbox", { name: /slash commands/i }),
    ).not.toBeInTheDocument();
  });

  it("renders SlashMenu with scopedAgent when slashAgent is provided", () => {
    const commands: SlashCommandsBySlug = {
      tom: [{ name: "help", description: "Show help" }],
      jerry: [{ name: "status", description: "Show status" }],
    };
    // When scopedAgent="tom", only tom's commands should render.
    render(
      <MessageInput
        {...defaultProps({
          channel: { ...baseChannel, members: ["user", "tom", "jerry"] },
          slashCommands: commands,
          showSlash: true,
          slashQuery: "",
          slashAgent: "tom",
        })}
      />,
    );
    expect(screen.getByRole("listbox", { name: /slash commands/i })).toBeInTheDocument();
    expect(screen.getByRole("option", { name: /\/help/i })).toBeInTheDocument();
    expect(
      screen.queryByRole("option", { name: /\/status/i }),
    ).not.toBeInTheDocument();
  });

  it("renders SlashMenu without error when channel is undefined", () => {
    // Covers the channel?.members || [] fallback on line 113.
    // When channel is undefined, members is [], so SlashMenu returns null
    // (no matching agents) — but the input area must still render cleanly.
    const commands: SlashCommandsBySlug = {
      tom: [{ name: "help", description: "Show help" }],
    };
    const { container } = render(
      <MessageInput
        {...defaultProps({
          channel: undefined,
          slashCommands: commands,
          showSlash: true,
          slashQuery: "",
        })}
      />,
    );
    // Input area still renders; SlashMenu gracefully returns null.
    expect(container.querySelector(".border-t")).toBeTruthy();
    expect(
      screen.queryByRole("listbox", { name: /slash commands/i }),
    ).not.toBeInTheDocument();
  });

  /* ---- mention listbox ---- */

  it("shows mention listbox when mention is active with candidates", () => {
    render(
      <MessageInput
        {...defaultProps({
          mention: { partial: "@to", atIndex: 0 },
          mentionCandidates: ["tom", "don"],
          mentionSel: 0,
        })}
      />,
    );
    expect(screen.getByRole("listbox", { name: /mention a member/i })).toBeInTheDocument();
    expect(screen.getByText("@tom")).toBeInTheDocument();
    expect(screen.getByText("@don")).toBeInTheDocument();
  });

  it("does not show mention listbox when mention is null", () => {
    render(<MessageInput {...defaultProps({ mention: null })} />);
    expect(
      screen.queryByRole("listbox", { name: /mention a member/i }),
    ).not.toBeInTheDocument();
  });

  it("does not show mention listbox when candidates are empty", () => {
    render(
      <MessageInput
        {...defaultProps({
          mention: { partial: "@x", atIndex: 0 },
          mentionCandidates: [],
        })}
      />,
    );
    expect(
      screen.queryByRole("listbox", { name: /mention a member/i }),
    ).not.toBeInTheDocument();
  });

  it("does not show mention listbox when slash menu is open", () => {
    const commands: SlashCommandsBySlug = {
      tom: [{ name: "help", description: "Show help" }],
    };
    render(
      <MessageInput
        {...defaultProps({
          slashCommands: commands,
          showSlash: true,
          mention: { partial: "@to", atIndex: 0 },
          mentionCandidates: ["tom"],
        })}
      />,
    );
    // Slash menu is visible, mention is not
    expect(screen.getByRole("listbox", { name: /slash commands/i })).toBeInTheDocument();
    expect(
      screen.queryByRole("listbox", { name: /mention a member/i }),
    ).not.toBeInTheDocument();
  });

  it("calls onMentionSelChange on mouse enter of a mention candidate", () => {
    const onMentionSelChange = vi.fn();
    render(
      <MessageInput
        {...defaultProps({
          mention: { partial: "@t", atIndex: 0 },
          mentionCandidates: ["tom", "don"],
          mentionSel: 0,
          onMentionSelChange,
        })}
      />,
    );
    fireEvent.mouseEnter(screen.getByText("@don"));
    expect(onMentionSelChange).toHaveBeenCalledWith(1);
  });

  it("calls onInsertMention on mousedown of a mention candidate", () => {
    const onInsertMention = vi.fn();
    render(
      <MessageInput
        {...defaultProps({
          mention: { partial: "@t", atIndex: 0 },
          mentionCandidates: ["tom"],
          mentionSel: 0,
          onInsertMention,
        })}
      />,
    );
    fireEvent.mouseDown(screen.getByText("@tom"));
    expect(onInsertMention).toHaveBeenCalledWith("tom");
  });

  it("marks the selected mention candidate with aria-selected", () => {
    render(
      <MessageInput
        {...defaultProps({
          mention: { partial: "@t", atIndex: 0 },
          mentionCandidates: ["tom", "don"],
          mentionSel: 1,
        })}
      />,
    );
    const tom = screen.getByRole("option", { name: "@tom" });
    const don = screen.getByRole("option", { name: "@don" });
    expect(tom).toHaveAttribute("aria-selected", "false");
    expect(don).toHaveAttribute("aria-selected", "true");
  });

  /* ---- attachments bar ---- */

  it("shows AttachmentsBar when there are pending attachments", () => {
    render(
      <MessageInput
        {...defaultProps({
          pendingAttachments: [
            { id: "a1", filename: "doc.pdf", size: 2048 },
          ],
          onRemoveAttachment: vi.fn(),
          onRetryAttachment: vi.fn(),
        })}
      />,
    );
    expect(screen.getByText("doc.pdf")).toBeInTheDocument();
  });

  it("does not show AttachmentsBar when pendingAttachments is empty", () => {
    render(<MessageInput {...defaultProps()} />);
    expect(screen.queryByText("doc.pdf")).not.toBeInTheDocument();
  });

  /* ---- mobile layout ---- */

  it("renders without error when isMobile is true (style is jsdom-limited)", () => {
    // jsdom with React 19 does not render inline style attributes, so we
    // verify the component mounts cleanly in mobile mode rather than
    // inspecting pixel-level CSS.
    const { container } = render(
      <MessageInput {...defaultProps({ isMobile: true, keyboardInset: 300 })} />,
    );
    expect(container.querySelector(".border-t")).toBeTruthy();
  });

  it("renders without error when isMobile is false", () => {
    const { container } = render(
      <MessageInput {...defaultProps({ isMobile: false, keyboardInset: 300 })} />,
    );
    expect(container.querySelector(".border-t")).toBeTruthy();
  });

  /* ---- onBlur / onPaste ---- */

  it("calls onDismissMention on textarea blur", () => {
    const onDismissMention = vi.fn();
    render(<MessageInput {...defaultProps({ onDismissMention })} />);
    fireEvent.blur(screen.getByRole("textbox", { name: /message input/i }));
    expect(onDismissMention).toHaveBeenCalled();
  });

  it("calls onPaste when provided and user pastes into textarea", () => {
    const onPaste = vi.fn();
    render(<MessageInput {...defaultProps({ onPaste })} />);
    fireEvent.paste(screen.getByRole("textbox", { name: /message input/i }));
    expect(onPaste).toHaveBeenCalled();
  });

  it("does not crash when onPaste is undefined", () => {
    render(<MessageInput {...defaultProps({ onPaste: undefined })} />);
    const textarea = screen.getByRole("textbox", { name: /message input/i });
    expect(() =>
      fireEvent.paste(textarea),
    ).not.toThrow();
  });

  /* ---- whitespace-only value ---- */

  it("disables send button when value is only whitespace", () => {
    render(<MessageInput {...defaultProps({ value: "   " })} />);
    expect(screen.getByRole("button", { name: /send message/i })).toBeDisabled();
  });
});
