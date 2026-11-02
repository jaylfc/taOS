import { writeFileSync } from "node:fs";
import { join } from "node:path";

/** Writes version.json to the build output so the running SPA can poll it and
 *  detect when a new build has been deployed. */
export function spaVersionPlugin(version) {
  let outDir = "";
  return {
    name: "taos-spa-version",
    configResolved(config) {
      outDir = join(config.root, config.build.outDir);
    },
    writeBundle() {
      writeFileSync(join(outDir, "version.json"), JSON.stringify({ version }) + "\n");
    },
  };
}
