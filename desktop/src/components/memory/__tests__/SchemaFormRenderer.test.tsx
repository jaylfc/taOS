import { describe, it, expect, vi } from "vitest";
import { render, screen, fireEvent } from "@testing-library/react";
import { SchemaFormRenderer } from "../SchemaFormRenderer";

const baseSchema = {
  name: { type: "string", title: "Name", description: "Your name" },
  enabled: { type: "boolean", title: "Enabled" },
  color: { type: "string", title: "Color", enum: ["red", "green", "blue"], default: "red" },
  count: { type: "integer", title: "Count", minimum: 0, maximum: 10 },
  score: { type: "number", title: "Score", minimum: 0, maximum: 100, default: 50 },
};

const baseValues = { name: "Alice", enabled: true, color: "blue", count: 5, score: 75 };

function makeProps(schema = baseSchema, values = baseValues) {
  const onChange = vi.fn();
  return { schema, values, onChange };
}

describe("SchemaFormRenderer", () => {
  it("renders nothing when schema is empty", () => {
    const onChange = vi.fn();
    const { container } = render(<SchemaFormRenderer schema={{}} values={{}} onChange={onChange} />);
    expect(container.querySelector("div")).toBeNull();
    expect(screen.getByText("No settings available.")).toBeInTheDocument();
  });

  it("renders nothing when schema is null", () => {
    const onChange = vi.fn();
    const { container } = render(
      <SchemaFormRenderer schema={null as any} values={{}} onChange={onChange} />,
    );
    expect(container.querySelector("div")).toBeNull();
    expect(screen.getByText("No settings available.")).toBeInTheDocument();
  });

  it("renders string field with title and description", () => {
    render(<SchemaFormRenderer {...makeProps()} />);
    expect(screen.getByLabelText("Name")).toBeInTheDocument();
    expect(screen.getByText("Your name")).toBeInTheDocument();
    expect(screen.getByRole("textbox", { name: "Name" })).toHaveValue("Alice");
  });

  it("renders boolean field as a switch with aria-label", () => {
    render(<SchemaFormRenderer {...makeProps()} />);
    const sw = screen.getByRole("switch", { name: "Enabled" });
    expect(sw).toHaveAttribute("aria-checked", "true");
    expect(sw).toHaveAttribute("aria-label", "Enabled");
  });

  it("renders enum field as a select with all options", () => {
    render(<SchemaFormRenderer {...makeProps()} />);
    const select = screen.getByRole("combobox", { name: "Color" });
    expect(select).toHaveValue("blue");
    const options = select.querySelectorAll("option");
    expect(options).toHaveLength(3);
    expect(options[0]).toHaveValue("red");
    expect(options[2]).toHaveTextContent("blue");
  });

  it("renders number field with min/max attributes", () => {
    render(<SchemaFormRenderer {...makeProps()} />);
    const input = screen.getByRole("spinbutton", { name: "Count" });
    expect(input).toHaveValue(5);
    expect(input).toHaveAttribute("min", "0");
    expect(input).toHaveAttribute("max", "10");
  });

  it("renders default value when key missing from values", () => {
    const onChange = vi.fn();
    render(
      <SchemaFormRenderer
        schema={{ score: { type: "number", title: "Score", default: 42 } }}
        values={{}}
        onChange={onChange}
      />,
    );
    expect(screen.getByRole("spinbutton", { name: "Score" })).toHaveValue(42);
  });

  it("renders empty string when no value and no default", () => {
    const onChange = vi.fn();
    render(
      <SchemaFormRenderer
        schema={{ name: { type: "string", title: "Name" } }}
        values={{}}
        onChange={onChange}
      />,
    );
    expect(screen.getByRole("textbox", { name: "Name" })).toHaveValue("");
  });

  it("uses key as label fallback when title is missing", () => {
    const onChange = vi.fn();
    render(
      <SchemaFormRenderer
        schema={{ myField: { type: "string" } }}
        values={{ myField: "x" }}
        onChange={onChange}
      />,
    );
    expect(screen.getByLabelText("myField")).toBeInTheDocument();
  });

  it("does not render description when prop is absent", () => {
    const onChange = vi.fn();
    render(
      <SchemaFormRenderer
        schema={{ enabled: { type: "boolean", title: "Enabled" } }}
        values={{ enabled: false }}
        onChange={onChange}
      />,
    );
    expect(screen.queryByText("Your name")).not.toBeInTheDocument();
  });

  it("onChange fires with key and value when text input changes", async () => {
    const onChange = vi.fn();
    render(
      <SchemaFormRenderer
        schema={{ name: { type: "string", title: "Name" } }}
        values={{ name: "Alice" }}
        onChange={onChange}
      />,
    );
    const input = screen.getByRole("textbox", { name: "Name" });
    fireEvent.change(input, { target: { value: "Bob" } });
    expect(onChange).toHaveBeenCalledWith("name", "Bob");
  });

  it("onChange fires with key and checked when switch is toggled", async () => {
    const onChange = vi.fn();
    render(
      <SchemaFormRenderer
        schema={{ enabled: { type: "boolean", title: "Enabled" } }}
        values={{ enabled: true }}
        onChange={onChange}
      />,
    );
    const sw = screen.getByRole("switch", { name: "Enabled" });
    fireEvent.click(sw);
    expect(onChange).toHaveBeenCalledWith("enabled", false);
  });

  it("onChange fires with key and selected value when select changes", async () => {
    const onChange = vi.fn();
    render(
      <SchemaFormRenderer
        schema={{ color: { type: "string", title: "Color", enum: ["red", "green", "blue"] } }}
        values={{ color: "red" }}
        onChange={onChange}
      />,
    );
    const select = screen.getByRole("combobox", { name: "Color" });
    fireEvent.change(select, { target: { value: "green" } });
    expect(onChange).toHaveBeenCalledWith("color", "green");
  });

  it("onChange fires with numeric value when number input changes", async () => {
    const onChange = vi.fn();
    render(
      <SchemaFormRenderer
        schema={{ count: { type: "integer", title: "Count" } }}
        values={{ count: 5 }}
        onChange={onChange}
      />,
    );
    const input = screen.getByRole("spinbutton", { name: "Count" });
    fireEvent.change(input, { target: { value: "7" } });
    expect(onChange).toHaveBeenCalledWith("count", 7);
  });

  it("onChange fires with empty string when number input is cleared", async () => {
    const onChange = vi.fn();
    render(
      <SchemaFormRenderer
        schema={{ count: { type: "integer", title: "Count" } }}
        values={{ count: 5 }}
        onChange={onChange}
      />,
    );
    const input = screen.getByRole("spinbutton", { name: "Count" });
    fireEvent.change(input, { target: { value: "" } });
    expect(onChange).toHaveBeenCalledWith("count", "");
  });

  it("renders multiple fields in one schema", () => {
    render(<SchemaFormRenderer {...makeProps()} />);
    expect(screen.getByRole("textbox", { name: "Name" })).toBeInTheDocument();
    expect(screen.getByRole("switch", { name: "Enabled" })).toBeInTheDocument();
    expect(screen.getByRole("combobox", { name: "Color" })).toBeInTheDocument();
    expect(screen.getByRole("spinbutton", { name: "Count" })).toBeInTheDocument();
    expect(screen.getByRole("spinbutton", { name: "Score" })).toBeInTheDocument();
  });

  it("renders default text input for string without enum", () => {
    render(<SchemaFormRenderer {...makeProps()} />);
    const input = screen.getByLabelText("Name");
    expect(input.tagName).toBe("INPUT");
    expect(input).toHaveAttribute("type", "text");
  });

  it("renders number input for type=number", () => {
    const onChange = vi.fn();
    render(
      <SchemaFormRenderer
        schema={{ score: { type: "number", title: "Score" } }}
        values={{ score: 50 }}
        onChange={onChange}
      />,
    );
    const input = screen.getByRole("spinbutton", { name: "Score" });
    expect(input).toHaveAttribute("type", "number");
  });

  it("renders correct id and htmlFor pairing", () => {
    render(<SchemaFormRenderer {...makeProps()} />);
    const input = screen.getByLabelText("Name");
    expect(input).toHaveAttribute("id", "schema-field-name");
  });

  it("renders space-y-4 wrapper class", () => {
    const onChange = vi.fn();
    const { container } = render(
      <SchemaFormRenderer
        schema={{ name: { type: "string", title: "Name" } }}
        values={{ name: "x" }}
        onChange={onChange}
      />,
    );
    expect(container.firstChild).toHaveClass("space-y-4");
  });

  it("renders empty-state text with correct classes", () => {
    const onChange = vi.fn();
    render(<SchemaFormRenderer schema={{}} values={{}} onChange={onChange} />);
    const p = screen.getByText("No settings available.");
    expect(p).toHaveClass("text-xs");
    expect(p).toHaveClass("text-shell-text-tertiary");
    expect(p).toHaveClass("text-center");
  });

  it("boolean field renders unchecked when value is false", () => {
    const onChange = vi.fn();
    render(
      <SchemaFormRenderer
        schema={{ enabled: { type: "boolean", title: "Enabled" } }}
        values={{ enabled: false }}
        onChange={onChange}
      />,
    );
    expect(screen.getByRole("switch", { name: "Enabled" })).toHaveAttribute("aria-checked", "false");
  });

  it("boolean field renders checked when value is truthy (non-bool)", () => {
    const onChange = vi.fn();
    render(
      <SchemaFormRenderer
        schema={{ enabled: { type: "boolean", title: "Enabled" } }}
        values={{ enabled: 1 }}
        onChange={onChange}
      />,
    );
    expect(screen.getByRole("switch", { name: "Enabled" })).toHaveAttribute("aria-checked", "true");
  });
});
