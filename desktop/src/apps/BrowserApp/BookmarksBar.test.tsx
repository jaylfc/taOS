import { describe, it, expect, beforeEach, vi, afterEach } from "vitest";
import { render, screen, fireEvent, waitFor, act } from "@testing-library/react";
import { BookmarksBar } from "./BookmarksBar";
import * as bookmarksApi from "@/lib/browser-bookmarks-api";
import { useBrowserStore } from "@/stores/browser-store";

vi.mock("@/lib/browser-bookmarks-api");

const WINDOW_ID = "win-bbar";
const PROFILE_ID = "personal";

function makeBookmark(overrides: Partial<bookmarksApi.Bookmark> = {}): bookmarksApi.Bookmark {
  return {
    bookmark_id: "bm-1",
    url: "https://example.com",
    title: "Example Site",
    created_at: Date.now(),
    ...overrides,
  };
}

beforeEach(() => {
  vi.mocked(bookmarksApi.listBookmarks).mockResolvedValue([]);
  vi.mocked(bookmarksApi.removeBookmark).mockResolvedValue(true);
  useBrowserStore.setState({ windows: {} });
  useBrowserStore.getState().createWindow(WINDOW_ID, PROFILE_ID);
});

afterEach(() => {
  vi.clearAllMocks();
});

describe("BookmarksBar", () => {
  it("renders null (nothing) when bookmark list is empty", async () => {
    vi.mocked(bookmarksApi.listBookmarks).mockResolvedValue([]);
    const { container } = render(
      <BookmarksBar windowId={WINDOW_ID} profileId={PROFILE_ID} />,
    );
    // Wait for the async load to settle
    await act(async () => {});
    expect(container.firstChild).toBeNull();
  });

  it("renders one chip per bookmark", async () => {
    vi.mocked(bookmarksApi.listBookmarks).mockResolvedValue([
      makeBookmark({ bookmark_id: "bm-1", title: "Alpha", url: "https://alpha.com" }),
      makeBookmark({ bookmark_id: "bm-2", title: "Beta", url: "https://beta.com" }),
    ]);

    render(<BookmarksBar windowId={WINDOW_ID} profileId={PROFILE_ID} />);
    await waitFor(() => screen.getByText("Alpha"));
    expect(screen.getByText("Beta")).toBeTruthy();
  });

  it("click on chip calls navigateTab with bookmark URL", async () => {
    vi.mocked(bookmarksApi.listBookmarks).mockResolvedValue([
      makeBookmark({ bookmark_id: "bm-1", title: "Docs", url: "https://docs.example.com" }),
    ]);
    const navigateSpy = vi.spyOn(useBrowserStore.getState(), "navigateTab");

    render(<BookmarksBar windowId={WINDOW_ID} profileId={PROFILE_ID} />);
    await waitFor(() => screen.getByText("Docs"));

    fireEvent.click(screen.getByRole("button", { name: /Go to Docs/i }));
    expect(navigateSpy).toHaveBeenCalledWith(
      WINDOW_ID,
      expect.any(String),
      "https://docs.example.com",
    );
  });

  it("right-click opens context menu, Remove calls removeBookmark with bookmark id", async () => {
    vi.mocked(bookmarksApi.listBookmarks).mockResolvedValue([
      makeBookmark({ bookmark_id: "bm-42", title: "My Page", url: "https://my.example.com" }),
    ]);

    render(<BookmarksBar windowId={WINDOW_ID} profileId={PROFILE_ID} />);
    await waitFor(() => screen.getByText("My Page"));

    const chip = screen.getByRole("button", { name: /Go to My Page/i });
    fireEvent.contextMenu(chip);

    const removeBtn = await waitFor(() => screen.getByRole("menuitem", { name: /Remove bookmark/i }));
    await act(async () => {
      fireEvent.click(removeBtn);
    });

    expect(bookmarksApi.removeBookmark).toHaveBeenCalledWith(PROFILE_ID, "bm-42");
  });

  it("after successful remove the chip disappears", async () => {
    vi.mocked(bookmarksApi.listBookmarks).mockResolvedValue([
      makeBookmark({ bookmark_id: "bm-1", title: "ToRemove", url: "https://remove.me" }),
    ]);
    vi.mocked(bookmarksApi.removeBookmark).mockResolvedValue(true);

    render(<BookmarksBar windowId={WINDOW_ID} profileId={PROFILE_ID} />);
    await waitFor(() => screen.getByText("ToRemove"));

    const chip = screen.getByRole("button", { name: /Go to ToRemove/i });
    fireEvent.contextMenu(chip);
    const removeBtn = await waitFor(() => screen.getByRole("menuitem", { name: /Remove bookmark/i }));
    await act(async () => {
      fireEvent.click(removeBtn);
    });

    await waitFor(() => expect(screen.queryByText("ToRemove")).toBeNull());
  });

  it("truncates long titles to ~20 chars", async () => {
    const longTitle = "A Very Long Title That Should Be Truncated";
    vi.mocked(bookmarksApi.listBookmarks).mockResolvedValue([
      makeBookmark({ bookmark_id: "bm-1", title: longTitle, url: "https://long.example.com" }),
    ]);

    render(<BookmarksBar windowId={WINDOW_ID} profileId={PROFILE_ID} />);
    await waitFor(() => screen.getByText("A Very Long Title Th…"));
  });
});
