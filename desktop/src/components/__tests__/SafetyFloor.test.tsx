import { describe, it, expect } from "vitest";
import { render, screen } from "@testing-library/react";
import { SafetyFloor } from "../SafetyFloor";
import { useThemeStore } from "@/stores/theme-store";

describe("SafetyFloor", () => {
  it("always renders an interactive assistant button regardless of theme", () => {
    useThemeStore.setState({ structure: { topBar: { variant: "hidden" }, dock: { variant: "hidden" } } } as never);
    render(<SafetyFloor />);
    const btn = screen.getByRole("button", { name: /assistant/i });
    expect(btn).toBeInTheDocument();
    expect((btn.parentElement as HTMLElement).style.pointerEvents).not.toBe("none");
  });
});
