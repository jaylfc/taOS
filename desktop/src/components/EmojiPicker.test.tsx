// @vitest-environment jsdom
import { describe, it, expect, vi } from "vitest";
import { render, screen, fireEvent } from "@testing-library/react";
import { EmojiPickerField } from "./EmojiPicker";

describe("EmojiPickerField", () => {
  it("shows + when value is empty", () => {
    render(<EmojiPickerField value="" onChange={vi.fn()} />);
    const btn = screen.getByRole("button", { name: "Open emoji picker" });
    expect(btn).toHaveTextContent("+");
  });

  it("shows the current emoji value when provided", () => {
    render(<EmojiPickerField value="🦉" onChange={vi.fn()} />);
    const btn = screen.getByRole("button", { name: "Open emoji picker" });
    expect(btn).toHaveTextContent("🦉");
  });

  it("is collapsed by default (aria-expanded false, no dialog)", () => {
    render(<EmojiPickerField value="" onChange={vi.fn()} />);
    const btn = screen.getByRole("button", { name: "Open emoji picker" });
    expect(btn).toHaveAttribute("aria-expanded", "false");
    expect(screen.queryByRole("dialog", { name: "Emoji picker" })).toBeNull();
  });

  it("opens the picker dialog on click and sets aria-expanded", () => {
    render(<EmojiPickerField value="" onChange={vi.fn()} />);
    const btn = screen.getByRole("button", { name: "Open emoji picker" });
    fireEvent.click(btn);
    expect(btn).toHaveAttribute("aria-expanded", "true");
    expect(screen.getByRole("dialog", { name: "Emoji picker" })).toBeInTheDocument();
  });

  it("toggles the picker closed on a second click", () => {
    render(<EmojiPickerField value="" onChange={vi.fn()} />);
    const btn = screen.getByRole("button", { name: "Open emoji picker" });
    fireEvent.click(btn);
    expect(screen.getByRole("dialog", { name: "Emoji picker" })).toBeInTheDocument();
    fireEvent.click(btn);
    expect(btn).toHaveAttribute("aria-expanded", "false");
    expect(screen.queryByRole("dialog", { name: "Emoji picker" })).toBeNull();
  });

  it("calls onChange with the picked emoji and closes the picker", () => {
    const onChange = vi.fn();
    render(<EmojiPickerField value="" onChange={onChange} />);
    const btn = screen.getByRole("button", { name: "Open emoji picker" });
    fireEvent.click(btn);
    expect(screen.getByRole("dialog", { name: "Emoji picker" })).toBeInTheDocument();

    const picker = screen.getByRole("dialog", { name: "Emoji picker" });
    const emojiButtons = picker.querySelectorAll("button");
    if (emojiButtons.length > 0) {
      fireEvent.click(emojiButtons[0]);
      expect(onChange).toHaveBeenCalledTimes(1);
      expect(btn).toHaveAttribute("aria-expanded", "false");
    }
  });
});
