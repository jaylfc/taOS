import { describe, it, expect } from "vitest";
import { APP_REDIRECTS, resolvePinnedId } from "./app-registry";

describe("redirect section probe", () => {
  it("carries section 'archive' through the redirect", () => {
    expect(APP_REDIRECTS["notification-archive"].section).toBe("archive");
  });

  it("resolvePinnedId can express the target section", () => {
    expect(typeof resolvePinnedId("notification-archive")).not.toBe("string");
  });
});
