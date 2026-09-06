import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import { WallhavenBrowser } from "./WallhavenBrowser";

const MOCK_SEARCH_RESPONSE = {
  data: [
    {
      id: "abc123",
      url: "https://wallhaven.cc/w/abc123",
      path: "https://w.wallhaven.cc/full/ab/wallhaven-abc123.jpg",
      thumbs: {
        small: "https://th.wallhaven.cc/small/ab/abc123.jpg",
        original: "https://th.wallhaven.cc/original/ab/abc123.jpg",
        large: "https://th.wallhaven.cc/large/ab/abc123.jpg",
      },
      resolution: "1920x1080",
      category: "general",
      purity: "sfw",
    },
    {
      id: "def456",
      url: "https://wallhaven.cc/w/def456",
      path: "https://w.wallhaven.cc/full/de/wallhaven-def456.jpg",
      thumbs: {
        small: "https://th.wallhaven.cc/small/de/def456.jpg",
        original: "https://th.wallhaven.cc/original/de/def456.jpg",
        large: "https://th.wallhaven.cc/large/de/def456.jpg",
      },
      resolution: "2560x1440",
      category: "anime",
      purity: "sfw",
    },
  ],
  meta: {
    current_page: 1,
    last_page: 3,
    total: 30,
  },
};

const MOCK_EMPTY_RESPONSE = {
  data: [],
  meta: { current_page: 1, last_page: 1, total: 0 },
};

describe("WallhavenBrowser", () => {
  let mockFetch: ReturnType<typeof vi.fn>;

  beforeEach(() => {
    mockFetch = vi.fn();
    globalThis.fetch = mockFetch;
  });

  it("renders the search input and idle prompt", () => {
    render(<WallhavenBrowser onSelect={vi.fn()} />);
    expect(screen.getByLabelText(/search wallhaven/i)).toBeInTheDocument();
    expect(screen.getByText(/type a search term/i)).toBeInTheDocument();
  });

  it("debounces search and fetches results", async () => {
    mockFetch.mockResolvedValueOnce({
      ok: true,
      json: () => Promise.resolve(MOCK_SEARCH_RESPONSE),
    });

    render(<WallhavenBrowser onSelect={vi.fn()} />);

    const input = screen.getByLabelText(/search wallhaven/i);
    fireEvent.change(input, { target: { value: "nature" } });

    // Wait for debounce (300ms) + fetch to complete
    await waitFor(
      () => {
        expect(mockFetch).toHaveBeenCalledWith(
          expect.stringContaining("/api/wallhaven/search?q=nature&page=1"),
        );
      },
      { timeout: 1000 },
    );
  });

  it("shows loading state while fetching", async () => {
    // Return a promise that resolves slowly so we can observe the loading state
    let resolvePromise!: (value: unknown) => void;
    const slowPromise = new Promise((resolve) => {
      resolvePromise = resolve;
    });
    mockFetch.mockReturnValueOnce(slowPromise);

    render(<WallhavenBrowser onSelect={vi.fn()} />);

    const input = screen.getByLabelText(/search wallhaven/i);
    fireEvent.change(input, { target: { value: "nature" } });

    // Wait for debounce to fire, then we should see loading
    await waitFor(
      () => {
        expect(screen.getByText(/searching/i)).toBeInTheDocument();
      },
      { timeout: 1000 },
    );

    // Clean up: resolve the promise
    resolvePromise({
      ok: true,
      json: () => Promise.resolve(MOCK_SEARCH_RESPONSE),
    });
  });

  it("shows 'no wallpapers found' for empty results", async () => {
    mockFetch.mockResolvedValueOnce({
      ok: true,
      json: () => Promise.resolve(MOCK_EMPTY_RESPONSE),
    });

    render(<WallhavenBrowser onSelect={vi.fn()} />);

    const input = screen.getByLabelText(/search wallhaven/i);
    fireEvent.change(input, { target: { value: "zzzzzz" } });

    await waitFor(
      () => {
        expect(screen.getByText(/no wallpapers found/i)).toBeInTheDocument();
      },
      { timeout: 1000 },
    );
  });

  it("shows error state on fetch failure", async () => {
    mockFetch.mockRejectedValueOnce(new Error("Network error"));

    render(<WallhavenBrowser onSelect={vi.fn()} />);

    const input = screen.getByLabelText(/search wallhaven/i);
    fireEvent.change(input, { target: { value: "nature" } });

    await waitFor(
      () => {
        expect(screen.getByText(/network error/i)).toBeInTheDocument();
      },
      { timeout: 1000 },
    );
  });

  it("displays search results and calls onSelect on click", async () => {
    mockFetch.mockResolvedValueOnce({
      ok: true,
      json: () => Promise.resolve(MOCK_SEARCH_RESPONSE),
    });

    const onSelect = vi.fn();
    render(<WallhavenBrowser onSelect={onSelect} />);

    const input = screen.getByLabelText(/search wallhaven/i);
    fireEvent.change(input, { target: { value: "nature" } });

    await waitFor(
      () => {
        expect(
          screen.getByLabelText(/set wallpaper: abc123/i),
        ).toBeInTheDocument();
      },
      { timeout: 1000 },
    );

    fireEvent.click(screen.getByLabelText(/set wallpaper: abc123/i));
    expect(onSelect).toHaveBeenCalledWith(
      "https://w.wallhaven.cc/full/ab/wallhaven-abc123.jpg",
      "Wallhaven: abc123 (1920x1080)",
    );
  });

  it("shows pagination controls when multiple pages exist", async () => {
    mockFetch.mockResolvedValueOnce({
      ok: true,
      json: () => Promise.resolve(MOCK_SEARCH_RESPONSE),
    });

    render(<WallhavenBrowser onSelect={vi.fn()} />);

    const input = screen.getByLabelText(/search wallhaven/i);
    fireEvent.change(input, { target: { value: "nature" } });

    await waitFor(
      () => {
        expect(screen.getByLabelText(/previous page/i)).toBeInTheDocument();
        expect(screen.getByLabelText(/next page/i)).toBeInTheDocument();
        expect(screen.getByText("1 / 3")).toBeInTheDocument();
      },
      { timeout: 1000 },
    );
  });

  it("previous page button is disabled on first page", async () => {
    mockFetch.mockResolvedValueOnce({
      ok: true,
      json: () => Promise.resolve(MOCK_SEARCH_RESPONSE),
    });

    render(<WallhavenBrowser onSelect={vi.fn()} />);

    const input = screen.getByLabelText(/search wallhaven/i);
    fireEvent.change(input, { target: { value: "nature" } });

    await waitFor(
      () => {
        const prevBtn = screen.getByLabelText(/previous page/i);
        expect(prevBtn).toBeDisabled();
      },
      { timeout: 1000 },
    );
  });
});
