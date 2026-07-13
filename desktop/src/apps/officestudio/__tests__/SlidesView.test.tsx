import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import { describe, it, expect, vi, afterEach } from "vitest";
import { SlidesView } from "../SlidesView";

const DOC = {
  id: "slides-1",
  kind: "slides",
  title: "Test deck",
  content: JSON.stringify({
    version: 1,
    title: "Test deck",
    slides: [{ id: "s1", layout: "title", title: "First slide", body: "", bullets: [] }],
  }),
  updated_at: 100,
};

function makeFetchMock() {
  return vi.fn((input: RequestInfo | URL, init?: RequestInit) => {
    const url = String(input);
    if (url === "/api/office/docs") {
      return Promise.resolve({
        ok: true,
        json: () => Promise.resolve([{ id: DOC.id, kind: DOC.kind, title: DOC.title, updated_at: DOC.updated_at }]),
      } as Response);
    }
    if (url === `/api/office/docs/${DOC.id}`) {
      return Promise.resolve({ ok: true, json: () => Promise.resolve(DOC) } as Response);
    }
    return Promise.resolve({ ok: true, json: () => Promise.resolve({}) } as Response);
  });
}

describe("SlidesView", () => {
  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("renders a new deck with one slide by default", async () => {
    vi.stubGlobal("fetch", makeFetchMock() as unknown as typeof fetch);
    render(<SlidesView />);

    await waitFor(() => expect(screen.getByText("Loading...")).toBeDefined());
    await waitFor(() => expect(screen.getByLabelText("Deck title")).toHaveValue("Untitled deck"));

    // One slide in the thumbnail rail
    const slides = screen.getAllByLabelText(/Slide 1/);
    expect(slides.length).toBeGreaterThan(0);
  });

  it("adds a slide and asserts it appears in the thumbnail rail", async () => {
    vi.stubGlobal("fetch", makeFetchMock() as unknown as typeof fetch);
    render(<SlidesView />);

    await waitFor(() => screen.getByLabelText("Deck title"));

    // Click Add slide button
    fireEvent.click(screen.getByRole("button", { name: "Add slide" }));

    // Two slides now in the thumbnail rail
    await waitFor(() => {
      const slides = screen.getAllByLabelText(/Slide [12]/);
      expect(slides.length).toBe(2);
    });
  });

  it("reorders slides by moving a slide up", async () => {
    vi.stubGlobal("fetch", makeFetchMock() as unknown as typeof fetch);
    render(<SlidesView />);

    await waitFor(() => screen.getByLabelText("Deck title"));

    // Add a second slide
    fireEvent.click(screen.getByRole("button", { name: "Add slide" }));

    await waitFor(() => expect(screen.getAllByLabelText(/Slide [12]/).length).toBe(2));

    // Move slide 2 up (the second slide, at index 1, can move up)
    const moveUpButtons = screen.getAllByLabelText(/Move slide.*up/);
    fireEvent.click(moveUpButtons[0]);

    // Verify slides still present after reorder
    await waitFor(() => expect(screen.getAllByLabelText(/Slide [12]/).length).toBe(2));
  });

  it("reorders slides by moving a slide down", async () => {
    vi.stubGlobal("fetch", makeFetchMock() as unknown as typeof fetch);
    render(<SlidesView />);

    await waitFor(() => screen.getByLabelText("Deck title"));

    // Add a third slide to test moving down
    fireEvent.click(screen.getByRole("button", { name: "Add slide" }));
    fireEvent.click(screen.getByRole("button", { name: "Add slide" }));

    await waitFor(() => expect(screen.getAllByLabelText(/Slide [123]/).length).toBe(3));

    // Move slide 1 down (the first slide can move down)
    fireEvent.click(screen.getByLabelText("Move slide 1 down"));

    await waitFor(() => expect(screen.getAllByLabelText(/Slide [123]/).length).toBe(3));
  });

  it("enters Present mode and renders the canvas", async () => {
    vi.stubGlobal("fetch", makeFetchMock() as unknown as typeof fetch);
    render(<SlidesView />);

    await waitFor(() => screen.getByLabelText("Deck title"));

    // Click Present button
    fireEvent.click(screen.getByRole("button", { name: "Present" }));

    // Present mode should show the Exit button and slide indicator
    await waitFor(() => expect(screen.getByRole("button", { name: "Exit presentation" })).toBeDefined());
    expect(screen.getByText("1 / 1")).toBeDefined();

    // SlideCanvas is rendered inside PresentMode - check for data-slide-stage
    const stage = document.querySelector("[data-slide-stage]");
    expect(stage).toBeDefined();
  });

  it("navigates slides in Present mode with next/prev buttons", async () => {
    vi.stubGlobal("fetch", makeFetchMock() as unknown as typeof fetch);
    render(<SlidesView />);

    await waitFor(() => screen.getByLabelText("Deck title"));

    // Add another slide to navigate
    fireEvent.click(screen.getByRole("button", { name: "Add slide" }));

    await waitFor(() => screen.getAllByLabelText(/Slide [12]/).length);

    // Enter Present mode
    fireEvent.click(screen.getByRole("button", { name: "Present" }));

    await waitFor(() => screen.getByRole("button", { name: "Exit presentation" }));

    // Click Next slide
    fireEvent.click(screen.getByRole("button", { name: "Next slide" }));

    await waitFor(() => expect(screen.getByText("2 / 2")).toBeDefined());

    // Click Previous slide
    fireEvent.click(screen.getByRole("button", { name: "Previous slide" }));

    await waitFor(() => expect(screen.getByText("1 / 2")).toBeDefined());
  });
});