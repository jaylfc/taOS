import { describe, it, expect } from "vitest";
import {
  validateBundleFile,
  buildImportFormData,
  secretsRowsToObject,
  formatBundleSize,
  MAX_BUNDLE_BYTES,
  type SecretRow,
} from "./importBundle";

function makeFile(name: string, size: number): File {
  // A File backed by a Blob of the requested byte length.
  const blob = new Blob([new Uint8Array(size)]);
  return new File([blob], name, { type: "application/octet-stream" });
}

describe("validateBundleFile", () => {
  it("rejects a missing file", () => {
    const r = validateBundleFile(null);
    expect(r.ok).toBe(false);
    expect(r.error).toMatch(/select/i);
  });

  it("rejects an empty file", () => {
    const r = validateBundleFile(makeFile("agent.zip", 0));
    expect(r.ok).toBe(false);
    expect(r.error).toMatch(/empty/i);
  });

  it("rejects an unsupported extension", () => {
    const r = validateBundleFile(makeFile("agent.txt", 10));
    expect(r.ok).toBe(false);
    expect(r.error).toMatch(/unsupported/i);
  });

  it("rejects a file over the size cap", () => {
    // Avoid allocating 200MB+: stub a File-like object with an oversized size.
    const big = { name: "agent.zip", size: MAX_BUNDLE_BYTES + 1 } as File;
    const r = validateBundleFile(big);
    expect(r.ok).toBe(false);
    expect(r.error).toMatch(/too large/i);
  });

  it("accepts .zip, .tar.gz and .tgz", () => {
    expect(validateBundleFile(makeFile("agent.zip", 10)).ok).toBe(true);
    expect(validateBundleFile(makeFile("agent.tar.gz", 10)).ok).toBe(true);
    expect(validateBundleFile(makeFile("agent.tgz", 10)).ok).toBe(true);
  });

  it("matches extensions case-insensitively", () => {
    expect(validateBundleFile(makeFile("AGENT.ZIP", 10)).ok).toBe(true);
  });
});

describe("secretsRowsToObject", () => {
  it("drops blank keys and trims them", () => {
    const rows: SecretRow[] = [
      { key: "  OPENAI_API_KEY  ", value: "sk-1" },
      { key: "", value: "ignored" },
      { key: "   ", value: "also-ignored" },
    ];
    expect(secretsRowsToObject(rows)).toEqual({ OPENAI_API_KEY: "sk-1" });
  });

  it("keeps the last value on duplicate keys", () => {
    const rows: SecretRow[] = [
      { key: "TOKEN", value: "a" },
      { key: "TOKEN", value: "b" },
    ];
    expect(secretsRowsToObject(rows)).toEqual({ TOKEN: "b" });
  });
});

describe("formatBundleSize", () => {
  it("formats bytes, KB and MB", () => {
    expect(formatBundleSize(512)).toBe("512 B");
    expect(formatBundleSize(2048)).toBe("2.0 KB");
    expect(formatBundleSize(5 * 1024 * 1024)).toBe("5.0 MB");
  });
});

describe("buildImportFormData", () => {
  it("assembles the multipart payload with framework, file, name, model and secrets JSON", () => {
    const bundle = makeFile("agent.tar.gz", 16);
    const fd = buildImportFormData({
      framework: "hermes",
      bundle,
      name: "  My Agent  ",
      model: "  gpt-4o  ",
      secrets: [
        { key: "API_KEY", value: "secret" },
        { key: "", value: "skip" },
      ],
    });

    expect(fd.get("framework")).toBe("hermes");
    expect(fd.get("name")).toBe("My Agent");
    expect(fd.get("model")).toBe("gpt-4o");
    expect(fd.get("secrets")).toBe(JSON.stringify({ API_KEY: "secret" }));

    const file = fd.get("bundle");
    expect(file).toBeInstanceOf(File);
    expect((file as File).name).toBe("agent.tar.gz");
  });

  it("omits an empty model field", () => {
    const fd = buildImportFormData({
      framework: "hermes",
      bundle: makeFile("a.zip", 4),
      name: "x",
      model: "   ",
      secrets: [],
    });
    expect(fd.has("model")).toBe(false);
    expect(fd.get("secrets")).toBe("{}");
  });
});
