import { render, screen, fireEvent } from "@testing-library/react";
import { describe, it, expect, vi } from "vitest";
import { MigrationBanner } from "../MigrationBanner";

describe("<MigrationBanner />", () => {
  it("renders the migration message when agent has not migrated", () => {
    render(
      <MigrationBanner
        agent={{ migrated_to_v2_personas: false }}
        onDismiss={vi.fn()}
        onAddPersona={vi.fn()}
      />
    );
    expect(
      screen.getByText(/Memory upgraded/i)
    ).toBeInTheDocument();
  });

  it("renders nothing when agent has migrated", () => {
    const { container } = render(
      <MigrationBanner
        agent={{ migrated_to_v2_personas: true }}
        onDismiss={vi.fn()}
        onAddPersona={vi.fn()}
      />
    );
    expect(container.textContent).toBe("");
  });

  it("calls onAddPersona when the Add persona button is clicked", () => {
    const onAddPersona = vi.fn();
    render(
      <MigrationBanner
        agent={{}}
        onDismiss={vi.fn()}
        onAddPersona={onAddPersona}
      />
    );
    fireEvent.click(screen.getByRole("button", { name: /add persona/i }));
    expect(onAddPersona).toHaveBeenCalled();
  });

  it("calls onDismiss when the Dismiss button is clicked", () => {
    const onDismiss = vi.fn();
    render(
      <MigrationBanner
        agent={{}}
        onDismiss={onDismiss}
        onAddPersona={vi.fn()}
      />
    );
    fireEvent.click(screen.getByRole("button", { name: /dismiss/i }));
    expect(onDismiss).toHaveBeenCalled();
  });
});
