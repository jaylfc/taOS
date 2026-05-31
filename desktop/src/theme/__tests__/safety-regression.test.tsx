// desktop/src/theme/__tests__/safety-regression.test.tsx
import { describe, it, expect } from "vitest";
import { render, screen } from "@testing-library/react";
import { applyThemeConfig } from "@/stores/theme-store";
import { SafetyFloor } from "@/components/SafetyFloor";

describe("safety floor survives a hostile theme", () => {
  it("assistant button still present after applying a theme that hides everything", () => {
    applyThemeConfig({ tokens: {}, structure: { dock: { variant: "hidden" }, topBar: { variant: "hidden" } }, effects: [], requires: [] });
    render(<SafetyFloor />);
    expect(screen.getByRole("button", { name: /assistant/i })).toBeInTheDocument();
  });
});
