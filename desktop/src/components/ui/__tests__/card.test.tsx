import { describe, it, expect, vi } from "vitest";
import { render, fireEvent, screen } from "@testing-library/react";
import { Card, CardHeader, CardTitle, CardDescription, CardContent, CardFooter } from "../card";

describe("Card", () => {
  it("renders with base classes and children", () => {
    const { container } = render(
      <Card data-testid="card">
        <div>child</div>
      </Card>
    );
    const card = container.firstChild as HTMLElement;
    expect(card).toHaveClass("rounded-xl", "border", "border-white/[0.06]", "bg-white/[0.04]", "backdrop-blur-sm", "shadow-sm");
    expect(card).toHaveAttribute("data-testid", "card");
    expect(card).toHaveTextContent("child");
  });

  it("renders with additional className", () => {
    const { container } = render(
      <Card className="custom-class">
        <div>child</div>
      </Card>
    );
    const card = container.firstChild as HTMLElement;
    expect(card).toHaveClass("custom-class");
  });

  it("forwards ref", () => {
    const ref = { current: null };
    render(<Card ref={ref} />);
    expect(ref.current).not.toBeNull();
  });

  it("fires onClick with correct args", () => {
    const handleClick = vi.fn();
    const { container } = render(
      <Card data-testid="card" onClick={handleClick}>
        <div>child</div>
      </Card>
    );
    const card = container.firstChild as HTMLElement;
    fireEvent.click(card);
    expect(handleClick).toHaveBeenCalledTimes(1);
  });

  it("forwards arbitrary props", () => {
    const { container } = render(
      <Card data-testid="card" role="region" aria-label="test card">
        <div>child</div>
      </Card>
    );
    const card = container.firstChild as HTMLElement;
    expect(card).toHaveAttribute("role", "region");
    expect(card).toHaveAttribute("aria-label", "test card");
  });

  it("handles empty children", () => {
    const { container } = render(<Card data-testid="card" />);
    const card = container.firstChild as HTMLElement;
    expect(card).toBeInTheDocument();
  });

  it("applies className to base classes", () => {
    const { container } = render(
      <Card data-testid="card" className="test-class">
        <div>child</div>
      </Card>
    );
    const card = container.firstChild as HTMLElement;
    expect(card).toHaveClass("rounded-xl", "test-class");
  });
});

describe("CardHeader", () => {
  it("renders with base classes and children", () => {
    const { container } = render(
      <CardHeader data-testid="card-header">
        <div>header</div>
      </CardHeader>
    );
    const header = container.firstChild as HTMLElement;
    expect(header).toHaveClass("flex", "flex-col", "gap-1", "p-4", "pb-2");
    expect(header).toHaveAttribute("data-testid", "card-header");
    expect(header).toHaveTextContent("header");
  });

  it("renders with additional className", () => {
    const { container } = render(
      <CardHeader className="custom-header">
        <div>header</div>
      </CardHeader>
    );
    const header = container.firstChild as HTMLElement;
    expect(header).toHaveClass("custom-header");
  });

  it("forwards ref", () => {
    const ref = { current: null };
    render(<CardHeader ref={ref} />);
    expect(ref.current).not.toBeNull();
  });

  it("fires onClick with correct args", () => {
    const handleClick = vi.fn();
    const { container } = render(
      <CardHeader data-testid="card-header" onClick={handleClick}>
        <div>header</div>
      </CardHeader>
    );
    const header = container.firstChild as HTMLElement;
    fireEvent.click(header);
    expect(handleClick).toHaveBeenCalledTimes(1);
  });

  it("forwards arbitrary props", () => {
    const { container } = render(
      <CardHeader data-testid="card-header" role="banner" aria-label="header">
        <div>header</div>
      </CardHeader>
    );
    const header = container.firstChild as HTMLElement;
    expect(header).toHaveAttribute("role", "banner");
    expect(header).toHaveAttribute("aria-label", "header");
  });

  it("handles empty children", () => {
    const { container } = render(<CardHeader data-testid="card-header" />);
    const header = container.firstChild as HTMLElement;
    expect(header).toBeInTheDocument();
  });
});

describe("CardTitle", () => {
  it("renders with base classes and text content", () => {
    const { container } = render(
      <CardTitle data-testid="card-title">
        Title
      </CardTitle>
    );
    const title = container.firstChild as HTMLElement;
    expect(title).toHaveClass("text-base", "font-semibold", "text-shell-text");
    expect(title).toHaveAttribute("data-testid", "card-title");
    expect(title).toHaveTextContent("Title");
  });

  it("renders with additional className", () => {
    const { container } = render(
      <CardTitle className="custom-title">
        Title
      </CardTitle>
    );
    const title = container.firstChild as HTMLElement;
    expect(title).toHaveClass("custom-title");
  });

  it("forwards ref", () => {
    const ref = { current: null };
    render(<CardTitle ref={ref} />);
    expect(ref.current).not.toBeNull();
  });

  it("fires onClick with correct args", () => {
    const handleClick = vi.fn();
    const { container } = render(
      <CardTitle data-testid="card-title" onClick={handleClick}>
        Title
      </CardTitle>
    );
    const title = container.firstChild as HTMLElement;
    fireEvent.click(title);
    expect(handleClick).toHaveBeenCalledTimes(1);
  });

  it("forwards arbitrary props", () => {
    const { container } = render(
      <CardTitle data-testid="card-title" role="heading" aria-level="2">
        Title
      </CardTitle>
    );
    const title = container.firstChild as HTMLElement;
    expect(title).toHaveAttribute("role", "heading");
    expect(title).toHaveAttribute("aria-level", "2");
  });

  it("handles empty children", () => {
    const { container } = render(<CardTitle data-testid="card-title" />);
    const title = container.firstChild as HTMLElement;
    expect(title).toBeInTheDocument();
  });
});

