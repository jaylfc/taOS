import { render, screen, cleanup } from "@testing-library/react";
import { describe, it, expect, afterEach } from "vitest";
import { PreviewView } from "./PreviewView";
import { emptySite } from "./templates";

afterEach(() => cleanup());

describe("PreviewView", () => {
  it("points the iframe at the backend preview URL when the site is saved and clean", () => {
    render(<PreviewView site={emptySite()} siteId="site-abc123" dirty={false} />);
    const iframe = screen.getByTitle("Site preview") as HTMLIFrameElement;
    expect(iframe.getAttribute("src")).toBe("/api/web/sites/site-abc123/preview");
    expect(iframe.getAttribute("sandbox")).toBe("allow-scripts");
  });

  it("falls back to a sandboxed srcDoc of the live export when the site is unsaved", () => {
    render(<PreviewView site={emptySite()} siteId={null} dirty={false} />);
    const iframe = screen.getByTitle("Site preview (unsaved)") as HTMLIFrameElement;
    expect(iframe.getAttribute("sandbox")).toBe("allow-scripts");
    expect(iframe.hasAttribute("src")).toBe(false);
  });

  it("falls back to srcDoc when a saved site has unsaved (dirty) edits", () => {
    render(<PreviewView site={emptySite()} siteId="site-abc123" dirty />);
    expect(screen.getByTitle("Site preview (unsaved)")).toBeInTheDocument();
    expect(screen.queryByTitle("Site preview")).not.toBeInTheDocument();
  });
});
