import { describe, it, expect } from "vitest";
import { render, screen } from "@testing-library/react";
import { TypingFooter } from "../TypingFooter";

describe("TypingFooter", () => {
  it("renders nothing when empty", () => {
    const { container } = render(<TypingFooter humans={[]} agents={[]} />);
    expect(container.firstChild).toBeNull();
  });
  it("shows one human typing", () => {
    render(<TypingFooter humans={["alice"]} agents={[]} />);
    expect(screen.getByText("alice is typing…")).toBeInTheDocument();
  });
  it("shows N others when humans > 2", () => {
    render(<TypingFooter humans={["alice", "bob", "carol"]} agents={[]} />);
    expect(screen.getByText("alice and 2 others are typing…")).toBeInTheDocument();
  });
  it("filters self out of humans", () => {
    const { container } = render(<TypingFooter humans={["user"]} agents={[]} selfId="user" />);
    expect(container.firstChild).toBeNull();
  });
  it("shows default thinking label for agent with no phase", () => {
    render(<TypingFooter humans={[]} agents={[{ slug: "tom" }]} />);
    expect(screen.getByText(/tom/i)).toBeInTheDocument();
    expect(screen.getByText(/thinking/i)).toBeInTheDocument();
  });
  it("renders 'using X' for tool phase", () => {
    render(
      <TypingFooter
        humans={[]}
        agents={[{ slug: "tom", phase: "tool", detail: "web_search" }]}
        selfId="user"
      />,
    );
    expect(screen.getByText(/tom/i)).toBeInTheDocument();
    expect(screen.getByText(/using web_search/i)).toBeInTheDocument();
  });
  it("renders 'writing X' for writing phase", () => {
    render(
      <TypingFooter
        humans={[]}
        agents={[{ slug: "don", phase: "writing", detail: "payment.py" }]}
        selfId="user"
      />,
    );
    expect(screen.getByText(/writing payment\.py/i)).toBeInTheDocument();
  });
  it("truncates detail longer than 40 chars", () => {
    const longDetail = "a".repeat(60);
    render(
      <TypingFooter
        humans={[]}
        agents={[{ slug: "tom", phase: "tool", detail: longDetail }]}
        selfId="user"
      />,
    );
    const text = screen.getByText(/using/i).textContent ?? "";
    expect(text.length).toBeLessThanOrEqual(60);
    expect(text).toContain("…");
  });
  it("falls back to 'thinking' for unknown phase", () => {
    render(
      <TypingFooter
        humans={[]}
        agents={[{ slug: "tom", phase: "quantum-entanglement" as any, detail: null }]}
        selfId="user"
      />,
    );
    expect(screen.getByText(/thinking/i)).toBeInTheDocument();
  });
});
