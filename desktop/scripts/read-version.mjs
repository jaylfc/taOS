// Reads __version__ from ../tinyagentos/__init__.py at Vite build time.
// Single source of truth with the backend.
import { readFileSync } from "node:fs";
import { execFileSync } from "node:child_process";
import { resolve, dirname } from "node:path";
import { fileURLToPath } from "node:url";

const HERE = dirname(fileURLToPath(import.meta.url));
const initPath = resolve(HERE, "..", "..", "tinyagentos", "__init__.py");

function readPackageVersion() {
  try {
    const src = readFileSync(initPath, "utf8");
    const m = src.match(/^\s*__version__\s*=\s*['"]([^'"]+)['"]/m);
    return m ? m[1] : "dev";
  } catch {
    return "dev";
  }
}

function readBuildId() {
  // Include both the git short SHA (reproducible anchored identifier) and
  // a high-resolution timestamp so every build — even back-to-back
  // builds on the same commit — produces a unique identifier.  The SPA
  // version-check poll relies on this to detect redeployed bundles.
  //
  // Format: {sha}.{timestamp}  (e.g. "a3bd632.lrx5f4m9abc")
  // When git isn't available only the timestamp is emitted.
  let sha = "";
  try {
    sha = execFileSync("git", ["rev-parse", "--short", "HEAD"], {
      cwd: HERE,
      stdio: ["ignore", "pipe", "ignore"],
    }).toString().trim();
  } catch {
    // git not available — no SHA component
  }
  const ms = Date.now().toString(36);
  const hr = process.hrtime.bigint().toString(36);
  // Take the last 6 base-36 chars of the nanosecond counter — combined
  // with the ms component this yields ~2.1B unique values per ms tick,
  // which is plenty for sub-millisecond build differentiation while
  // keeping the stamp compact.
  const stamp = `${ms}${hr.slice(-6)}`;
  return sha ? `${sha}.${stamp}` : stamp;
}

// Service worker uses this string as its cache name (`taos-static-${VERSION}`).
// Combining the package version with a unique build id (SHA + timestamp)
// guarantees a fresh SW per build, so stale-PWA recovery actually works after
// a controller upgrade and the SPA version-check poll detects redeployed
// bundles even when the package version hasn't changed.
export function readBackendVersion() {
  return `${readPackageVersion()}+${readBuildId()}`;
}
