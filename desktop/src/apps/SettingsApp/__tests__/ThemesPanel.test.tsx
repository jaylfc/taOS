import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, fireEvent } from "@testing-library/react";
import { ThemesPanel } from "../ThemesPanel";
import { useThemeStore } from "@/stores/theme-store";

beforeEach(() => {
  document.documentElement.removeAttribute("style");
  vi.stubGlobal("fetch", vi.fn().mockResolvedValue({ ok: true, json: async () => [
    { theme_id: "ocean", name: "Ocean Blue", config: { tokens: { "--color-accent": "#00aaff" }, structure: {}, effects: [], requires: [] } },
  ] }));
  useThemeStore.setState({ appTypeIndicator: "badge" });
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

  it("announces a themes-fetch failure via role=alert", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue({ ok: false, status: 500, statusText: "Internal Server Error" }));
    render(<ThemesPanel />);
    expect(await screen.findByRole("alert")).toBeInTheDocument();
  });

  it("does not render an alert when themes load successfully", async () => {
    render(<ThemesPanel />);
    await screen.findByText("Ocean Blue");
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
  });

  it("has app type indicators radiogroup with three options", async () => {
    render(<ThemesPanel />);
    await screen.findByText("Ocean Blue");
    const radiogroup = screen.getByRole("radiogroup", { name: /app type indicators/i });
    expect(radiogroup).toBeInTheDocument();
    const badgesRadio = screen.getByRole("radio", { name: /badges/i });
    const outlinesRadio = screen.getByRole("radio", { name: /outlines/i });
    const offRadio = screen.getByRole("radio", { name: /off/i });
    expect(badgesRadio).toBeInTheDocument();
    expect(outlinesRadio).toBeInTheDocument();
    expect(offRadio).toBeInTheDocument();
    expect(badgesRadio).toHaveAttribute("aria-checked", "true");
    fireEvent.click(outlinesRadio);
    expect(useThemeStore.getState().appTypeIndicator).toBe("outline");
    expect(outlinesRadio).toHaveAttribute("aria-checked", "true");
  });
});
