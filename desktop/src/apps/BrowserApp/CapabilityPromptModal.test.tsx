import { describe, it, expect, beforeEach, vi, afterEach } from "vitest";
import { render, screen, fireEvent, waitFor, act } from "@testing-library/react";
import { CapabilityPromptModal } from "./CapabilityPromptModal";
import * as capabilityApi from "@/lib/browser-capability-api";

function dispatchPromptEvent(detail: object) {
  window.dispatchEvent(
    new CustomEvent("taos-browser:capability-prompt", { detail }),
  );
}

const BASE_DETAIL = {
  profileId: "profile-1",
  agentId: "agent-42",
  agentName: "TestAgent",
  permission: "drive",
  host: "example.com",
  fullUrl: "https://example.com/page",
};

beforeEach(() => {
  vi.spyOn(capabilityApi, "grantCapability").mockResolvedValue({ granted: true });
});

afterEach(() => {
  vi.restoreAllMocks();
});

describe("CapabilityPromptModal", () => {
  it("renders nothing when no event has fired", () => {
    render(<CapabilityPromptModal />);
    expect(screen.queryByRole("dialog")).toBeNull();
  });

  it("renders modal when taos-browser:capability-prompt event dispatched", async () => {
    render(<CapabilityPromptModal />);
    act(() => { dispatchPromptEvent(BASE_DETAIL); });
    await waitFor(() => {
      expect(screen.getByRole("dialog")).toBeTruthy();
    });
  });

  it("shows the agent name + permission + host", async () => {
    render(<CapabilityPromptModal />);
    act(() => { dispatchPromptEvent(BASE_DETAIL); });
    await waitFor(() => {
      const dialog = screen.getByRole("dialog");
      expect(dialog.textContent).toContain("TestAgent");
      expect(dialog.textContent).toContain("drive");
      expect(dialog.textContent).toContain("example.com");
    });
  });

  it("'This page only' calls grantCapability with full URL + ~1h expiry", async () => {
    render(<CapabilityPromptModal />);
    act(() => { dispatchPromptEvent(BASE_DETAIL); });
    await waitFor(() => { expect(screen.getByRole("dialog")).toBeTruthy(); });

    const expectedMs = Date.now() + 60 * 60 * 1000;
    fireEvent.click(screen.getByText(/This page only/));
    await waitFor(() => {
      expect(capabilityApi.grantCapability).toHaveBeenCalledWith(
        "profile-1",
        "agent-42",
        "https://example.com/page",
        "drive",
        expect.any(String),
      );
      const calls = (capabilityApi.grantCapability as ReturnType<typeof vi.fn>).mock.calls;
      const expiresAt = calls[0][4] as string;
      const actualMs = new Date(expiresAt).getTime();
      expect(Math.abs(actualMs - expectedMs)).toBeLessThan(5000);
    });
  });

  it("'This site (this session)' calls grantCapability with host + ~24h expiry", async () => {
    render(<CapabilityPromptModal />);
    act(() => { dispatchPromptEvent(BASE_DETAIL); });
    await waitFor(() => { expect(screen.getByRole("dialog")).toBeTruthy(); });

    const expectedMs = Date.now() + 24 * 60 * 60 * 1000;
    fireEvent.click(screen.getByText(/This site \(this session\)/));
    await waitFor(() => {
      expect(capabilityApi.grantCapability).toHaveBeenCalledWith(
        "profile-1",
        "agent-42",
        "example.com",
        "drive",
        expect.any(String),
      );
      const calls = (capabilityApi.grantCapability as ReturnType<typeof vi.fn>).mock.calls;
      const expiresAt = calls[0][4] as string;
      const actualMs = new Date(expiresAt).getTime();
      expect(Math.abs(actualMs - expectedMs)).toBeLessThan(5000);
    });
  });

  it("'This site (always)' calls grantCapability with host + null expiry", async () => {
    render(<CapabilityPromptModal />);
    act(() => { dispatchPromptEvent(BASE_DETAIL); });
    await waitFor(() => { expect(screen.getByRole("dialog")).toBeTruthy(); });

    fireEvent.click(screen.getByText(/This site \(always\)/));
    await waitFor(() => {
      expect(capabilityApi.grantCapability).toHaveBeenCalledWith(
        "profile-1",
        "agent-42",
        "example.com",
        "drive",
        null,
      );
    });
  });

  it("'Deny' closes without calling grantCapability", async () => {
    render(<CapabilityPromptModal />);
    act(() => { dispatchPromptEvent(BASE_DETAIL); });
    await waitFor(() => { expect(screen.getByRole("dialog")).toBeTruthy(); });

    fireEvent.click(screen.getByText(/^Deny/));
    await waitFor(() => {
      expect(screen.queryByRole("dialog")).toBeNull();
    });
    expect(capabilityApi.grantCapability).not.toHaveBeenCalled();
  });

  it("Esc closes without granting", async () => {
    render(<CapabilityPromptModal />);
    act(() => { dispatchPromptEvent(BASE_DETAIL); });
    await waitFor(() => { expect(screen.getByRole("dialog")).toBeTruthy(); });

    fireEvent.keyDown(window, { key: "Escape" });
    await waitFor(() => {
      expect(screen.queryByRole("dialog")).toBeNull();
    });
    expect(capabilityApi.grantCapability).not.toHaveBeenCalled();
  });

  it("click-outside backdrop closes without granting", async () => {
    render(<CapabilityPromptModal />);
    act(() => { dispatchPromptEvent(BASE_DETAIL); });
    await waitFor(() => { expect(screen.getByRole("dialog")).toBeTruthy(); });

    // Click on the backdrop (the dialog element itself, not its inner content)
    const backdrop = screen.getByRole("dialog");
    fireEvent.click(backdrop, { target: backdrop });
    await waitFor(() => {
      expect(screen.queryByRole("dialog")).toBeNull();
    });
    expect(capabilityApi.grantCapability).not.toHaveBeenCalled();
  });

  it("error from grantCapability shows inline; modal stays open", async () => {
    vi.spyOn(capabilityApi, "grantCapability").mockResolvedValue({
      error: "host pattern rejected",
    });
    render(<CapabilityPromptModal />);
    act(() => { dispatchPromptEvent(BASE_DETAIL); });
    await waitFor(() => { expect(screen.getByRole("dialog")).toBeTruthy(); });

    fireEvent.click(screen.getByText(/This page only/));
    await waitFor(() => {
      expect(screen.getByRole("alert")).toBeTruthy();
      expect(screen.getByText("host pattern rejected")).toBeTruthy();
      expect(screen.getByRole("dialog")).toBeTruthy();
    });
  });

  it("grant success closes the modal", async () => {
    render(<CapabilityPromptModal />);
    act(() => { dispatchPromptEvent(BASE_DETAIL); });
    await waitFor(() => { expect(screen.getByRole("dialog")).toBeTruthy(); });

    fireEvent.click(screen.getByText(/This site \(always\)/));
    await waitFor(() => {
      expect(screen.queryByRole("dialog")).toBeNull();
    });
  });

  it("aria-modal + role=dialog present", async () => {
    render(<CapabilityPromptModal />);
    act(() => { dispatchPromptEvent(BASE_DETAIL); });
    await waitFor(() => {
      const dialog = screen.getByRole("dialog");
      expect(dialog.getAttribute("aria-modal")).toBe("true");
    });
  });
});
