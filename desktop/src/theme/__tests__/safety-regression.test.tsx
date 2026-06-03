// desktop/src/theme/__tests__/safety-regression.test.tsx
import { describe, it, expect, beforeEach, afterEach } from "vitest";
import { render, screen, cleanup } from "@testing-library/react";
import { applyThemeConfig, revertTheme } from "@/stores/theme-store";
import { SafetyFloor } from "@/components/SafetyFloor";

describe("safety floor", () => {
  beforeEach(() => revertTheme());
  afterEach(() => cleanup());

  it("is hidden on the default theme so it does not duplicate the top-bar button", () => {
    // Default theme: structure {} — the standard top-bar assistant button is present.
    render(<SafetyFloor />);
    expect(
      screen.queryByRole("button", { name: /assistant/i }),
    ).not.toBeInTheDocument();
  });

  it("assistant button still present after applying a theme that hides everything", () => {
    applyThemeConfig({ tokens: {}, structure: { dock: { variant: "hidden" }, topBar: { variant: "hidden" } }, effects: [], requires: [] });
    render(<SafetyFloor />);
    expect(screen.getByRole("button", { name: /assistant/i })).toBeInTheDocument();
  });
});
