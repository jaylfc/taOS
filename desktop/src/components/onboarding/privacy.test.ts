import { describe, it, expect } from "vitest";
import { formatRate } from "./privacy";

describe("formatRate", () => {
  it("reads KB/s caps the way people say them", () => {
    expect(formatRate(5000)).toBe("5 MB/s");
    expect(formatRate(2500)).toBe("2.5 MB/s");
    expect(formatRate(512)).toBe("512 KB/s");
  });

  it("has no text for no cap", () => {
    expect(formatRate(0)).toBeNull();
  });
});
