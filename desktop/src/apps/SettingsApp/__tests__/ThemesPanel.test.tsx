import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, fireEvent } from "@testing-library/react";
import { ThemesPanel } from "../ThemesPanel";

beforeEach(() => {
  document.documentElement.removeAttribute("style");
  vi.stubGlobal("fetch", vi.fn().mockResolvedValue({ ok: true, json: async () => [
    { theme_id: "ocean", name: "Ocean Blue", config: { tokens: { "--color-accent": "#00aaff" }, structure: {}, effects: [], requires: [] } },
  ] }));
});

describe("ThemesPanel", () => {
  it("lists installed themes and previews on select with a Keep/Revert bar", async () => {
    render(<ThemesPanel />);
    expect(await screen.findByText("Ocean Blue")).toBeInTheDocument();
    fireEvent.click(screen.getByText("Ocean Blue"));
    expect(document.documentElement.style.getPropertyValue("--color-accent")).toBe("#00aaff");
    expect(screen.getByRole("button", { name: /keep/i })).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: /revert/i }));
    expect(document.documentElement.style.getPropertyValue("--color-accent")).toBe("");
  });

  it("shows built-in taOS Dark and taOS Light even when server returns empty list", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue({ ok: true, json: async () => [] }));
    render(<ThemesPanel />);
    expect(await screen.findByText("taOS Dark")).toBeInTheDocument();
    expect(screen.getByText("taOS Light")).toBeInTheDocument();
  });
});
