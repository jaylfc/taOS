import { useState } from "react";
import { describe, it, expect, afterEach, beforeEach, vi } from "vitest";
import { render, screen, fireEvent, cleanup, within } from "@testing-library/react";
import { WebStudioApp } from "./WebStudioApp";
import { EditView } from "./webstudio/EditView";
import { exportSiteHtml, downloadSiteHtml } from "./webstudio/export";
import { emptySite, siteFromTemplate, TEMPLATES } from "./webstudio/templates";
import type { Site } from "./webstudio/types";

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

describe("WebStudioApp shell", () => {
  beforeEach(() => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => ({ ok: true, json: async () => [] })),
    );
  });

  it("renders the five studio views in the rail", () => {
    render(<WebStudioApp windowId="w1" />);
    const nav = screen.getByRole("navigation", { name: "Web Studio views" });
    const labels = within(nav)
      .getAllByRole("button")
      .map((b) => b.getAttribute("aria-label"));
    expect(labels).toEqual(["Generate", "Templates", "Edit", "Preview", "Export"]);
  });

  it("generate seeds a multi-section site into the Edit view", () => {
    const { container } = render(<WebStudioApp windowId="w1" />);
    // Pick a prompt idea, then generate.
    fireEvent.click(screen.getByRole("button", { name: "A cafe with a menu and bookings" }));
    fireEvent.click(screen.getByRole("button", { name: /Generate site/i }));
    // Now on the Edit view with real sections rendered.
    expect(screen.getByLabelText("Site title")).toBeInTheDocument();
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
