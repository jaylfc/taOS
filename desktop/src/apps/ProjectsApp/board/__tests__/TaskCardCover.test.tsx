import { describe, it, expect } from "vitest";
import { render, screen } from "@testing-library/react";
import { TaskCardCover } from "../TaskCardCover";

describe("TaskCardCover", () => {
  it("renders nothing when kind is none", () => {
    const { container } = render(<TaskCardCover kind="none" />);
    expect(container.firstChild).toBeNull();
  });

  it("renders gradient variant", () => {
    render(<TaskCardCover kind="gradient" />);
    expect(screen.getByTestId("cover-gradient")).toBeInTheDocument();
  });

  it("renders code variant with the snippet", () => {
    render(<TaskCardCover kind="code" data={{ snippet: "let x = 1;", language: "ts" }} />);
    expect(screen.getByTestId("cover-code").textContent).toContain("let x = 1;");
  });

  it("renders terminal variant with lines", () => {
    render(<TaskCardCover kind="terminal" data={{ lines: ["WARN slow", "INFO ok"] }} />);
    expect(screen.getByTestId("cover-terminal").textContent).toContain("WARN slow");
  });

  it("renders screenshot variant placeholder", () => {
    render(<TaskCardCover kind="screenshot" />);
    expect(screen.getByTestId("cover-screenshot")).toBeInTheDocument();
  });
});
