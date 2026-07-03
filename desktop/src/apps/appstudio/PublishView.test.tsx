import { describe, it, expect, afterEach, vi } from "vitest";
import { render, screen, cleanup, waitFor } from "@testing-library/react";
import { PublishView } from "./PublishView";

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

describe("PublishView security scan wiring", () => {
  it("calls the analyze endpoint on mount and shows a clean state when no findings", async () => {
    const fetchMock = vi.fn(async () => ({
      ok: true,
      json: async () => ({ findings: [], blocked: false }),
      text: async () => "",
    }));
    vi.stubGlobal("fetch", fetchMock);

    render(<PublishView />);

    await waitFor(() => {
      expect(screen.getByTestId("security-findings-clean")).toBeInTheDocument();
    });
    expect(fetchMock).toHaveBeenCalledWith(
      "/api/userspace-apps/analyze",
      expect.objectContaining({ method: "POST" }),
    );
    const publishButton = screen.getByRole("button", { name: /publish to my store/i });
    expect(publishButton).not.toBeDisabled();
  });

  it("blocks the publish button when the scan returns a critical finding", async () => {
    const fetchMock = vi.fn(async () => ({
      ok: true,
      json: async () => ({
        findings: [
          {
            severity: "critical",
            rule_id: "eval-like-execution",
            file: "app.js",
            line: 5,
            message: "Use of eval() executes arbitrary strings as code.",
          },
        ],
        blocked: true,
      }),
      text: async () => "",
    }));
    vi.stubGlobal("fetch", fetchMock);

    render(<PublishView />);

    await waitFor(() => {
      expect(screen.getByTestId("security-findings-blocked")).toBeInTheDocument();
    });
    const publishButton = screen.getByRole("button", { name: /blocked by security scan/i });
    expect(publishButton).toBeDisabled();
  });

  it("allows publishing when only warnings are returned", async () => {
    const fetchMock = vi.fn(async () => ({
      ok: true,
      json: async () => ({
        findings: [
          {
            severity: "warning",
            rule_id: "some-warning-rule",
            file: "app.js",
            line: 2,
            message: "A non-blocking warning.",
          },
        ],
        blocked: false,
      }),
      text: async () => "",
    }));
    vi.stubGlobal("fetch", fetchMock);

    render(<PublishView />);

    await waitFor(() => {
      expect(screen.getByTestId("finding-warning")).toBeInTheDocument();
    });
    const publishButton = screen.getByRole("button", { name: /publish to my store/i });
    expect(publishButton).not.toBeDisabled();
  });

  it("shows a scan error state when the analyze request fails", async () => {
    const fetchMock = vi.fn(async () => ({
      ok: false,
      status: 500,
      json: async () => ({}),
      text: async () => "internal error",
    }));
    vi.stubGlobal("fetch", fetchMock);

    render(<PublishView />);

    await waitFor(() => {
      expect(screen.getByTestId("security-findings-error")).toBeInTheDocument();
    });
  });
});
