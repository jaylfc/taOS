import { describe, it, expect } from "vitest";
import { handlerAppForFile, isText, isImage, isMedia } from "@/apps/FilesApp";

describe("FilesApp file handler routing", () => {
  describe("isText", () => {
    it("matches text and code extensions", () => {
      expect(isText("notes.txt")).toBe(true);
      expect(isText("README.md")).toBe(true);
      expect(isText("script.py")).toBe(true);
      expect(isText("app.tsx")).toBe(true);
    });

    it("rejects non-text extensions", () => {
      expect(isText("photo.jpg")).toBe(false);
      expect(isText("song.mp3")).toBe(false);
      expect(isText("movie.mp4")).toBe(false);
    });
  });

  describe("isImage", () => {
    it("matches image extensions", () => {
      expect(isImage("photo.png")).toBe(true);
      expect(isImage("pic.JPG")).toBe(true);
      expect(isImage("diagram.svg")).toBe(true);
    });

    it("rejects non-image extensions", () => {
      expect(isImage("doc.txt")).toBe(false);
      expect(isImage("song.mp3")).toBe(false);
    });
  });

  describe("isMedia", () => {
    it("matches audio and video extensions", () => {
      expect(isMedia("song.mp3")).toBe(true);
      expect(isMedia("clip.mp4")).toBe(true);
      expect(isMedia("movie.mkv")).toBe(true);
      expect(isMedia("audio.wav")).toBe(true);
    });

    it("rejects non-media extensions", () => {
      expect(isMedia("doc.txt")).toBe(false);
      expect(isMedia("photo.png")).toBe(false);
    });
  });

  describe("handlerAppForFile", () => {
    it("routes text files to text-editor", () => {
      expect(handlerAppForFile("notes.txt")).toBe("text-editor");
      expect(handlerAppForFile("script.py")).toBe("text-editor");
      expect(handlerAppForFile("README.md")).toBe("text-editor");
    });

    it("routes images to image-viewer", () => {
      expect(handlerAppForFile("photo.png")).toBe("image-viewer");
      expect(handlerAppForFile("pic.jpg")).toBe("image-viewer");
      expect(handlerAppForFile("diagram.svg")).toBe("image-viewer");
    });

    it("routes audio to media-player", () => {
      expect(handlerAppForFile("song.mp3")).toBe("media-player");
      expect(handlerAppForFile("audio.wav")).toBe("media-player");
    });

    it("routes video to media-player", () => {
      expect(handlerAppForFile("clip.mp4")).toBe("media-player");
      expect(handlerAppForFile("movie.mkv")).toBe("media-player");
      expect(handlerAppForFile("video.webm")).toBe("media-player");
    });

    it("returns null for unhandled file types", () => {
      expect(handlerAppForFile("archive.zip")).toBeNull();
      expect(handlerAppForFile("unknown.xyz")).toBeNull();
    });
  });
});
