import { render } from "@testing-library/react";
import { describe, it, expect, vi, afterEach } from "vitest";
import BrowserApp from "./BrowserApp";

describe("BrowserApp initialUrl", () => {
  afterEach(() => vi.clearAllMocks());

  it("sets iframe src to initialUrl on first mount", () => {
    const { container } = render(
      <BrowserApp initialUrl="https://openclaw.local/ui" />,
    );
    const iframe = container.querySelector("iframe");
    expect(iframe).not.toBeNull();
    expect(iframe?.src).toBe("https://openclaw.local/ui");
  });

  it("does not override user-navigated src on re-mount when initialUrl unchanged", () => {
    const { container, rerender } = render(
      <BrowserApp initialUrl="https://openclaw.local/ui" />,
    );
    const iframe = container.querySelector("iframe") as HTMLIFrameElement;
    Object.defineProperty(iframe, "src", { value: "https://openclaw.local/other", writable: true });
    rerender(<BrowserApp initialUrl="https://openclaw.local/ui" />);
    expect(iframe.src).toBe("https://openclaw.local/other");
  });

  it("does not set iframe src when initialUrl is undefined", () => {
    const { container } = render(<BrowserApp />);
    const iframe = container.querySelector("iframe");
    expect(iframe?.src ?? "").not.toContain("https://");
  });
});
