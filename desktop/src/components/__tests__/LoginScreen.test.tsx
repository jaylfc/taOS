import { describe, it, expect, vi } from "vitest";
import { render, screen, fireEvent } from "@testing-library/react";
import { LoginScreen } from "../LoginScreen";

describe("<LoginScreen />", () => {
  it("renders with minimal valid props and shows the Launch button", () => {
    const onLaunch = vi.fn();
    render(<LoginScreen onLaunch={onLaunch} />);
    expect(screen.getByRole("button", { name: /Launch taOS/i })).toBeInTheDocument();
    expect(screen.getByAltText("taOS")).toBeInTheDocument();
  });

  it("calls onLaunch after clicking the Launch button", () => {
    vi.useFakeTimers();
    const onLaunch = vi.fn();
    render(<LoginScreen onLaunch={onLaunch} />);
    fireEvent.click(screen.getByRole("button", { name: /Launch taOS/i }));
    expect(onLaunch).not.toHaveBeenCalled();
    vi.advanceTimersByTime(600);
    expect(onLaunch).toHaveBeenCalled();
    vi.useRealTimers();
  });
});
