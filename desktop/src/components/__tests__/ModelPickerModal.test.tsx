import { describe, it, expect, vi } from "vitest";
import { render, screen, fireEvent } from "@testing-library/react";
import { ModelPickerModal } from "../ModelPickerModal";

const sampleModel = { id: "model-1", name: "Test Model", host: "localhost", hostKind: "controller" as const };

describe("ModelPickerModal", () => {
  it("renders the title when open", () => {
    render(
      <ModelPickerModal
        open={true}
        onClose={vi.fn()}
        models={[sampleModel]}
        modelsLoaded={true}
        onSelect={vi.fn()}
      />,
    );
    expect(screen.getByText("Select Model")).toBeInTheDocument();
  });

  it("does not render when closed", () => {
    const { container } = render(
      <ModelPickerModal
        open={false}
        onClose={vi.fn()}
        models={[sampleModel]}
        modelsLoaded={true}
        onSelect={vi.fn()}
      />,
    );
    expect(container.innerHTML).toBe("");
  });

  it("calls onClose when the close button is clicked", () => {
    const onClose = vi.fn();
    render(
      <ModelPickerModal
        open={true}
        onClose={onClose}
        models={[sampleModel]}
        modelsLoaded={true}
        onSelect={vi.fn()}
      />,
    );
    fireEvent.click(screen.getByRole("button", { name: /close/i }));
    expect(onClose).toHaveBeenCalled();
  });

  it("calls onSelect when a model is selected", () => {
    const onSelect = vi.fn();
    render(
      <ModelPickerModal
        open={true}
        onClose={vi.fn()}
        models={[sampleModel]}
        modelsLoaded={true}
        onSelect={onSelect}
      />,
    );
    fireEvent.click(screen.getByText("Test Model"));
    expect(onSelect).toHaveBeenCalledWith("model-1", sampleModel);
  });

  it("calls onClose when a model is selected", () => {
    const onClose = vi.fn();
    render(
      <ModelPickerModal
        open={true}
        onClose={onClose}
        models={[sampleModel]}
        modelsLoaded={true}
        onSelect={vi.fn()}
      />,
    );
    fireEvent.click(screen.getByText("Test Model"));
    expect(onClose).toHaveBeenCalled();
  });

  it("renders a custom title", () => {
    render(
      <ModelPickerModal
        open={true}
        onClose={vi.fn()}
        models={[sampleModel]}
        modelsLoaded={true}
        onSelect={vi.fn()}
        title="Pick a model"
      />,
    );
    expect(screen.getByText("Pick a model")).toBeInTheDocument();
  });
});
