import { describe, it, expect, afterEach, vi } from "vitest";
import { render, screen } from "@testing-library/react";
import { PublishView } from "../PublishView";
import { setBuildSession } from "../build-state";

afterEach(() => {
  vi.unstubAllGlobals();
  setBuildSession(null);
});

describe("PublishView -- provenance badge", () => {
  it("shows an AI-generated provenance badge next to the app name once an app has been built", () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(() => Promise.resolve({ ok: true, json: () => Promise.resolve({ findings: [], blocked: false }) })) as unknown as typeof fetch,
    );
    setBuildSession({ name: "Chore Tracker", appId: "chore-tracker-ab12", files: { "index.html": "<html></html>" } });
    render(<PublishView />);
    const badge = screen.getByTestId("provenance-badge");
    expect(badge.getAttribute("data-provenance")).toBe("ai-generated");
    expect(badge.textContent).toContain("AI-generated");
  });
});
