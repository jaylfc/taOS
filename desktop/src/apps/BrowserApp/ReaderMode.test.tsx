import { describe, expect, it, beforeEach, vi } from "vitest";
import { render, screen, fireEvent } from "@testing-library/react";
import { ReaderMode } from "./ReaderMode";
import { useBrowserStore } from "@/stores/browser-store";
import type { Tab } from "./types";

const TEST_WINDOW_ID = "win-test";

function makeTab(overrides: Partial<Tab> = {}): Tab {
  return {
    id: "tab-1",
    url: "https://example.com/article",
    title: "Test Tab",
    pinned: false,
    history: ["https://example.com/article"],
    historyIndex: 0,
    scrollY: 0,
    zoom: 1,
    state: "live",
    lastActiveAt: Date.now(),
    readerAvailable: true,
    readerActive: true,
    readerExtract: {
      title: "Test Article Title",
      text: "Some article text content here",
      html: "<p>Some article text content here</p>",
      word_count: 300,
    },
    ...overrides,
  };
}

beforeEach(() => {
  useBrowserStore.setState({ windows: {} });
  useBrowserStore.getState().createWindow(TEST_WINDOW_ID, "personal");
});

describe("ReaderMode — rendering", () => {
  it("renders the article title", () => {
    const tab = makeTab();
    render(<ReaderMode tab={tab} windowId={TEST_WINDOW_ID} />);
    expect(screen.getByRole("heading", { level: 1 })).toHaveTextContent(
      "Test Article Title",
    );
  });

  it("renders the source domain from tab URL", () => {
    const tab = makeTab();
    render(<ReaderMode tab={tab} windowId={TEST_WINDOW_ID} />);
    expect(screen.getByText("example.com")).toBeInTheDocument();
  });

  it("renders sanitised HTML body", () => {
    const tab = makeTab({
      readerExtract: {
        title: "Article",
        text: "Real content",
        html: "<p>Real content</p>",
        word_count: 300,
      },
    });
    render(<ReaderMode tab={tab} windowId={TEST_WINDOW_ID} />);
    const body = screen.getByTestId("reader-body");
    expect(body.querySelector("p")).toBeInTheDocument();
    expect(body.textContent).toContain("Real content");
  });

  it("strips <script> tags from extract HTML before render (XSS safety)", () => {
    const tab = makeTab({
      readerExtract: {
        title: "Article",
        text: "real content",
        html: "<script>alert(1)</script><p>real content</p>",
        word_count: 300,
      },
    });
    render(<ReaderMode tab={tab} windowId={TEST_WINDOW_ID} />);
    const body = screen.getByTestId("reader-body");
    expect(body.querySelector("script")).toBeNull();
    expect(body.textContent).toContain("real content");
  });

  it("returns null when readerExtract is absent", () => {
    const tab = makeTab({ readerExtract: null });
    const { container } = render(<ReaderMode tab={tab} windowId={TEST_WINDOW_ID} />);
    expect(container.firstChild).toBeNull();
  });
});

describe("ReaderMode — font controls", () => {
  it("font size buttons change the aria-pressed state", () => {
    const tab = makeTab();
    render(<ReaderMode tab={tab} windowId={TEST_WINDOW_ID} />);

    const smallBtn = screen.getByRole("button", { name: /font size small/i });
    const mediumBtn = screen.getByRole("button", { name: /font size medium/i });

    // Default is medium
    expect(mediumBtn).toHaveAttribute("aria-pressed", "true");
    expect(smallBtn).toHaveAttribute("aria-pressed", "false");

    fireEvent.click(smallBtn);

    expect(smallBtn).toHaveAttribute("aria-pressed", "true");
    expect(mediumBtn).toHaveAttribute("aria-pressed", "false");
  });

  it("font family buttons toggle aria-pressed", () => {
    const tab = makeTab();
    render(<ReaderMode tab={tab} windowId={TEST_WINDOW_ID} />);

    const serifBtn = screen.getByRole("button", { name: /font family serif/i });
    const sansBtn = screen.getByRole("button", { name: /font family sans-serif/i });

    // Default is serif
    expect(serifBtn).toHaveAttribute("aria-pressed", "true");

    fireEvent.click(sansBtn);
    expect(sansBtn).toHaveAttribute("aria-pressed", "true");
    expect(serifBtn).toHaveAttribute("aria-pressed", "false");
  });
});

describe("ReaderMode — exit button", () => {
  it("Exit Reader button calls setTabReader with readerActive: false", () => {
    const setTabReaderSpy = vi.spyOn(useBrowserStore.getState(), "setTabReader");
    const tab = makeTab();
    render(<ReaderMode tab={tab} windowId={TEST_WINDOW_ID} />);

    fireEvent.click(screen.getByRole("button", { name: /exit reader mode/i }));

    expect(setTabReaderSpy).toHaveBeenCalledWith(
      TEST_WINDOW_ID,
      "tab-1",
      { readerActive: false },
    );
  });
});
