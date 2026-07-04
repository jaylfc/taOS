import { render, screen, cleanup } from "@testing-library/react";
import { describe, it, expect, afterEach } from "vitest";
import { PreviewView } from "./PreviewView";
import { emptySite } from "./templates";
import type { Site } from "./types";

afterEach(() => cleanup());

/** A site with no sections at all (e.g. every section deleted). */
function siteWithNoSections(): Site {
  return { ...emptySite(), sections: [] };
}

describe("PreviewView", () => {
  it("points the iframe at the backend preview URL when saved, clean, and rendered", () => {
    render(<PreviewView site={emptySite()} siteId="site-abc123" dirty={false} hasRender />);
    const iframe = screen.getByTitle("Site preview") as HTMLIFrameElement;
    expect(iframe.getAttribute("src")).toBe("/api/web/sites/site-abc123/preview");
    expect(iframe.getAttribute("sandbox")).toBe("allow-scripts");
  });

  it("falls back to a sandboxed srcDoc of the live export when the site is unsaved", () => {
    render(<PreviewView site={emptySite()} siteId={null} dirty={false} hasRender={false} />);
    const iframe = screen.getByTitle("Site preview (unsaved)") as HTMLIFrameElement;
    expect(iframe.getAttribute("sandbox")).toBe("allow-scripts");
    expect(iframe.hasAttribute("src")).toBe(false);
  });

  it("falls back to srcDoc when a saved site has unsaved (dirty) edits", () => {
    render(<PreviewView site={emptySite()} siteId="site-abc123" dirty hasRender />);
    expect(screen.getByTitle("Site preview (unsaved)")).toBeInTheDocument();
    expect(screen.queryByTitle("Site preview")).not.toBeInTheDocument();
  });

  it("falls back to srcDoc for a legacy saved row with no stored render (no 404 blank frame)", () => {
    render(<PreviewView site={emptySite()} siteId="site-legacy" dirty={false} hasRender={false} />);
    expect(screen.getByTitle("Site preview (unsaved)")).toBeInTheDocument();
    expect(screen.queryByTitle("Site preview")).not.toBeInTheDocument();
  });

  it("shows an explicit empty state (not a blank iframe) when the site has no sections", () => {
    render(<PreviewView site={siteWithNoSections()} siteId="site-abc123" dirty={false} hasRender />);
    expect(screen.getByText("Nothing to preview yet")).toBeInTheDocument();
    expect(screen.queryByTitle("Site preview")).not.toBeInTheDocument();
    expect(screen.queryByTitle("Site preview (unsaved)")).not.toBeInTheDocument();
  });
});
