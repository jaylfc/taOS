import { describe, it, expect, vi } from "vitest";
import { render, fireEvent, screen } from "@testing-library/react";
import { Button } from "../button";

describe("Button", () => {
  it("renders with base classes and text content", () => {
    const { container } = render(
      <Button data-testid="button">Click me</Button>
    );
    const button = container.firstChild as HTMLElement;
    expect(button).toHaveClass(
      "inline-flex", "items-center", "justify-center", "gap-2", "whitespace-nowrap", "rounded-lg", "text-sm", "font-medium", "transition-all", "disabled:pointer-events-none", "disabled:opacity-50", "focus-visible:outline-none", "focus-visible:ring-2", "focus-visible:ring-accent/40"
    );
    expect(button).toHaveAttribute("data-testid", "button");
    expect(button).toHaveTextContent("Click me");
  });

  it("renders with variant default", () => {
    const { container } = render(
      <Button data-testid="button" variant="default">Default</Button>
    );
    const button = container.firstChild as HTMLElement;
    expect(button).toHaveClass("bg-accent", "text-white", "hover:brightness-110", "shadow-sm");
  });

  it("renders with variant destructive", () => {
    const { container } = render(
      <Button data-testid="button" variant="destructive">Delete</Button>
    );
    const button = container.firstChild as HTMLElement;
    expect(button).toHaveClass("bg-red-500/15", "text-red-400", "hover:bg-red-500/25", "border", "border-red-500/25");
  });

  it("renders with variant outline", () => {
    const { container } = render(
      <Button data-testid="button" variant="outline">Outline</Button>
    );
    const button = container.firstChild as HTMLElement;
    expect(button).toHaveClass("border", "border-white/10", "bg-white/[0.04]", "text-shell-text", "hover:bg-white/[0.08]");
  });

  it("renders with variant secondary", () => {
    const { container } = render(
      <Button data-testid="button" variant="secondary">Secondary</Button>
    );
    const button = container.firstChild as HTMLElement;
    expect(button).toHaveClass("bg-white/[0.06]", "text-shell-text", "hover:bg-white/[0.1]");
  });

  it("renders with variant ghost", () => {
    const { container } = render(
      <Button data-testid="button" variant="ghost">Ghost</Button>
    );
    const button = container.firstChild as HTMLElement;
    expect(button).toHaveClass("text-shell-text-secondary", "hover:bg-white/[0.06]", "hover:text-shell-text");
  });

  it("renders with variant link", () => {
    const { container } = render(
      <Button data-testid="button" variant="link">Link</Button>
    );
    const button = container.firstChild as HTMLElement;
    expect(button).toHaveClass("text-accent", "underline-offset-4", "hover:underline");
  });

  it("renders with size sm", () => {
    const { container } = render(
      <Button data-testid="button" size="sm">Small</Button>
    );
    const button = container.firstChild as HTMLElement;
    expect(button).toHaveClass("h-8", "px-3", "text-xs");
  });

  it("renders with size lg", () => {
    const { container } = render(
      <Button data-testid="button" size="lg">Large</Button>
    );
    const button = container.firstChild as HTMLElement;
    expect(button).toHaveClass("h-11", "px-6");
  });

  it("renders with size icon", () => {
    const { container } = render(
      <Button data-testid="button" size="icon">Icon</Button>
    );
    const button = container.firstChild as HTMLElement;
    expect(button).toHaveClass("h-9", "w-9");
  });

  it("renders with disabled state", () => {
    const { container } = render(
      <Button data-testid="button" disabled>Disabled</Button>
    );
    const button = container.firstChild as HTMLElement;
    expect(button).toHaveClass("disabled:pointer-events-none", "disabled:opacity-50");
    expect(button).toBeDisabled();
  });

  it("fires onClick with correct args", () => {
    const handleClick = vi.fn();
    const { container } = render(
      <Button data-testid="button" onClick={handleClick}>Click</Button>
    );
    const button = container.firstChild as HTMLElement;
    fireEvent.click(button);
    expect(handleClick).toHaveBeenCalledTimes(1);
  });

  it("forwards ref", () => {
    const ref = { current: null };
    render(<Button ref={ref} />);
    expect(ref.current).not.toBeNull();
  });

  it("forwards arbitrary props", () => {
    const { container } = render(
      <Button data-testid="button" role="button" aria-label="test button" type="submit">Test</Button>
    );
    const button = container.firstChild as HTMLElement;
    expect(button).toHaveAttribute("role", "button");
    expect(button).toHaveAttribute("aria-label", "test button");
    expect(button).toHaveAttribute("type", "submit");
  });

  it("handles empty children", () => {
    const { container } = render(<Button data-testid="button" />);
    const button = container.firstChild as HTMLElement;
    expect(button).toBeInTheDocument();
  });

  it("applies custom className", () => {
    const { container } = render(
      <Button data-testid="button" className="custom-class">Test</Button>
    );
    const button = container.firstChild as HTMLElement;
    expect(button).toHaveClass("custom-class");
  });

  it("renders with asChild true", () => {
    const { container } = render(
      <Button data-testid="button" asChild>
        <a href="/test">Link</a>
      </Button>
    );
    const link = container.firstChild as HTMLElement;
    expect(link).toBeInTheDocument();
    expect(link.tagName).toBe("A");
    expect(link).toHaveAttribute("href", "/test");
  });
});