// desktop/src/apps/StoreApp/BackendPillBar.test.tsx
import { describe, it, expect } from "vitest";
import { render } from "@testing-library/react";
import { BackendPillBar } from "./BackendPillBar";

describe("BackendPillBar", () => {
  it("renders nothing when disabled", () => {
    const { container } = render(
      <BackendPillBar
        available={["rkllama"]}
        selected={[]}
        onChange={() => {}}
        disabled
      />
    );
    expect(container.firstChild).toBeNull();
  });

  it("uses BACKEND_META label for known backends", () => {
    const { getByText } = render(
      <BackendPillBar
        available={["rkllama", "ollama"]}
        selected={[]}
        onChange={() => {}}
      />
    );
    expect(getByText("rkllama (NPU)")).toBeTruthy();
    expect(getByText("Ollama")).toBeTruthy();
  });

  it("falls back to raw key for unknown backends", () => {
    const { getByText } = render(
      <BackendPillBar
        available={["mystery-backend"]}
        selected={[]}
        onChange={() => {}}
      />
    );
    expect(getByText("mystery-backend")).toBeTruthy();
  });

  it("aria-pressed reflects selection", () => {
    const { getByRole } = render(
      <BackendPillBar
        available={["rkllama"]}
        selected={["rkllama"]}
        onChange={() => {}}
      />
    );
    const btn = getByRole("button", { name: /rkllama/i });
    expect(btn.getAttribute("aria-pressed")).toBe("true");
  });
});
