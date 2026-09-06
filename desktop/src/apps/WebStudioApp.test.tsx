import { useState } from "react";
import { describe, it, expect, afterEach, beforeEach, vi } from "vitest";
import { render, screen, fireEvent, cleanup, within, waitFor } from "@testing-library/react";
import { WebStudioApp } from "./WebStudioApp";
import { EditView } from "./webstudio/EditView";
import { SectionBlock } from "./webstudio/SectionBlock";
import { exportSiteHtml, downloadSiteHtml } from "./webstudio/export";
import { emptySite, siteFromTemplate, newSection, TEMPLATES } from "./webstudio/templates";
import { isValidSite, PALETTES, FONTS, type Site } from "./webstudio/types";

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

/** Controlled harness so EditView mutations are observable via the DOM. */
function EditHarness({ initial }: { initial: Site }) {
  const [site, setSite] = useState<Site>(initial);
  return (
    <EditView
      site={site}
      onChange={setSite}
      saved={[]}
      activeId={null}
      loading={false}
      saving={false}
      error={null}
      onNew={() => {}}
      onOpen={() => {}}
      onSave={() => {}}
      onDelete={() => {}}
    />
  );
}

function sectionTypes(container: HTMLElement): string[] {
  return [...container.querySelectorAll("[data-section-id]")].map(
    (el) => el.getAttribute("data-section-type") ?? "",
  );
}

/** A /api/taos-agent/chat NDJSON stream whose deltas concatenate to `text`. */
function chatStreamResponse(text: string): Response {
  const encoder = new TextEncoder();
  const body = new ReadableStream<Uint8Array>({
    start(controller) {
      controller.enqueue(encoder.encode(JSON.stringify({ delta: text }) + "\n"));
      controller.close();
    },
  });
  return new Response(body, { status: 200 });
}

describe("WebStudioApp shell", () => {
  beforeEach(() => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async (url: string) => {
        if (url === "/api/taos-agent/chat") {
          // No real agent in tests: an unparsable reply exercises the same
          // honest matchTemplate() fallback a real agent failure would.
          return chatStreamResponse("Sorry, I can't help with that request.");
        }
        return { ok: true, json: async () => [] };
      }),
    );
  });

  it("renders the six studio views in the rail", () => {
    render(<WebStudioApp windowId="w1" />);
    const nav = screen.getByRole("navigation", { name: "Web Studio views" });
    const labels = within(nav)
      .getAllByRole("button")
      .map((b) => b.getAttribute("aria-label"));
    expect(labels).toEqual(["Generate", "Templates", "Edit", "Preview", "Export", "Share"]);
  });

  it("generate seeds a multi-section site into the Edit view, falling back to a template", async () => {
    const { container } = render(<WebStudioApp windowId="w1" />);
    // Pick a prompt idea, then generate. No real agent in tests, so this
    // exercises the honest matchTemplate() fallback path.
    fireEvent.click(screen.getByRole("button", { name: "A cafe with a menu and bookings" }));
    fireEvent.click(screen.getByRole("button", { name: /Generate site/i }));
    // Now on the Edit view with real sections rendered.
    await waitFor(() => expect(screen.getByLabelText("Site title")).toBeInTheDocument());
    expect(sectionTypes(container).length).toBeGreaterThan(1);
    expect(sectionTypes(container)).toContain("hero");
  });
});

describe("WebStudio EditView model edits", () => {
  it("adds a section to the model", () => {
    const { container } = render(<EditHarness initial={emptySite()} />);
    const before = sectionTypes(container).length;
    fireEvent.click(screen.getByRole("button", { name: "Features" }));
    expect(sectionTypes(container).length).toBe(before + 1);
    expect(sectionTypes(container)).toContain("features");
  });

  it("removes the selected section from the model", () => {
    const { container } = render(<EditHarness initial={emptySite()} />);
    const before = sectionTypes(container).length;
    // Adding selects the new section; deleting it should return to `before`.
    fireEvent.click(screen.getByRole("button", { name: "Text block" }));
    expect(sectionTypes(container).length).toBe(before + 1);
    fireEvent.click(screen.getByRole("button", { name: "Delete section" }));
    expect(sectionTypes(container).length).toBe(before);
  });

  it("reorders sections when moving down", () => {
    const site = siteFromTemplate(TEMPLATES[0]); // hero, features, cta, footer
    const { container } = render(<EditHarness initial={site} />);
    const order = sectionTypes(container);
    expect(order[0]).toBe("hero");
    // Select the first section on the canvas, then move it down.
    container.querySelector("[data-section-id]")!.dispatchEvent(new MouseEvent("click", { bubbles: true }));
    fireEvent.click(screen.getByRole("button", { name: "Move section down" }));
    const after = sectionTypes(container);
    expect(after[0]).toBe(order[1]);
    expect(after[1]).toBe("hero");
  });

  it("commits an inline text edit into the model", () => {
    const { container } = render(<EditHarness initial={emptySite()} />);
    const heading = screen.getByRole("textbox", { name: "Hero heading" });
    heading.textContent = "My brand new headline";
    fireEvent.blur(heading);
    expect(within(container).getByText("My brand new headline")).toBeInTheDocument();
  });
});

