import type { Deck } from "./deck";

// Renders the deck to a single downloadable PDF. Each page is a raster of
// one slide, captured from an offscreen DOM node the caller has already
// rendered (one child per slide, marked with data-export-slide, in deck
// order) via SlideCanvas at a fixed 1280x720 scale — see SlidesView's hidden
// export container.
//
// modern-screenshot (MIT, already a dependency, used elsewhere for desktop
// screenshots) rasterizes each node to a PNG data URL; jsPDF (MIT) composes
// those into pages. Both are dynamically imported so the cost is only paid
// when a user actually exports.
export const EXPORT_PAGE_WIDTH = 1280;
export const EXPORT_PAGE_HEIGHT = 720;

export async function exportDeckToPdf(deck: Deck, container: HTMLElement): Promise<void> {
  const nodes = Array.from(container.querySelectorAll<HTMLElement>("[data-export-slide]"));
  if (nodes.length === 0) return;

  const [{ domToPng }, { jsPDF }] = await Promise.all([
    import("modern-screenshot"),
    import("jspdf"),
  ]);

  const doc = new jsPDF({
    orientation: "landscape",
    unit: "px",
    format: [EXPORT_PAGE_WIDTH, EXPORT_PAGE_HEIGHT],
  });

  for (let i = 0; i < nodes.length; i++) {
    const node = nodes[i];
    if (!node) continue;
    const dataUrl = await domToPng(node, {
      width: EXPORT_PAGE_WIDTH,
      height: EXPORT_PAGE_HEIGHT,
    });
    if (i > 0) doc.addPage([EXPORT_PAGE_WIDTH, EXPORT_PAGE_HEIGHT], "landscape");
    doc.addImage(dataUrl, "PNG", 0, 0, EXPORT_PAGE_WIDTH, EXPORT_PAGE_HEIGHT);
  }

  const filename = `${(deck.title || "deck").trim().replace(/\s+/g, "-") || "deck"}.pdf`;
  doc.save(filename);
}
