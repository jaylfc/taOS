import { describe, it, expect } from "vitest";
import { render, screen } from "@testing-library/react";
import { AttachmentGallery } from "../AttachmentGallery";

const img = (url: string, name: string) => ({
  filename: name, mime_type: "image/png", size: 1, url, source: "disk" as const,
});

describe("AttachmentGallery", () => {
  it("renders nothing for empty list", () => {
    const { container } = render(<AttachmentGallery attachments={[]} />);
    expect(container.firstChild).toBeNull();
  });
  it("renders a single image inline", () => {
    render(<AttachmentGallery attachments={[img("/a.png", "a.png")]} />);
    expect(screen.getByAltText("a.png")).toBeInTheDocument();
  });
  it("renders a grid for 2+ images", () => {
    render(<AttachmentGallery attachments={[
      img("/a.png", "a.png"), img("/b.png", "b.png"),
    ]} />);
    expect(screen.getByAltText("a.png")).toBeInTheDocument();
    expect(screen.getByAltText("b.png")).toBeInTheDocument();
  });
  it("renders file tiles for non-image attachments", () => {
    render(<AttachmentGallery attachments={[{
      filename: "r.pdf", mime_type: "application/pdf",
      size: 1000, url: "/r.pdf", source: "disk",
    }]} />);
    expect(screen.getByText("r.pdf")).toBeInTheDocument();
  });
});
