import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, fireEvent } from "@testing-library/react";
import { ThemesPanel } from "../ThemesPanel";

beforeEach(() => {
  document.documentElement.removeAttribute("style");
  vi.stubGlobal("fetch", vi.fn().mockResolvedValue({ ok: true, json: async () => [
    { theme_id: "default", name: "Default", config: { tokens: {}, structure: {}, effects: [], requires: [] } },
    { theme_id: "matrix", name: "Matrix Terminal", config: { tokens: { "--color-accent": "#00ff46" }, structure: {}, effects: [], requires: ["assistant","launcher"] } },
  ] }));
});

describe("ThemesPanel", () => {
  it("lists installed themes and previews on select with a Keep/Revert bar", async () => {
    render(<ThemesPanel />);
    expect(await screen.findByText("Matrix Terminal")).toBeInTheDocument();
    fireEvent.click(screen.getByText("Matrix Terminal"));
    expect(document.documentElement.style.getPropertyValue("--color-accent")).toBe("#00ff46");
    expect(screen.getByRole("button", { name: /keep/i })).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: /revert/i }));
    expect(document.documentElement.style.getPropertyValue("--color-accent")).toBe("");
  });
});
