import { render, screen } from "@testing-library/react";
import { describe, it, expect, vi, afterEach } from "vitest";
import BrowserApp from "./BrowserApp";

describe("BrowserApp initialUrl", () => {
  afterEach(() => vi.clearAllMocks());

  it("sets iframe src via proxy URL for initialUrl on first mount", () => {
    const { container } = render(
      <BrowserApp initialUrl="https://openclaw.local/ui" />,
    );
    const iframe = container.querySelector("iframe");
    expect(iframe).not.toBeNull();
    // The iframe always loads through the desktop proxy endpoint.
    // Verify the initialUrl is encoded in the proxy src.
    expect(iframe?.src).toContain("openclaw.local%2Fui");
  });

  it("rejects javascript: initialUrl and does not set iframe src", () => {
    const warnSpy = vi.spyOn(console, "warn").mockImplementation(() => {});
    const { container } = render(
      <BrowserApp initialUrl="javascript:alert(1)" />,
    );
    const iframe = container.querySelector("iframe");
    // iframe src must NOT contain the dangerous payload
    expect(iframe?.src ?? "").not.toContain("javascript");
    expect(iframe?.src ?? "").not.toContain("alert");
    // a warning must have been emitted
    expect(warnSpy).toHaveBeenCalledWith(
      expect.stringContaining("[BrowserApp]"),
      expect.anything(),
    );
    warnSpy.mockRestore();
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

  it("shows initialUrl in the address bar input, not DEFAULT_URL", () => {
    render(<BrowserApp initialUrl="https://openclaw.local/dashboard" />);
    const input = screen.getByRole<HTMLInputElement>("textbox");
    expect(input.value).toBe("https://openclaw.local/dashboard");
  });
});
