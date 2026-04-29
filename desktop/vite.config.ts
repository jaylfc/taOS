import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
import tailwindcss from "@tailwindcss/vite";
import path from "path";

export default defineConfig({
  test: {
    environment: "jsdom",
    globals: true,
    setupFiles: ["./vitest.setup.ts"],
    // *.spec.ts is reserved for Playwright e2e specs; vitest uses *.test.ts
    exclude: ["**/node_modules/**", "**/dist/**", "tests/**"],
  },
  plugins: [react(), tailwindcss()],
  base: "/desktop/",
  resolve: {
    alias: { "@": path.resolve(__dirname, "src") },
  },
  build: {
    outDir: "../static/desktop",
    emptyOutDir: true,
    // CodeMirror + mathjs + lucide each ship genuinely large libraries
    // that we use in full (TextEditor, Calculator, icons everywhere).
    // Warning set above them — the splits below ensure none of these
    // land in the eager main bundle.
    chunkSizeWarningLimit: 1600,
    rollupOptions: {
      input: {
        main: path.resolve(__dirname, "index.html"),
        chat: path.resolve(__dirname, "chat.html"),
      },
      output: {
        // Split heavy third-party libraries into their own chunks so the
        // shared `main` bundle stays lean and each app's lazy chunk only
        // pulls in the vendor code it actually uses. The buckets are
        // ordered longest-prefix-first so more specific matches win.
        manualChunks(id) {
          if (!id.includes("node_modules")) return;
          // CodeMirror + @lezer: only bucket the core runtime into the
          // shared chunk. Languages (loaded lazily by @codemirror/language-data)
          // and their @lezer grammars stay as individual chunks so a user
          // opening a .ts file doesn't download the Fortran grammar.
          if (
            id.includes("@codemirror/state") ||
            id.includes("@codemirror/view") ||
            id.includes("@codemirror/language") ||
            id.includes("@codemirror/commands") ||
            id.includes("@codemirror/search") ||
            id.includes("@codemirror/autocomplete") ||
            id.includes("@codemirror/lint") ||
            id.includes("@codemirror/theme-one-dark") ||
            id.includes("@lezer/common") ||
            id.includes("@lezer/lr") ||
            id.includes("@lezer/highlight")
          ) {
            return "vendor-codemirror";
          }
          if (id.includes("@milkdown") || id.includes("prosemirror")) return "vendor-milkdown";
          if (id.includes("@xterm")) return "vendor-xterm";
          if (id.includes("mathjs")) return "vendor-mathjs";
          if (id.includes("plyr")) return "vendor-plyr";
          if (id.includes("chess.js")) return "vendor-chess";
          if (id.includes("react-grid-layout") || id.includes("react-resizable") || id.includes("react-rnd")) {
            return "vendor-layout";
          }
          if (id.includes("@radix-ui")) return "vendor-radix";
          // Icons live in their own chunk so the hash stays stable
          // across app code changes (good HTTP cache hits). It's ~800
          // kB raw / 150 kB gzipped — loaded once then cached.
          if (id.includes("lucide-react")) return "vendor-icons";
          if (id.includes("react-dom") || id.includes("/react/") || id.includes("scheduler")) {
            return "vendor-react";
          }
          // Everything else falls back to Rollup's automatic chunking.
          return undefined;
        },
      },
    },
  },
  server: {
    proxy: {
      "/api": "http://localhost:6969",
      "/ws": { target: "ws://localhost:6969", ws: true },
    },
  },
});
