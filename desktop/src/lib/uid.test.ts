import { describe, expect, it } from "vitest";
import { randomId } from "./uid";

describe("randomId", () => {
  it("returns an id normally", () => {
    const id = randomId();
    expect(typeof id).toBe("string");
    expect(id.length).toBeGreaterThan(0);
  });

  it("applies the given prefix", () => {
    expect(randomId("sec-")).toMatch(/^sec-/);
  });

  it("still returns a valid id when crypto.randomUUID is unavailable (non-secure-context http)", () => {
    const original = crypto.randomUUID;
    // @ts-expect-error simulate a non-secure context where randomUUID is undefined
    crypto.randomUUID = undefined;
    try {
      const id = randomId();
      expect(typeof id).toBe("string");
      expect(id.length).toBeGreaterThan(0);
    } finally {
      crypto.randomUUID = original;
    }
  });
});
