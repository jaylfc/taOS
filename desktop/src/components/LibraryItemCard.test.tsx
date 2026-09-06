import { describe, it, expect, vi } from "vitest";
import { render, screen, fireEvent } from "@testing-library/react";
import { LibraryItemCard } from "./LibraryItemCard";
import type { LibraryItem, LibraryArtifact, LibraryJob } from "@/lib/library";

/* ------------------------------------------------------------------ */
/*  Fixtures                                                           */
/* ------------------------------------------------------------------ */

function makeItem(overrides: Partial<LibraryItem> = {}): LibraryItem {
  return {
    id: "item-1",
    kind: "text",
    source_url: "",
    title: "Test Item",
    status: "pending",
    storage_path: "",
    bytes: 0,
    meta_json: "{}",
    created_at: 1000,
    updated_at: 1000,
    ...overrides,
  };
}

function makeArtifact(overrides: Partial<LibraryArtifact> = {}): LibraryArtifact {
  return {
    id: "art-1",
    item_id: "item-1",
    kind: "text",
    path: "/tmp/test.txt",
    meta_json: "{}",
    created_at: 1000,
    ...overrides,
  };
}

function makeJob(overrides: Partial<LibraryJob> = {}): LibraryJob {
  return {
    id: "job-1",
    item_id: "item-1",
    stage: "metadata",
    state: "queued",
    error: "",
    created_at: 1000,
    updated_at: 1000,
    ...overrides,
  };
}

/* ------------------------------------------------------------------ */
/*  Tests                                                              */
/* ------------------------------------------------------------------ */

