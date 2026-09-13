import { defineConfig } from "vite";
import path from "path";

export default defineConfig({
  build: {
    outDir: "../static/desktop",
    emptyOutDir: false,
    rollupOptions: {
      input: {
        sw: path.resolve(__dirname, "src/sw.ts"),
      },
      output: {
        format: "iife",
        inlineDynamicImports: true,
        entryFileNames: "sw.js",
      },
    },
  },
});