describe("WebStudio static HTML export", () => {
  it("serializes a site to a self-contained HTML document", () => {
    const html = exportSiteHtml(emptySite());
    expect(html.startsWith("<!doctype html>")).toBe(true);
    expect(html).toContain("<style>");
    expect(html).toContain("Your Business"); // footer default
    expect(html).toContain("<title>Untitled site</title>");
  });

  it("triggers a .html download", () => {
    vi.stubGlobal("URL", {
      createObjectURL: vi.fn(() => "blob:mock"),
      revokeObjectURL: vi.fn(),
    });
    const clickSpy = vi.spyOn(HTMLAnchorElement.prototype, "click").mockImplementation(() => {});
    downloadSiteHtml({ ...emptySite(), title: "My Site" });
    expect(clickSpy).toHaveBeenCalledOnce();
    clickSpy.mockRestore();
  });
});

describe("isValidSite", () => {
  it("accepts a well-formed site", () => {
    expect(isValidSite(emptySite())).toBe(true);
  });

  it("rejects null, primitives and arrays", () => {
    expect(isValidSite(null)).toBe(false);
    expect(isValidSite(undefined)).toBe(false);
    expect(isValidSite("not a site")).toBe(false);
    expect(isValidSite(42)).toBe(false);
    expect(isValidSite([])).toBe(false);
  });

  it("rejects an object missing a sections array", () => {
    expect(
      isValidSite({ title: "X", theme: { palette: "midnight", font: "sans" } }),
    ).toBe(false);
  });

  it("rejects an object with a non-string title", () => {
    expect(isValidSite({ title: 5, theme: {}, sections: [] })).toBe(false);
  });

  it("rejects an object with a missing theme", () => {
    expect(isValidSite({ title: "X", sections: [] })).toBe(false);
  });
});

describe("WebStudioApp opens a corrupted saved site", () => {
  it("falls back to a blank site and surfaces an error instead of crashing", async () => {
    const savedList = [{ id: "site-abc", title: "Broken", updated_at: 1 }];
    vi.stubGlobal(
      "fetch",
      vi.fn(async (url: string) => {
        if (url === "/api/web/sites") {
          return { ok: true, json: async () => savedList };
        }
        if (url === "/api/web/sites/site-abc") {
          return {
            ok: true,
            json: async () => ({ id: "site-abc", title: "Broken", content: "{not valid json" }),
          };
        }
        return { ok: false, json: async () => ({}) };
      }),
    );

    render(<WebStudioApp windowId="w1" />);
    fireEvent.click(screen.getByRole("button", { name: "Edit" }));
    const item = await screen.findByRole("button", { name: /^Broken/i });
    fireEvent.click(item);

    await screen.findByText(/corrupted/i);
    expect(screen.getByLabelText("Site title")).toHaveValue("Untitled site");
  });
});

describe("SectionBlock image upload validation", () => {
  function fakeFile(name: string, type: string, size: number): File {
    const file = new File([new Uint8Array(1)], name, { type });
    Object.defineProperty(file, "size", { value: size });
    return file;
  }

  it("rejects an oversized image without calling onChange", async () => {
    const alertSpy = vi.spyOn(window, "alert").mockImplementation(() => {});
    const onChange = vi.fn();
    render(
      <SectionBlock
        section={newSection("hero")}
        colors={PALETTES.midnight.colors}
        fontStack={FONTS.sans.stack}
        editable
        onChange={onChange}
      />,
    );
    const input = screen.getByLabelText("Upload hero image") as HTMLInputElement;
    Object.defineProperty(input, "files", { value: [fakeFile("big.png", "image/png", 3 * 1024 * 1024)] });
    fireEvent.change(input);

    await waitFor(() => expect(alertSpy).toHaveBeenCalled());
    expect(alertSpy.mock.calls[0]?.[0]).toMatch(/larger than/i);
    expect(onChange).not.toHaveBeenCalled();
    alertSpy.mockRestore();
  });

  it("rejects a non-image file without calling onChange", async () => {
    const alertSpy = vi.spyOn(window, "alert").mockImplementation(() => {});
    const onChange = vi.fn();
    render(
      <SectionBlock
        section={newSection("hero")}
        colors={PALETTES.midnight.colors}
        fontStack={FONTS.sans.stack}
        editable
        onChange={onChange}
      />,
    );
    const input = screen.getByLabelText("Upload hero image") as HTMLInputElement;
    Object.defineProperty(input, "files", { value: [fakeFile("doc.pdf", "application/pdf", 1024)] });
    fireEvent.change(input);

    await waitFor(() => expect(alertSpy).toHaveBeenCalled());
    expect(alertSpy.mock.calls[0]?.[0]).toMatch(/not an image/i);
    expect(onChange).not.toHaveBeenCalled();
    alertSpy.mockRestore();
  });
});

describe("WebStudioApp confirms before discarding unsaved edits", () => {
  it("prompts before New discards a dirty in-memory site", () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => ({ ok: true, json: async () => [] })),
    );
    render(<WebStudioApp windowId="w1" />);
    fireEvent.click(screen.getByRole("button", { name: "Edit" }));

    const heading = screen.getByRole("textbox", { name: "Hero heading" });
    heading.textContent = "Edited but unsaved";
    fireEvent.blur(heading);

    const confirmSpy = vi.spyOn(window, "confirm").mockReturnValue(false);
    fireEvent.click(screen.getByRole("button", { name: "New" }));
    expect(confirmSpy).toHaveBeenCalled();
    // User declined the confirm, so the edit is still there.
    expect(screen.getByText("Edited but unsaved")).toBeInTheDocument();

    confirmSpy.mockReturnValue(true);
    fireEvent.click(screen.getByRole("button", { name: "New" }));
    expect(screen.queryByText("Edited but unsaved")).not.toBeInTheDocument();
    confirmSpy.mockRestore();
  });
});
