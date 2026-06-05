import { describe, it, expect } from "vitest";
import React from "react";
import { render, screen } from "@testing-library/react";
import { ModelPickerFlow } from "./ModelPickerFlow";

describe("ModelPickerFlow — empty state (fix #618)", () => {
  it("shows 'No models available.' when models=[] and modelsLoaded=true", () => {
    render(
      <ModelPickerFlow
        models={[]}
        modelsLoaded={true}
        onSelect={vi.fn()}
      />
    );
    expect(screen.getByText("No models available.")).toBeInTheDocument();
  });

  it("shows loading text when modelsLoaded=false", () => {
    render(
      <ModelPickerFlow
        models={[]}
        modelsLoaded={false}
        onSelect={vi.fn()}
      />
    );
    expect(screen.getByText("Loading models…")).toBeInTheDocument();
    expect(screen.queryByText("No models available.")).not.toBeInTheDocument();
  });
});
