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
  // Prefer the git short SHA so back-to-back builds on the same commit
  // produce identical bundles. Fall back to an epoch-second timestamp
  // when git isn't available (e.g. an sdist install).
  try {
    const sha = execFileSync("git", ["rev-parse", "--short", "HEAD"], {
      cwd: HERE,
      stdio: ["ignore", "pipe", "ignore"],
    }).toString().trim();
    if (sha) return sha;
  } catch {
    // git not available — fall through to timestamp
  }
  // Two distinct fields encoded in base36 so two builds within the same
  // second still produce distinct ids (Date.now() is millisecond-resolution
  // but high-res nanos disambiguate even sub-millisecond bursts).
  const ms = Date.now().toString(36);
  const hr = process.hrtime.bigint().toString(36);
  return `${ms}-${hr.slice(-6)}`;
}

// Service worker uses this string as its cache name (`taos-static-${VERSION}`).
// If the string never changes between builds, the browser sees a byte-identical
// SW on the next visit, never fires install/activate, never wipes the old
// precache, and clients keep loading the previous bundle forever. Combining
// the package version with the git SHA guarantees a fresh SW per build, so
// stale-PWA recovery actually works after a controller upgrade.
export function readBackendVersion() {
  return `${readPackageVersion()}+${readBuildId()}`;
}
