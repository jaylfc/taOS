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
    return `\n// generated asset\nconst assetUrl = "${rel}";`;
  }
  return `\n/* asset: ${rel} */`;
}
