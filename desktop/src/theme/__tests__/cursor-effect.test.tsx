import { describe, it, expect, afterEach } from "vitest";
import { render, cleanup } from "@testing-library/react";
import { CursorEffect } from "../effects/CursorEffect";

afterEach(() => {
  cleanup();
  document.body.style.cursor = "";
});

describe("CursorEffect", () => {
  it("applies a safe cursor value", () => {
    render(<CursorEffect params={{ cursor: "pointer" }} />);
    expect(document.body.style.cursor).toBe("pointer");
  });

  it("ignores an unsafe cursor value and falls back to crosshair", () => {
    render(<CursorEffect params={{ cursor: "url(/x.png)" }} />);
    expect(document.body.style.cursor).toBe("crosshair");
  });
});
