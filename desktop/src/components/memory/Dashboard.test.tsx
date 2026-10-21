import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { render, screen } from "@testing-library/react";
import { Dashboard } from "./Dashboard";

const originalFetch = global.fetch;

beforeEach(() => {
  global.fetch = vi.fn();
});

afterEach(() => {
  global.fetch = originalFetch;
  vi.restoreAllMocks();
});

describe("Dashboard", () => {
  it("renders crystal count and withLessons when stats.crystals is an object with total_crystals and with_lessons", async () => {
    // Mock fetchMemoryStats to return stats.crystals as {total_crystals: 3, with_lessons: 1}
    // Mock fetchCatalogStats to return something (we don't care for this test because stats.crystals is valid)
    global.fetch
      .mockResolvedValueOnce({
        ok: true,
        status: 200,
        headers: new Map([["content-type", "application/json"]]),
        json: async () => ({
          crystals: { total_crystals: 3, with_lessons: 1 },
          // other stats can be empty or default
          kg_entities: 0,
          kg_triples: 0,
          vector_count: 0,
          archive_events: 0,
        }),
      })
      .mockResolvedValueOnce({
        ok: true,
        status: 200,
        headers: new Map([["content-type", "application/json"]]),
        json: async () => ({
          total_sessions: 0,
          // other catalog stats
        }),
      });

    render(<Dashboard />);

    // Wait for the label to appear (ensures component is rendered)
    await screen.findByText(/crystals/i);

    // Find the crystal label element
    const crystalLabel = await screen.findByText(/crystals/i);
    // Get the parent div that contains the label and value
    const crystalValueDiv = crystalLabel.parentElement;
    if (!crystalValueDiv) {
      throw new Error("Could not find parent div for crystal label");
    }
    // The value element is the second child (index 1) of the valueDiv? 
    // Structure: [icon div, text container div] in the stat card.
    // The text container div has: [label p, value p, sub p]
    // So we need to get the text container div first.
    const statCardContainer = crystalLabel.parentElement?.parentElement;
    if (!statCardContainer) {
      throw new Error("Could not find stat card container");
    }
    // The stat card container has two children: icon div and text container div.
    const textContainer = statCardContainer.children[1];
    if (!textContainer) {
      throw new Error("Could not find text container");
    }
    // The value p is the second child (index 1) of the text container.
    const crystalValueElement = textContainer.children[1] as HTMLElement;
    expect(crystalValueElement).toBeInTheDocument();
    expect(crystalValueElement.textContent).toBe("3");

    // The sub p is the third child (index 2) of the text container.
    const crystalSubElement = textContainer.children[2] as HTMLElement;
    expect(crystalSubElement).toBeInTheDocument();
    expect(crystalSubElement.textContent).toBe("1 with lessons");

    // Ensure the value is not "[object Object]"
    expect(crystalValueElement.textContent).not.toBe("[object Object]");
  });

  it("renders dash when stats.crystals is an empty object", async () => {
    // Mock fetchMemoryStats to return stats.crystals as {}
    global.fetch
      .mockResolvedValueOnce({
        ok: true,
        status: 200,
        headers: new Map([["content-type", "application/json"]]),
        json: async () => ({
          crystals: {},
          // other stats can be empty or default
          kg_entities: 0,
          kg_triples: 0,
          vector_count: 0,
          archive_events: 0,
        }),
      })
      .mockResolvedValueOnce({
        ok: true,
        status: 200,
        headers: new Map([["content-type", "application/json"]]),
        json: async () => ({
          total_sessions: 0,
        }),
      });

    render(<Dashboard />);

    // Wait for the label to appear
    await screen.findByText(/crystals/i);

    // Find the crystal label element
    const crystalLabel = await screen.findByText(/crystals/i);
    // Get the parent div that contains the label and value
    const crystalValueDiv = crystalLabel.parentElement;
    if (!crystalValueDiv) {
      throw new Error("Could not find parent div for crystal label");
    }
    // Get the stat card container
    const statCardContainer = crystalLabel.parentElement?.parentElement;
    if (!statCardContainer) {
      throw new Error("Could not find stat card container");
    }
    // The text container div is the second child of the stat card container.
    const textContainer = statCardContainer.children[1];
    if (!textContainer) {
      throw new Error("Could not find text container");
    }
    // The value p is the second child (index 1) of the text container.
    const crystalValueElement = textContainer.children[1] as HTMLElement;
    expect(crystalValueElement).toBeInTheDocument();
    expect(crystalValueElement.textContent).toBe("—");

    // The sub p is the third child (index 2) of the text container.
    const crystalSubElement = textContainer.children[2] as HTMLElement;
    expect(crystalSubElement).toBeInTheDocument();
    expect(crystalSubElement.textContent).toBe("narratives");

    // Ensure the value is not "[object Object]"
    expect(crystalValueElement.textContent).not.toBe("[object Object]");
  });

  it("renders crystal count when stats.crystals is a number", async () => {
    // Mock fetchMemoryStats to return stats.crystals as 7
    global.fetch
      .mockResolvedValueOnce({
        ok: true,
        status: 200,
        headers: new Map([["content-type", "application/json"]]),
        json: async () => ({
          crystals: 7,
          // other stats can be empty or default
          kg_entities: 0,
          kg_triples: 0,
          vector_count: 0,
          archive_events: 0,
        }),
      })
      .mockResolvedValueOnce({
        ok: true,
        status: 200,
        headers: new Map([["content-type", "application/json"]]),
        json: async () => ({
          total_sessions: 0,
        }),
      });

    render(<Dashboard />);

    // Wait for the label to appear
    await screen.findByText(/crystals/i);

    // Find the crystal label element
    const crystalLabel = await screen.findByText(/crystals/i);
    // Get the parent div that contains the label and value
    const crystalValueDiv = crystalLabel.parentElement;
    if (!crystalValueDiv) {
      throw new Error("Could not find parent div for crystal label");
    }
    // Get the stat card container
    const statCardContainer = crystalLabel.parentElement?.parentElement;
    if (!statCardContainer) {
      throw new Error("Could not find stat card container");
    }
    // The text container div is the second child of the stat card container.
    const textContainer = statCardContainer.children[1];
    if (!textContainer) {
      throw new Error("Could not find text container");
    }
    // The value p is the second child (index 1) of the text container.
    const crystalValueElement = textContainer.children[1] as HTMLElement;
    expect(crystalValueElement).toBeInTheDocument();
    expect(crystalValueElement.textContent).toBe("7");

    // The sub p is the third child (index 2) of the text container.
    const crystalSubElement = textContainer.children[2] as HTMLElement;
    expect(crystalSubElement).toBeInTheDocument();
    expect(crystalSubElement.textContent).toBe("narratives");

    // Ensure the value is not "[object Object]"
    expect(crystalValueElement.textContent).not.toBe("[object Object]");
  });
});