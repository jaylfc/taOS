import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, fireEvent } from "@testing-library/react";
import { SafetyFloor } from "./SafetyFloor";
import { useTaosAgentStore } from "@/stores/taos-agent-store";
import { useThemeStore } from "@/stores/theme-store";

describe("SafetyFloor", () => {
  beforeEach(() => {
    useTaosAgentStore.setState({ isOpen: false });
    useThemeStore.setState({ structure: {} });
  });

  it("renders nothing when the top bar is visible", () => {
    useThemeStore.setState({ structure: { topBar: { variant: "standard" } } });
    const { container } = render(<SafetyFloor />);
    expect(container.innerHTML).toBe("");
  });

  it("renders the assistant button when the top bar is hidden", () => {
    useThemeStore.setState({ structure: { topBar: { variant: "hidden" } } });
    render(<SafetyFloor />);
    expect(screen.getByRole("button", { name: /taos assistant/i })).toBeInTheDocument();
  });

  it("calls openPanel when the button is clicked", () => {
    const openPanel = vi.fn();
    useTaosAgentStore.setState({ isOpen: false, openPanel });
    useThemeStore.setState({ structure: { topBar: { variant: "hidden" } } });
    render(<SafetyFloor />);
    fireEvent.click(screen.getByRole("button", { name: /taos assistant/i }));
    expect(openPanel).toHaveBeenCalledTimes(1);
  });
});
