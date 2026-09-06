import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, fireEvent, act } from "@testing-library/react";
import { InstallPromptBanner } from "../InstallPromptBanner";

const fireBeforeInstallPrompt = (prompt: () => Promise<{outcome: string}>) => {
  const event = new Event("beforeinstallprompt") as Event & {
    prompt: () => Promise<{outcome: string}>;
    userChoice: Promise<{outcome: string}>;
  };
  // @ts-expect-error test wiring
  event.prompt = prompt;
  // @ts-expect-error test wiring
  event.userChoice = Promise.resolve({ outcome: "accepted" });
  window.dispatchEvent(event);
};

describe("InstallPromptBanner", () => {
  beforeEach(() => {
    localStorage.clear();
    Object.defineProperty(window, "matchMedia", {
      value: (q: string) => ({
        matches: q.includes("max-width") ? true : false,
        addEventListener: () => {},
        removeEventListener: () => {},
      }),
      configurable: true,
    });
    Object.defineProperty(window, "innerWidth", { value: 400, configurable: true });
  });

  it("renders nothing until beforeinstallprompt fires", () => {
    const { container } = render(<InstallPromptBanner />);
    expect(container.firstChild).toBeNull();
  });

  it("renders after beforeinstallprompt and install click calls prompt()", async () => {
    const promptSpy = vi.fn(() => Promise.resolve({ outcome: "accepted" }));
    render(<InstallPromptBanner />);
    await act(async () => {
      fireBeforeInstallPrompt(promptSpy);
    });
    expect(screen.getByRole("region", { name: /install/i })).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: /^install$/i }));
    expect(promptSpy).toHaveBeenCalled();
  });

  it("Not now click writes dismissal timestamp and hides", async () => {
    render(<InstallPromptBanner />);
    await act(async () => {
      fireBeforeInstallPrompt(vi.fn(() => Promise.resolve({ outcome: "accepted" })));
    });
    fireEvent.click(screen.getByRole("button", { name: /not now/i }));
    expect(localStorage.getItem("taos-install-dismissed")).not.toBeNull();
    expect(screen.queryByRole("region", { name: /install/i })).toBeNull();
  });

  it("stays hidden when recently dismissed", async () => {
    localStorage.setItem("taos-install-dismissed", String(Date.now()));
    render(<InstallPromptBanner />);
    await act(async () => {
      fireBeforeInstallPrompt(vi.fn(() => Promise.resolve({ outcome: "accepted" })));
    });
    expect(screen.queryByRole("region", { name: /install/i })).toBeNull();
  });

  it("reappears after 30 days", async () => {
    const thirtyOneDaysAgo = Date.now() - 31 * 24 * 60 * 60 * 1000;
    localStorage.setItem("taos-install-dismissed", String(thirtyOneDaysAgo));
    render(<InstallPromptBanner />);
    await act(async () => {
      fireBeforeInstallPrompt(vi.fn(() => Promise.resolve({ outcome: "accepted" })));
    });
    expect(screen.getByRole("region", { name: /install/i })).toBeInTheDocument();
  });
});
