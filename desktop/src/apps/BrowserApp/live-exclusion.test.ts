import { describe, expect, it } from "vitest";
import { detectLiveExclusion } from "./live-exclusion";

/**
 * Helper: build an iframe whose contentDocument is set up via JSDOM
 * by writing HTML directly. JSDOM iframes default to about:blank with
 * a same-origin contentDocument.
 */
function makeIframe(bodyHtml: string): HTMLIFrameElement {
  const iframe = document.createElement("iframe");
  document.body.appendChild(iframe);
  const doc = iframe.contentDocument!;
  doc.body.innerHTML = bodyHtml;
  return iframe;
}

describe("detectLiveExclusion — pinned exemption", () => {
  it("returns 'pinned' when isPinned=true regardless of content", () => {
    const iframe = makeIframe("<p>hello</p>");
    expect(detectLiveExclusion(iframe, true)).toBe("pinned");
  });
});

describe("detectLiveExclusion — audio/video", () => {
  it("returns undefined when no media", () => {
    const iframe = makeIframe("<p>just text</p>");
    expect(detectLiveExclusion(iframe, false)).toBeUndefined();
  });

  it("returns undefined when video is paused", () => {
    const iframe = makeIframe("<video></video>");
    // JSDOM HTMLMediaElement.paused defaults to true
    expect(detectLiveExclusion(iframe, false)).toBeUndefined();
  });

  it("returns 'video' when a video is playing (paused=false)", () => {
    const iframe = makeIframe("<video></video>");
    const video = iframe.contentDocument!.querySelector("video") as HTMLVideoElement;
    Object.defineProperty(video, "paused", { value: false, configurable: true });
    Object.defineProperty(video, "ended", { value: false, configurable: true });
    expect(detectLiveExclusion(iframe, false)).toBe("video");
  });

  it("returns 'audio' when an audio element is playing", () => {
    const iframe = makeIframe("<audio></audio>");
    const audio = iframe.contentDocument!.querySelector("audio") as HTMLAudioElement;
    Object.defineProperty(audio, "paused", { value: false, configurable: true });
    Object.defineProperty(audio, "ended", { value: false, configurable: true });
    expect(detectLiveExclusion(iframe, false)).toBe("audio");
  });
});

describe("detectLiveExclusion — active form input", () => {
  it("returns 'form-active' when an input has non-empty value AND is focused", () => {
    const iframe = makeIframe('<input type="text">');
    const input = iframe.contentDocument!.querySelector("input") as HTMLInputElement;
    input.value = "some text";
    input.focus();
    expect(detectLiveExclusion(iframe, false)).toBe("form-active");
  });

  it("returns undefined when input is focused but empty", () => {
    const iframe = makeIframe('<input type="text">');
    const input = iframe.contentDocument!.querySelector("input") as HTMLInputElement;
    input.focus();
    expect(detectLiveExclusion(iframe, false)).toBeUndefined();
  });

  it("returns 'form-active' for focused textarea with content", () => {
    const iframe = makeIframe("<textarea></textarea>");
    const ta = iframe.contentDocument!.querySelector("textarea") as HTMLTextAreaElement;
    ta.value = "draft email";
    ta.focus();
    expect(detectLiveExclusion(iframe, false)).toBe("form-active");
  });
});

describe("detectLiveExclusion — upload in progress", () => {
  it("returns 'upload' when a file input has selected files", () => {
    const iframe = makeIframe('<input type="file">');
    const input = iframe.contentDocument!.querySelector("input") as HTMLInputElement;
    // Mock files property — JSDOM's FileList is read-only
    Object.defineProperty(input, "files", {
      value: [new File(["x"], "test.txt")],
      configurable: true,
    });
    expect(detectLiveExclusion(iframe, false)).toBe("upload");
  });

  it("returns undefined when file input has no selected files", () => {
    const iframe = makeIframe('<input type="file">');
    expect(detectLiveExclusion(iframe, false)).toBeUndefined();
  });
});

describe("detectLiveExclusion — graceful fallback", () => {
  it("returns undefined when contentDocument is null", () => {
    const iframe = document.createElement("iframe");
    // No appendChild — contentDocument may be null
    Object.defineProperty(iframe, "contentDocument", {
      value: null,
      configurable: true,
    });
    expect(detectLiveExclusion(iframe, false)).toBeUndefined();
  });
});