describe("LibraryItemCard", () => {
  /* ---------------------------------------------------------------- */
  /*  Pending                                                          */
  /* ---------------------------------------------------------------- */

  describe("pending state", () => {
    it("renders the title, kind badge, and pending status", () => {
      render(<LibraryItemCard item={makeItem({ title: "My Video", kind: "url:youtube" })} />);
      expect(screen.getByText("My Video")).toBeInTheDocument();
      expect(screen.getByText("YouTube")).toBeInTheDocument();
      expect(screen.getByText("pending")).toBeInTheDocument();
    });

    it("shows a thumbnail placeholder when no thumbnail artifact exists", () => {
      render(<LibraryItemCard item={makeItem()} />);
      expect(screen.getByText("No thumbnail")).toBeInTheDocument();
      expect(screen.queryByAltText(/Thumbnail/)).not.toBeInTheDocument();
    });

    it("shows the no-pipeline-stages message", () => {
      render(<LibraryItemCard item={makeItem()} />);
      expect(screen.getByText("No pipeline stages")).toBeInTheDocument();
    });

    it("shows the no-artifacts message", () => {
      render(<LibraryItemCard item={makeItem()} />);
      expect(screen.getByText("No artifacts")).toBeInTheDocument();
    });

    it("disables the download button", () => {
      render(<LibraryItemCard item={makeItem()} />);
      const downloadBtn = screen.getByLabelText("Download (not available yet)");
      expect(downloadBtn).toBeDisabled();
    });

    it("renders the link-to-collection button", () => {
      render(<LibraryItemCard item={makeItem()} />);
      expect(screen.getByLabelText("Link to collection")).toBeInTheDocument();
    });
  });

  /* ---------------------------------------------------------------- */
  /*  Processing                                                       */
  /* ---------------------------------------------------------------- */

  describe("processing state", () => {
    const processingItem = makeItem({
      id: "item-2",
      kind: "text",
      title: "notes.txt",
      status: "processing",
      bytes: 2048,
      meta_json: JSON.stringify({}),
    });
    const processingJobs = [
      makeJob({ id: "job-a", item_id: "item-2", stage: "metadata", state: "done" }),
      makeJob({ id: "job-b", item_id: "item-2", stage: "text", state: "processing" }),
    ];

    it("renders the processing status", () => {
      render(
        <LibraryItemCard
          item={processingItem}
          jobs={processingJobs}
        />,
      );
      expect(screen.getByText("processing")).toBeInTheDocument();
    });

    it("shows pipeline stages with their states", () => {
      render(
        <LibraryItemCard
          item={processingItem}
          jobs={processingJobs}
        />,
      );
      expect(screen.getByText("metadata: done")).toBeInTheDocument();
      expect(screen.getByText("text: processing")).toBeInTheDocument();
    });

    it("disables the download button", () => {
      render(
        <LibraryItemCard
          item={processingItem}
          jobs={processingJobs}
        />,
      );
      expect(screen.getByLabelText("Download (not available yet)")).toBeDisabled();
    });
  });

  /* ---------------------------------------------------------------- */
  /*  Done (ready)                                                     */
  /* ---------------------------------------------------------------- */

  describe("done (ready) state", () => {
    const doneItem = makeItem({
      id: "item-3",
      kind: "text",
      title: "notes.txt",
      status: "ready",
      bytes: 1024,
      meta_json: JSON.stringify({
        preview: "This is the preview text of the document content.",
        duration: 120,
      }),
    });
    const doneArtifacts = [
      makeArtifact({
        id: "art-t",
        item_id: "item-3",
        kind: "thumbnail",
        path: "/tmp/thumb.jpg",
        meta_json: JSON.stringify({ width: 320, height: 240 }),
      }),
      makeArtifact({
        id: "art-x",
        item_id: "item-3",
        kind: "text",
        path: "/tmp/notes.txt",
        meta_json: JSON.stringify({ char_count: 1024, line_count: 20 }),
      }),
    ];
    const doneJobs = [
      makeJob({ id: "job-a", item_id: "item-3", stage: "metadata", state: "done" }),
      makeJob({ id: "job-b", item_id: "item-3", stage: "text", state: "done" }),
    ];

    it("renders the ready status", () => {
      render(
        <LibraryItemCard
          item={doneItem}
          artifacts={doneArtifacts}
          jobs={doneJobs}
        />,
      );
      expect(screen.getByText("ready")).toBeInTheDocument();
    });

    it("renders the thumbnail image", () => {
      render(
        <LibraryItemCard
          item={doneItem}
          artifacts={doneArtifacts}
          jobs={doneJobs}
        />,
      );
      const img = screen.getByAltText("Thumbnail for notes.txt") as HTMLImageElement;
      expect(img).toBeInTheDocument();
      expect(img.src).toContain("/tmp/thumb.jpg");
    });

    it("shows placeholder when thumbnail fails to load", () => {
      render(
        <LibraryItemCard
          item={doneItem}
          artifacts={doneArtifacts}
          jobs={doneJobs}
        />,
      );
      const img = screen.getByAltText("Thumbnail for notes.txt") as HTMLImageElement;
      fireEvent.error(img);
      expect(screen.getByText("No thumbnail")).toBeInTheDocument();
      expect(screen.queryByAltText(/Thumbnail/)).not.toBeInTheDocument();
    });

    it("shows the duration for media items", () => {
      render(
        <LibraryItemCard
          item={doneItem}
          artifacts={doneArtifacts}
          jobs={doneJobs}
        />,
      );
      expect(screen.getByText("2:00")).toBeInTheDocument();
    });

    it("shows artifacts with their preview text", () => {
      render(
        <LibraryItemCard
          item={doneItem}
          artifacts={doneArtifacts}
          jobs={doneJobs}
        />,
      );
      expect(screen.getByText("text")).toBeInTheDocument();
      expect(screen.getByText("1024 chars")).toBeInTheDocument();
      expect(
        screen.getByText("This is the preview text of the document content."),
      ).toBeInTheDocument();
    });

    it("shows pipeline stages as done", () => {
      render(
        <LibraryItemCard
          item={doneItem}
          artifacts={doneArtifacts}
          jobs={doneJobs}
        />,
      );
      expect(screen.getByText("metadata: done")).toBeInTheDocument();
      expect(screen.getByText("text: done")).toBeInTheDocument();
    });

    it("calls onLinkToCollection when the link button is clicked", () => {
      const onLinkToCollection = vi.fn();
      render(
        <LibraryItemCard
          item={doneItem}
          artifacts={doneArtifacts}
          jobs={doneJobs}
          onLinkToCollection={onLinkToCollection}
        />,
      );
      fireEvent.click(screen.getByLabelText("Link to collection"));
      expect(onLinkToCollection).toHaveBeenCalledTimes(1);
      expect(onLinkToCollection).toHaveBeenCalledWith(doneItem);
    });

    it("disables the download button", () => {
      render(
        <LibraryItemCard
          item={doneItem}
          artifacts={doneArtifacts}
          jobs={doneJobs}
        />,
      );
      expect(screen.getByLabelText("Download (not available yet)")).toBeDisabled();
    });

    it("shows the source URL button when source_url is set", () => {
      const youtubeItem = makeItem({
        id: "item-yt",
        kind: "url:youtube",
        title: "YouTube Video",
        status: "ready",
        source_url: "https://youtube.com/watch?v=abc",
        meta_json: JSON.stringify({}),
      });
      render(<LibraryItemCard item={youtubeItem} />);
      expect(screen.getByLabelText("Open source https://youtube.com/watch?v=abc")).toBeInTheDocument();
    });
  });

  /* ---------------------------------------------------------------- */
  /*  Failed (error)                                                   */
  /* ---------------------------------------------------------------- */

  describe("failed (error) state", () => {
    const failedItem = makeItem({
      id: "item-4",
      kind: "pdf",
      title: "broken.pdf",
      status: "error",
      bytes: 512,
      meta_json: JSON.stringify({
        error: "Source file not found: /tmp/broken.pdf",
      }),
    });
    const failedJobs = [
      makeJob({
        id: "job-a",
        item_id: "item-4",
        stage: "metadata",
        state: "failed",
        error: "Source file not found",
      }),
    ];

    it("renders the error status", () => {
      render(
        <LibraryItemCard
          item={failedItem}
          jobs={failedJobs}
        />,
      );
      expect(screen.getByText("error")).toBeInTheDocument();
    });

    it("shows the error message in an alert", () => {
      render(
        <LibraryItemCard
          item={failedItem}
          jobs={failedJobs}
        />,
      );
      const alert = screen.getByRole("alert");
      expect(alert).toHaveTextContent("Source file not found: /tmp/broken.pdf");
    });

    it("shows the failed pipeline stage", () => {
      render(
        <LibraryItemCard
          item={failedItem}
          jobs={failedJobs}
        />,
      );
      expect(screen.getByText("metadata: failed")).toBeInTheDocument();
    });

    it("disables the download button", () => {
      render(
        <LibraryItemCard
          item={failedItem}
          jobs={failedJobs}
        />,
      );
      expect(screen.getByLabelText("Download (not available yet)")).toBeDisabled();
    });

    it("shows a default error message when meta_json has no error field", () => {
      const itemNoError = makeItem({
        id: "item-5",
        status: "error",
        meta_json: "{}",
      });
      render(<LibraryItemCard item={itemNoError} />);
      const alert = screen.getByRole("alert");
      expect(alert).toHaveTextContent("Processing failed");
    });
  });

  /* ---------------------------------------------------------------- */
  /*  Edge cases                                                       */
  /* ---------------------------------------------------------------- */

  describe("edge cases", () => {
    it("renders Untitled when title is empty", () => {
      render(<LibraryItemCard item={makeItem({ title: "" })} />);
      expect(screen.getByText("Untitled")).toBeInTheDocument();
    });

    it("shows no preview available when item has no preview in meta_json", () => {
      const item = makeItem({
        status: "ready",
        meta_json: JSON.stringify({}),
      });
      const artifacts = [makeArtifact({ kind: "text" })];
      render(<LibraryItemCard item={item} artifacts={artifacts} />);
      expect(screen.getByText("No preview available")).toBeInTheDocument();
    });

    it("renders transcript and ocr artifacts in the list", () => {
      const item = makeItem({
        status: "ready",
        meta_json: JSON.stringify({ preview: "preview text" }),
      });
      const artifacts = [
        makeArtifact({ id: "a1", kind: "transcript" }),
        makeArtifact({ id: "a2", kind: "ocr" }),
      ];
      render(<LibraryItemCard item={item} artifacts={artifacts} />);
      expect(screen.getByText("transcript")).toBeInTheDocument();
      expect(screen.getByText("ocr")).toBeInTheDocument();
    });
  });
});