describe("CardDescription", () => {
  it("renders with base classes and text content", () => {
    const { container } = render(
      <CardDescription data-testid="card-description">
        Description
      </CardDescription>
    );
    const description = container.firstChild as HTMLElement;
    expect(description).toHaveClass("text-xs", "text-shell-text-secondary");
    expect(description).toHaveAttribute("data-testid", "card-description");
    expect(description).toHaveTextContent("Description");
  });

  it("renders with additional className", () => {
    const { container } = render(
      <CardDescription className="custom-description">
        Description
      </CardDescription>
    );
    const description = container.firstChild as HTMLElement;
    expect(description).toHaveClass("custom-description");
  });

  it("forwards ref", () => {
    const ref = { current: null };
    render(<CardDescription ref={ref} />);
    expect(ref.current).not.toBeNull();
  });

  it("fires onClick with correct args", () => {
    const handleClick = vi.fn();
    const { container } = render(
      <CardDescription data-testid="card-description" onClick={handleClick}>
        Description
      </CardDescription>
    );
    const description = container.firstChild as HTMLElement;
    fireEvent.click(description);
    expect(handleClick).toHaveBeenCalledTimes(1);
  });

  it("forwards arbitrary props", () => {
    const { container } = render(
      <CardDescription data-testid="card-description" role="doc-subtitle" aria-label="description">
        Description
      </CardDescription>
    );
    const description = container.firstChild as HTMLElement;
    expect(description).toHaveAttribute("role", "doc-subtitle");
    expect(description).toHaveAttribute("aria-label", "description");
  });

  it("handles empty children", () => {
    const { container } = render(<CardDescription data-testid="card-description" />);
    const description = container.firstChild as HTMLElement;
    expect(description).toBeInTheDocument();
  });
});

describe("CardContent", () => {
  it("renders with base classes and text content", () => {
    const { container } = render(
      <CardContent data-testid="card-content">
        Content
      </CardContent>
    );
    const content = container.firstChild as HTMLElement;
    expect(content).toHaveClass("p-4", "pt-2");
    expect(content).toHaveAttribute("data-testid", "card-content");
    expect(content).toHaveTextContent("Content");
  });

  it("renders with additional className", () => {
    const { container } = render(
      <CardContent className="custom-content">
        Content
      </CardContent>
    );
    const content = container.firstChild as HTMLElement;
    expect(content).toHaveClass("custom-content");
  });

  it("forwards ref", () => {
    const ref = { current: null };
    render(<CardContent ref={ref} />);
    expect(ref.current).not.toBeNull();
  });

  it("fires onClick with correct args", () => {
    const handleClick = vi.fn();
    const { container } = render(
      <CardContent data-testid="card-content" onClick={handleClick}>
        Content
      </CardContent>
    );
    const content = container.firstChild as HTMLElement;
    fireEvent.click(content);
    expect(handleClick).toHaveBeenCalledTimes(1);
  });

  it("forwards arbitrary props", () => {
    const { container } = render(
      <CardContent data-testid="card-content" role="main" aria-label="card content">
        Content
      </CardContent>
    );
    const content = container.firstChild as HTMLElement;
    expect(content).toHaveAttribute("role", "main");
    expect(content).toHaveAttribute("aria-label", "card content");
  });

  it("handles empty children", () => {
    const { container } = render(<CardContent data-testid="card-content" />);
    const content = container.firstChild as HTMLElement;
    expect(content).toBeInTheDocument();
  });
});

describe("CardFooter", () => {
  it("renders with base classes and children", () => {
    const { container } = render(
      <CardFooter data-testid="card-footer">
        <div>footer</div>
      </CardFooter>
    );
    const footer = container.firstChild as HTMLElement;
    expect(footer).toHaveClass("flex", "items-center", "p-4", "pt-2");
    expect(footer).toHaveAttribute("data-testid", "card-footer");
    expect(footer).toHaveTextContent("footer");
  });

  it("renders with additional className", () => {
    const { container } = render(
      <CardFooter className="custom-footer">
        <div>footer</div>
      </CardFooter>
    );
    const footer = container.firstChild as HTMLElement;
    expect(footer).toHaveClass("custom-footer");
  });

  it("forwards ref", () => {
    const ref = { current: null };
    render(<CardFooter ref={ref} />);
    expect(ref.current).not.toBeNull();
  });

  it("fires onClick with correct args", () => {
    const handleClick = vi.fn();
    const { container } = render(
      <CardFooter data-testid="card-footer" onClick={handleClick}>
        <div>footer</div>
      </CardFooter>
    );
    const footer = container.firstChild as HTMLElement;
    fireEvent.click(footer);
    expect(handleClick).toHaveBeenCalledTimes(1);
  });

  it("forwards arbitrary props", () => {
    const { container } = render(
      <CardFooter data-testid="card-footer" role="contentinfo" aria-label="footer">
        <div>footer</div>
      </CardFooter>
    );
    const footer = container.firstChild as HTMLElement;
    expect(footer).toHaveAttribute("role", "contentinfo");
    expect(footer).toHaveAttribute("aria-label", "footer");
  });

  it("handles empty children", () => {
    const { container } = render(<CardFooter data-testid="card-footer" />);
    const footer = container.firstChild as HTMLElement;
    expect(footer).toBeInTheDocument();
  });
});