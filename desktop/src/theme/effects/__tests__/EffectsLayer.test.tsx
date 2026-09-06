import { describe, it, expect } from "vitest";
import { render } from "@testing-library/react";
import { EffectsLayer } from "../EffectsLayer";
import { useThemeStore } from "@/stores/theme-store";

describe("EffectsLayer", () => {
  it("renders enabled effect modules in a non-interactive layer", () => {
    useThemeStore.setState({ effects: [{ module: "scanlines" }, { module: "crt" }] } as never);
    const { container } = render(<EffectsLayer />);
    const layer = container.querySelector('[data-testid="effects-layer"]') as HTMLElement;
    expect(layer).not.toBeNull();
    expect(layer.style.pointerEvents).toBe("none");
    expect(container.querySelector('[data-effect="scanlines"]')).not.toBeNull();
    expect(container.querySelector('[data-effect="crt"]')).not.toBeNull();
  });

  it("renders nothing for unknown modules", () => {
    useThemeStore.setState({ effects: [{ module: "nope" }] } as never);
    const { container } = render(<EffectsLayer />);
    expect(container.querySelector("[data-effect]")).toBeNull();
  });
});
