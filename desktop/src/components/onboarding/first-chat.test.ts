import { describe, it, expect } from "vitest";
import { assistantNameError } from "./first-chat";

describe("assistantNameError", () => {
  it("accepts a name with a letter or digit, in any script", () => {
    for (const n of ["Assistant", "R2", "  Sage ", "Élodie", "助手", "x".repeat(64)]) {
      expect(assistantNameError(n)).toBe("");
    }
  });

  it("refuses a blank, an over-long or a symbol-only name", () => {
    expect(assistantNameError("   ")).toBe("Give your assistant a name.");
    expect(assistantNameError("x".repeat(65))).toBe("Use 64 characters or fewer.");
    expect(assistantNameError("!!! ---")).toBe("Use at least one letter or number.");
  });
});
