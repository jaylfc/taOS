import { describe, it, expect } from "vitest";
import { render, screen } from "@testing-library/react";
import { PublishView } from "../PublishView";

describe("PublishView -- provenance badge", () => {
  it("shows an AI-generated provenance badge next to the app name", () => {
    render(<PublishView />);
    const badge = screen.getByTestId("provenance-badge");
    expect(badge.getAttribute("data-provenance")).toBe("ai-generated");
    expect(badge.textContent).toContain("AI-generated");
  });
});
