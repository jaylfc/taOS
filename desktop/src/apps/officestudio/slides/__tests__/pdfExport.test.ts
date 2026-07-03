import { afterEach, describe, expect, it, vi } from "vitest";
import { blankDeck } from "../deck";
import { EXPORT_PAGE_HEIGHT, EXPORT_PAGE_WIDTH, exportDeckToPdf } from "../pdfExport";

const domToPngMock = vi.fn(async () => "data:image/png;base64,mock");
const addImageMock = vi.fn();
const addPageMock = vi.fn();
const saveMock = vi.fn();

vi.mock("modern-screenshot", () => ({
  domToPng: (...args: unknown[]) => domToPngMock(...args),
}));

vi.mock("jspdf", () => ({
  // A plain function (not an arrow function) so it can be invoked with
  // `new`; returning an object from a constructor call overrides `this`
  // with that object, which is all exportDeckToPdf needs from jsPDF.
  jsPDF: vi.fn().mockImplementation(function jsPDFMock() {
    return { addImage: addImageMock, addPage: addPageMock, save: saveMock };
  }),
}));

function makeExportContainer(slideCount: number): HTMLElement {
  const container = document.createElement("div");
  for (let i = 0; i < slideCount; i++) {
    const node = document.createElement("div");
    node.setAttribute("data-export-slide", "");
    container.appendChild(node);
  }
  document.body.appendChild(container);
  return container;
}

describe("exportDeckToPdf", () => {
  afterEach(() => {
    domToPngMock.mockClear();
    addImageMock.mockClear();
    addPageMock.mockClear();
    saveMock.mockClear();
    document.body.innerHTML = "";
  });

  it("rasterizes each marked slide node and adds one PDF page per slide", async () => {
    const deck = { ...blankDeck(), title: "My Deck" };
    const container = makeExportContainer(3);

    await exportDeckToPdf(deck, container);

    expect(domToPngMock).toHaveBeenCalledTimes(3);
    // first slide does not call addPage (the jsPDF constructor already made page 1)
    expect(addPageMock).toHaveBeenCalledTimes(2);
    expect(addImageMock).toHaveBeenCalledTimes(3);
    expect(addImageMock).toHaveBeenCalledWith(
      "data:image/png;base64,mock",
      "PNG",
      0,
      0,
      EXPORT_PAGE_WIDTH,
      EXPORT_PAGE_HEIGHT,
    );
    expect(saveMock).toHaveBeenCalledWith("My-Deck.pdf");
  });

  it("does nothing when there are no export-marked slide nodes", async () => {
    const deck = blankDeck();
    const container = document.createElement("div");
    document.body.appendChild(container);

    await exportDeckToPdf(deck, container);

    expect(domToPngMock).not.toHaveBeenCalled();
    expect(saveMock).not.toHaveBeenCalled();
  });

  it("falls back to a generic filename when the deck has no title", async () => {
    const deck = { ...blankDeck(), title: "   " };
    const container = makeExportContainer(1);

    await exportDeckToPdf(deck, container);

    expect(saveMock).toHaveBeenCalledWith("deck.pdf");
  });
});
