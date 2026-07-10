/** Derive a unique, valid JS identifier from an asset filename so repeated
 *  inserts into the same JS file don't emit a colliding ``const`` declaration. */
function assetVarName(filename: string): string {
  const base = filename.replace(/\.[^.]+$/, "").replace(/[^a-zA-Z0-9]+/g, "_");
  const safe = /^[a-zA-Z_]/.test(base) ? base : `_${base}`;
  return `${safe}Url`;
}

/** Build a code reference to a generated asset, tailored to the file it will be
 *  appended to. The asset already lives in the game's file set, so a relative
 *  ``./{filename}`` resolves under the game's preview base. */
export function buildAssetReference(activePath: string, filename: string): string {
  const ext = activePath.slice(activePath.lastIndexOf(".") + 1).toLowerCase();
  const rel = `./${filename}`;
  if (ext === "html" || ext === "htm") {
    return `\n<img src="${rel}" alt="" />`;
  }
  if (ext === "css") {
    return `\n/* generated asset */\n.generated-asset { background-image: url("${rel}"); }`;
  }
  if (ext === "js" || ext === "mjs" || ext === "ts") {
    return `\n// generated asset\nconst ${assetVarName(filename)} = "${rel}";`;
  }
  return `\n/* asset: ${rel} */`;
}
