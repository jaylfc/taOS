import { writeFileSync } from "node:fs";
import path from "node:path";

/** Writes version.json to the build output so the running SPA can poll it and
 *  detect when a new build has been deployed. */
export function spaVersionPlugin(version) {
  let outDir = "";
  return {
    name: "taos-spa-version",
    configResolved(config) {
      outDir = path.resolve(config.root, config.build.outDir);
    },
    writeBundle() {
      writeFileSync(
        path.join(outDir, "version.json"),
        JSON.stringify({ version }) + "\n",
      );
    },
  };
}