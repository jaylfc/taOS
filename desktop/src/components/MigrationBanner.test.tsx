/**
 * @vitest-environment jsdom
 */
import { describe, it, expect, vi, afterEach } from "vitest";
import { render, screen, fireEvent, cleanup } from "@testing-library/react";
import { MigrationBanner } from "./MigrationBanner";

afterEach(cleanup);

describe("MigrationBanner", () => {
  it("renders the upgrade message and action buttons", () => {
    render(
      <MigrationBanner
        agent={{ migrated_to_v2_personas: false }}
        onDismiss={vi.fn()}
        onAddPersona={vi.fn()}
      />
    );
    expect(screen.getByText(/Memory upgraded/)).toBeTruthy();
    expect(screen.getByRole("button", { name: /add persona/i })).toBeTruthy();
    expect(screen.getByRole("button", { name: /dismiss/i })).toBeTruthy();
  });

  it("returns null when agent is already migrated", () => {
    const { container } = render(
      <MigrationBanner
        agent={{ migrated_to_v2_personas: true }}
        onDismiss={vi.fn()}
        onAddPersona={vi.fn()}
      />
    );
    expect(container.innerHTML).toBe("");
  });

  it("calls onAddPersona when Add persona is clicked", () => {
    const onAddPersona = vi.fn();
    render(
      <MigrationBanner
        agent={{ migrated_to_v2_personas: false }}
        onDismiss={vi.fn()}
        onAddPersona={onAddPersona}
      />
    );
    fireEvent.click(screen.getByRole("button", { name: /add persona/i }));
    expect(onAddPersona).toHaveBeenCalledTimes(1);
  });

  it("calls onDismiss when Dismiss is clicked", () => {
    const onDismiss = vi.fn();
    render(
      <MigrationBanner
        agent={{ migrated_to_v2_personas: false }}
        onDismiss={onDismiss}
        onAddPersona={vi.fn()}
      />
    );
    fireEvent.click(screen.getByRole("button", { name: /dismiss/i }));
    expect(onDismiss).toHaveBeenCalledTimes(1);
  });
});
