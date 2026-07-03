import { describe, expect, it } from "vitest";
import {
  addSlide,
  blankDeck,
  insertSlideAfter,
  moveSlideBy,
  newSlide,
  parseDeckContent,
  reorderSlide,
  removeSlide,
  serializeDeck,
  updateSlide,
  type Deck,
  type Slide,
} from "../deck";

function makeSlides(n: number): Slide[] {
  return Array.from({ length: n }, (_, i) => ({
    ...newSlide("title-content"),
    id: `s${i + 1}`,
    title: `Slide ${i + 1}`,
  }));
}

describe("blankDeck", () => {
  it("starts with exactly one slide", () => {
    const deck = blankDeck();
    expect(deck.slides).toHaveLength(1);
    expect(deck.title).toBe("Untitled deck");
  });
});

describe("addSlide / insertSlideAfter", () => {
  it("appends a slide when afterId is null", () => {
    const slides = makeSlides(2);
    const next = addSlide(slides, null);
    expect(next).toHaveLength(3);
    expect(next[2].id).not.toBe(slides[0].id);
    expect(next[2].id).not.toBe(slides[1].id);
  });

  it("inserts right after the given slide", () => {
    const slides = makeSlides(3);
    const slide = newSlide();
    const next = insertSlideAfter(slides, "s1", slide);
    expect(next.map((s) => s.id)).toEqual(["s1", slide.id, "s2", "s3"]);
  });

  it("falls back to appending when afterId is not found", () => {
    const slides = makeSlides(2);
    const slide = newSlide();
    const next = insertSlideAfter(slides, "missing", slide);
    expect(next.map((s) => s.id)).toEqual(["s1", "s2", slide.id]);
  });
});

describe("removeSlide", () => {
  it("removes the slide with the given id", () => {
    const slides = makeSlides(3);
    const next = removeSlide(slides, "s2");
    expect(next.map((s) => s.id)).toEqual(["s1", "s3"]);
  });

  it("refuses to remove the last remaining slide", () => {
    const slides = makeSlides(1);
    const next = removeSlide(slides, "s1");
    expect(next).toHaveLength(1);
    expect(next).toBe(slides);
  });
});

describe("updateSlide", () => {
  it("patches the title/body/bullets/layout of the target slide only", () => {
    const slides = makeSlides(2);
    const next = updateSlide(slides, "s1", {
      title: "New title",
      body: "New body",
      bullets: ["a", "b"],
      layout: "two-column",
    });
    expect(next[0]).toMatchObject({
      title: "New title",
      body: "New body",
      bullets: ["a", "b"],
      layout: "two-column",
    });
    expect(next[1]).toBe(slides[1]);
  });

  it("does not mutate the source array", () => {
    const slides = makeSlides(2);
    const before = JSON.stringify(slides);
    updateSlide(slides, "s1", { title: "changed" });
    expect(JSON.stringify(slides)).toBe(before);
  });
});

describe("reorderSlide / moveSlideBy", () => {
  it("moves a slide from one index to another", () => {
    const slides = makeSlides(4);
    const next = reorderSlide(slides, 0, 2);
    expect(next.map((s) => s.id)).toEqual(["s2", "s3", "s1", "s4"]);
  });

  it("clamps an out-of-range target index instead of throwing", () => {
    const slides = makeSlides(3);
    const next = reorderSlide(slides, 0, 99);
    expect(next.map((s) => s.id)).toEqual(["s2", "s3", "s1"]);
  });

  it("is a no-op for an out-of-range source index", () => {
    const slides = makeSlides(3);
    const next = reorderSlide(slides, 10, 0);
    expect(next).toBe(slides);
  });

  it("moveSlideBy moves the slide with the given id by a relative delta", () => {
    const slides = makeSlides(3);
    const next = moveSlideBy(slides, "s3", -2);
    expect(next.map((s) => s.id)).toEqual(["s3", "s1", "s2"]);
  });

  it("moveSlideBy is a no-op for an unknown id", () => {
    const slides = makeSlides(3);
    const next = moveSlideBy(slides, "missing", 1);
    expect(next).toBe(slides);
  });
});

describe("serializeDeck / parseDeckContent round trip", () => {
  it("round-trips a deck with multiple slides and fields untouched", () => {
    const deck: Deck = {
      version: 1,
      title: "Quarterly review",
      slides: [
        {
          id: "s1",
          layout: "title",
          title: "Q3 results",
          body: "A strong quarter",
          bullets: [],
          notes: "Smile",
        },
        {
          id: "s2",
          layout: "two-column",
          title: "Breakdown",
          body: "",
          bullets: ["Revenue up", "Costs down"],
          imageDataUri: "data:image/png;base64,AAAA",
        },
      ],
    };
    const json = serializeDeck(deck);
    const parsed = parseDeckContent(json);
    expect(parsed).toEqual(deck);
  });

  // blankDeck() mints a fresh random slide id each call, so a fallback deck
  // can't be compared with toEqual(blankDeck()) directly; check its shape
  // instead (single untitled "title" slide).
  function expectBlankDeckShape(deck: Deck) {
    expect(deck.title).toBe("Untitled deck");
    expect(deck.slides).toHaveLength(1);
    expect(deck.slides[0]).toMatchObject({ layout: "title", title: "", body: "", bullets: [] });
  }

  it("falls back to a blank deck for empty content", () => {
    expectBlankDeckShape(parseDeckContent(""));
    expectBlankDeckShape(parseDeckContent("   "));
  });

  it("falls back to a blank deck for malformed JSON", () => {
    expectBlankDeckShape(parseDeckContent("{not json"));
  });

  it("falls back to a blank deck when a slide is missing required fields", () => {
    const corrupted = JSON.stringify({
      version: 1,
      title: "Broken",
      slides: [{ id: "s1", layout: "title" }],
    });
    expectBlankDeckShape(parseDeckContent(corrupted));
  });

  it("falls back to a blank deck when slides is empty", () => {
    const empty = JSON.stringify({ version: 1, title: "Empty", slides: [] });
    expectBlankDeckShape(parseDeckContent(empty));
  });
});
