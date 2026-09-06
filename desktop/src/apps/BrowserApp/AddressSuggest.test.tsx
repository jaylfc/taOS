import { describe, expect, it, vi } from "vitest";
import { render, screen, fireEvent } from "@testing-library/react";
import { AddressSuggest } from "./AddressSuggest";
import type { Suggestion } from "@/lib/browser-suggest-api";

const SAMPLE: Suggestion[] = [
  { url: "https://a.test/", title: "A", source: "history", score: 1 },
  { url: "https://b.test/", title: "B", source: "bookmark", score: 2 },
];

describe("AddressSuggest", () => {
  it("renders nothing when suggestions empty", () => {
    const { container } = render(
      <AddressSuggest
        suggestions={[]}
        selectedIndex={-1}
        onSelect={() => {}}
        onHighlight={() => {}}
      />,
    );
    expect(container.querySelector('[role="listbox"]')).toBeNull();
  });

  it("renders one option per suggestion", () => {
    render(
      <AddressSuggest
        suggestions={SAMPLE}
        selectedIndex={-1}
        onSelect={() => {}}
        onHighlight={() => {}}
      />,
    );
    const opts = screen.getAllByRole("option");
    expect(opts.length).toBe(2);
  });

  it("marks the selectedIndex with aria-selected", () => {
    render(
      <AddressSuggest
        suggestions={SAMPLE}
        selectedIndex={1}
        onSelect={() => {}}
        onHighlight={() => {}}
      />,
    );
    const opts = screen.getAllByRole("option");
    expect(opts[0].getAttribute("aria-selected")).toBe("false");
    expect(opts[1].getAttribute("aria-selected")).toBe("true");
  });

  it("clicking an option fires onSelect with the suggestion", () => {
    const onSelect = vi.fn();
    render(
      <AddressSuggest
        suggestions={SAMPLE}
        selectedIndex={-1}
        onSelect={onSelect}
        onHighlight={() => {}}
      />,
    );
    fireEvent.click(screen.getAllByRole("option")[0]);
    expect(onSelect).toHaveBeenCalledWith(SAMPLE[0]);
  });

  it("hovering an option fires onHighlight with its index", () => {
    const onHighlight = vi.fn();
    render(
      <AddressSuggest
        suggestions={SAMPLE}
        selectedIndex={-1}
        onSelect={() => {}}
        onHighlight={onHighlight}
      />,
    );
    fireEvent.mouseEnter(screen.getAllByRole("option")[1]);
    expect(onHighlight).toHaveBeenCalledWith(1);
  });
});
