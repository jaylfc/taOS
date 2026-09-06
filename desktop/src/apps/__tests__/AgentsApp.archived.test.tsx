import { describe, it, expect } from "vitest";

// Unit tests for the archive timestamp helpers.
// Logic is duplicated here to avoid exporting internals from AgentsApp.

function parseArchiveTimestamp(ts: string): Date | null {
  const m = /^(\d{4})(\d{2})(\d{2})T(\d{2})(\d{2})(\d{2})$/.exec(ts);
  if (!m) return null;
  return new Date(Date.UTC(+m[1]!, +m[2]! - 1, +m[3]!, +m[4]!, +m[5]!, +m[6]!));
}

function relativeTimeFromTs(ts: string): string {
  const d = parseArchiveTimestamp(ts);
  if (!d) return ts;
  const diff = Date.now() - d.getTime();
  const mins = Math.floor(diff / 60000);
  if (mins < 1) return "just now";
  if (mins < 60) return `${mins}m ago`;
  const hrs = Math.floor(mins / 60);
  if (hrs < 24) return `${hrs}h ago`;
  const days = Math.floor(hrs / 24);
  if (days < 7) return `${days}d ago`;
  return d.toLocaleDateString();
}

describe("parseArchiveTimestamp", () => {
  it("parses a valid compact timestamp", () => {
    const d = parseArchiveTimestamp("20260416T213045");
    expect(d).toBeInstanceOf(Date);
    expect(d!.getUTCFullYear()).toBe(2026);
    expect(d!.getUTCMonth()).toBe(3);
    expect(d!.getUTCDate()).toBe(16);
    expect(d!.getUTCHours()).toBe(21);
    expect(d!.getUTCMinutes()).toBe(30);
    expect(d!.getUTCSeconds()).toBe(45);
  });

  it("returns null for invalid timestamps", () => {
    expect(parseArchiveTimestamp("not-a-date")).toBeNull();
    expect(parseArchiveTimestamp("")).toBeNull();
    expect(parseArchiveTimestamp("2026-04-16T21:30:45")).toBeNull();
  });
});

describe("relativeTimeFromTs", () => {
  it("passes through unparseable timestamps unchanged", () => {
    expect(relativeTimeFromTs("bad-ts")).toBe("bad-ts");
  });

  it("returns a non-empty string for a parseable past timestamp", () => {
    const result = relativeTimeFromTs("20200101T000000");
    expect(typeof result).toBe("string");
    expect(result.length).toBeGreaterThan(0);
  });
});
