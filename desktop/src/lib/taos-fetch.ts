/**
 * fetch wrapper that:
 *  - reads X-Taos-Version from every response and feeds it into
 *    BackendStatus (so version changes are detected opportunistically
 *    without a separate /api/version request).
 *  - converts thrown network errors into a tagged BackendUnavailableError
 *    when BackendStatus.getStatus() === "reconnecting", so the React
 *    error boundary can recognize and render a friendly "waiting for
 *    taOS to come back" skeleton instead of the app's default error UI.
 *
 * Drop-in replacement for global fetch. Codebase migrates calls over time;
 * new code uses this from day one.
 */
import type { BackendStatusController } from "./backendStatus";
import { withCsrf } from "./csrf";

export class BackendUnavailableError extends Error {
  constructor(message = "Backend is unavailable") {
    super(message);
    this.name = "BackendUnavailableError";
  }
}

interface Options {
  status: BackendStatusController;
  fetchImpl?: typeof fetch;
}

export function createTaosFetch(opts: Options): typeof fetch {
  const inner = opts.fetchImpl ?? fetch;
  const wrapped: typeof fetch = async (input, init) => {
    try {
      const r = await inner(input as RequestInfo, withCsrf(init));
      const v = r.headers.get("X-Taos-Version");
      if (v) opts.status.reportVersion(v);
      return r;
    } catch (err) {
      if (opts.status.getStatus() === "reconnecting") {
        throw new BackendUnavailableError(
          err instanceof Error ? err.message : String(err)
        );
      }
      throw err;
    }
  };
  return wrapped;
}
