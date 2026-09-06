import { describe, it, expect, vi } from "vitest";
import { render, screen, fireEvent } from "@testing-library/react";
import { SlashMenu } from "../SlashMenu";

const commands = {
  tom: [{ name: "help", description: "Show Hermes help" }, { name: "clear", description: "Clear context" }],
  don: [{ name: "help", description: "SmolAgents help" }],
};

describe("SlashMenu", () => {
  it("renders header + commands grouped per agent in a group channel", () => {
    render(<SlashMenu commands={commands} queryAfterSlash="" members={["user", "tom", "don"]}
             onPick={vi.fn()} onClose={vi.fn()} />);
    expect(screen.getByText("@tom")).toBeInTheDocument();
    expect(screen.getByText("@don")).toBeInTheDocument();
    expect(screen.getAllByText("/help").length).toBe(2);
    expect(screen.getByText("/clear")).toBeInTheDocument();
  });

  it("drops the header in a DM", () => {
    render(<SlashMenu commands={commands} queryAfterSlash="" members={["user", "tom"]}
             onPick={vi.fn()} onClose={vi.fn()} />);
    expect(screen.queryByText("@tom")).not.toBeInTheDocument();
    expect(screen.getByText("/help")).toBeInTheDocument();
    expect(screen.getByText("/clear")).toBeInTheDocument();
  });

  it("fuzzy filters across slug + command", () => {
    render(<SlashMenu commands={commands} queryAfterSlash="tomc" members={["user", "tom", "don"]}
             onPick={vi.fn()} onClose={vi.fn()} />);
    expect(screen.getByText("/clear")).toBeInTheDocument();
    expect(screen.queryByText("/help")).not.toBeInTheDocument();
  });

  it("Enter invokes onPick with the current selection", () => {
    const onPick = vi.fn();
    render(<SlashMenu commands={commands} queryAfterSlash="" members={["user", "tom", "don"]}
             onPick={onPick} onClose={vi.fn()} />);
    fireEvent.keyDown(document, { key: "Enter" });
    expect(onPick).toHaveBeenCalledWith("tom", "help");
  });

  it("Escape calls onClose", () => {
    const onClose = vi.fn();
    render(<SlashMenu commands={commands} queryAfterSlash="" members={["user", "tom"]}
             onPick={vi.fn()} onClose={onClose} />);
    fireEvent.keyDown(document, { key: "Escape" });
    expect(onClose).toHaveBeenCalled();
  });

  it("scopes to a single agent in a group channel via scopedAgent", () => {
    render(<SlashMenu commands={commands} queryAfterSlash="" members={["user", "tom", "don"]}
             scopedAgent="tom" onPick={vi.fn()} onClose={vi.fn()} />);
    // Only tom's commands are shown, with the header.
    expect(screen.getByText("@tom")).toBeInTheDocument();
    expect(screen.getByText("/help")).toBeInTheDocument();
    expect(screen.getByText("/clear")).toBeInTheDocument();
    // don's commands are excluded.
    expect(screen.queryByText("@don")).not.toBeInTheDocument();
  });
});
