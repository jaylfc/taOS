import { describe, expect, it, vi } from "vitest";
import { render, screen, fireEvent } from "@testing-library/react";
import { CalculatorApp } from "./CalculatorApp";

vi.mock("@/components/ui", () => ({
  Button: ({
    children,
    onClick,
    "aria-label": ariaLabel,
  }: {
    children: React.ReactNode;
    onClick?: () => void;
    "aria-label"?: string;
  }) => (
    <button onClick={onClick} aria-label={ariaLabel}>
      {children}
    </button>
  ),
}));

describe("CalculatorApp", () => {
  it("renders initial display with 0", () => {
    render(<CalculatorApp windowId="win-1" />);
    expect(screen.getByLabelText("Expression").textContent).toBe("0");
  });

  it("updates display when digits are entered", () => {
    render(<CalculatorApp windowId="win-1" />);
    fireEvent.click(screen.getByRole("button", { name: "7" }));
    fireEvent.click(screen.getByRole("button", { name: "7" }));
    expect(screen.getByLabelText("Expression").textContent).toBe("77");
  });

  it("performs addition and shows result", () => {
    render(<CalculatorApp windowId="win-1" />);
    fireEvent.click(screen.getByRole("button", { name: "2" }));
    fireEvent.click(screen.getByRole("button", { name: "+" }));
    fireEvent.click(screen.getByRole("button", { name: "3" }));
    fireEvent.click(screen.getByRole("button", { name: "=" }));
    expect(screen.getByLabelText("Result").textContent).toBe("5");
  });

  it("clears display with C", () => {
    render(<CalculatorApp windowId="win-1" />);
    fireEvent.click(screen.getByRole("button", { name: "9" }));
    fireEvent.click(screen.getByRole("button", { name: "C" }));
    expect(screen.getByLabelText("Expression").textContent).toBe("0");
    expect(screen.getByLabelText("Result").textContent).toBe("");
  });

  it("evaluates multiplication correctly", () => {
    render(<CalculatorApp windowId="win-1" />);
    fireEvent.click(screen.getByRole("button", { name: "6" }));
    fireEvent.click(screen.getByRole("button", { name: "\u00d7" }));
    fireEvent.click(screen.getByRole("button", { name: "7" }));
    fireEvent.click(screen.getByRole("button", { name: "=" }));
    expect(screen.getByLabelText("Result").textContent).toBe("42");
  });

  it("handles decimal input", () => {
    render(<CalculatorApp windowId="win-1" />);
    fireEvent.click(screen.getByRole("button", { name: "." }));
    fireEvent.click(screen.getByRole("button", { name: "5" }));
    expect(screen.getByLabelText("Expression").textContent).toBe("0.5");
  });

  it("performs percentage operation", () => {
    render(<CalculatorApp windowId="win-1" />);
    fireEvent.click(screen.getByRole("button", { name: "5" }));
    fireEvent.click(screen.getByRole("button", { name: "0" }));
    fireEvent.click(screen.getByRole("button", { name: "%" }));
    expect(screen.getByLabelText("Expression").textContent).toBe("0.5");
  });

  it("handles backspace to delete last character", () => {
    render(<CalculatorApp windowId="win-1" />);
    fireEvent.click(screen.getByRole("button", { name: "1" }));
    fireEvent.click(screen.getByRole("button", { name: "2" }));
    fireEvent.click(screen.getByRole("button", { name: "\u232b" }));
    expect(screen.getByLabelText("Expression").textContent).toBe("1");
  });
});
