// desktop/src/theme/__tests__/light-scheme-arbitrary-values.test.ts
//
// Regression guard for the light-scheme gap in tokens.css. The compatibility
// layer inverted the *plain-fraction* overlay utilities (bg-white/5, …) but
// NOT the arbitrary-value form (bg-white/[0.04]) used by the shared primitives
// (card, button, tabs) and ~126 app surfaces. This test proves both forms
// now invert.
//
// Two traps this test is built to avoid:
//   1. It asserts on COMPUTED colour, never on the class name — the class is
//      present in both schemes, so a class-name assertion could never fail.
//   2. It reads tokens.css via node:fs and asserts the load actually happened —
//      `import tokensCss from "../tokens.css"` returns an empty string under
//      this repo's vitest config.
//
// A third trap is avoided by the coverage test: it DERIVES the covered overlay
// set out of tokens.css and diffs it against what desktop/src actually uses,
// rather than asserting against a hardcoded allowlist. An allowlist cannot fail
// on a form it does not already enumerate, so the next new arbitrary-value
// utility would slip past it blind; deriving the set from the source of truth
// and scanning the real source keeps the guard honest.
import { describe, it, expect, beforeEach, afterEach } from "vitest";
import { readFileSync, readdirSync } from "node:fs";
import { resolve } from "node:path";

const TOKENS_CSS = readFileSync(
  resolve(process.cwd(), "src/theme/tokens.css"),
  "utf8",
);

// A stand-in for Tailwind's emitted utilities. In the real app the arbitrary
// value `bg-white/[0.04]` compiles to a white-overlay rule; here we reproduce
// that base rule (specificity (0,1,0)) so the tokens.css inversion
// (:root[data-scheme="light"] …, specificity (0,3,0)) demonstrably overrides it
// in light scheme and leaves it alone in dark scheme. The variant-prefixed and
// divide forms get the same treatment so their inversion is asserted on
// computed colour, not just on selector presence.
const TAILWIND_BASE = `
[class~="bg-white/[0.04]"] { background-color: rgba(255, 255, 255, 0.04); }
[class~="bg-white/[0.02]"] { background-color: rgba(255, 255, 255, 0.02); }
[class~="border-white/[0.06]"] { border-color: rgba(255, 255, 255, 0.06); }
[class~="border-white/[0.18]"] { border-color: rgba(255, 255, 255, 0.18); }
[class~="data-[state=active]:bg-white/[0.08]"][data-state="active"] { background-color: rgba(255, 255, 255, 0.08); }
[class~="divide-white/[0.04]"] > :not([hidden]) ~ :not([hidden]) { border-color: rgba(255, 255, 255, 0.04); }
[class~="divide-white/5"] > :not([hidden]) ~ :not([hidden]) { border-color: rgba(255, 255, 255, 0.05); }
[class~="data-[state=unchecked]:bg-white/10"][data-state="unchecked"] { background-color: rgba(255, 255, 255, 0.1); }
`;

// Walk desktop/src collecting every source module that can carry a class token,
// skipping test files (their assertions are not rendered overlays) and the
// node_modules/__tests__ subtrees.
function collectSourceFiles(dir: string): string[] {
  const out: string[] = [];
  for (const entry of readdirSync(dir, { withFileTypes: true })) {
    const p = resolve(dir, entry.name);
    if (entry.isDirectory()) {
      if (entry.name === "node_modules" || entry.name === "__tests__") continue;
      out.push(...collectSourceFiles(p));
    } else if (
      /\.(tsx|ts|jsx|js)$/.test(entry.name) &&
      !/\.test\.(tsx|ts|jsx|js)$/.test(entry.name)
    ) {
      out.push(p);
    }
  }
  return out;
}

function injectCss(css: string): void {
  const style = document.createElement("style");
  style.textContent = css;
  document.head.appendChild(style);
}

function setScheme(scheme: "light" | "dark" | null): void {
  if (scheme === null) document.documentElement.removeAttribute("data-scheme");
  else document.documentElement.setAttribute("data-scheme", scheme);
}

function bgColor(className: string, attrs: Record<string, string> = {}): string {
  const el = document.createElement("div");
  el.className = className;
  for (const [k, v] of Object.entries(attrs)) el.setAttribute(k, v);
  document.body.appendChild(el);
  const color = window.getComputedStyle(el).backgroundColor;
  el.remove();
  return color;
}

function borderColor(className: string): string {
  const el = document.createElement("div");
  el.className = className;
  el.style.borderStyle = "solid";
  el.style.borderWidth = "1px";
  document.body.appendChild(el);
  const color = window.getComputedStyle(el).borderTopColor;
  el.remove();
  return color;
}

// A divide utility colours the CHILD row separators, so measure the second
// child's border-top colour, which is what `> :not([hidden]) ~ :not([hidden])`
// targets in the emitted CSS.
function divideColor(className: string): string {
  const container = document.createElement("div");
  container.className = className;
  const first = document.createElement("div");
  const second = document.createElement("div");
  container.appendChild(first);
  container.appendChild(second);
  document.body.appendChild(container);
  const color = window.getComputedStyle(second).borderTopColor;
  container.remove();
  return color;
}

beforeEach(() => {
  document.head.innerHTML = "";
  document.body.innerHTML = "";
  setScheme(null);
  injectCss(TOKENS_CSS);
  injectCss(TAILWIND_BASE);
});

afterEach(() => {
  document.head.innerHTML = "";
  document.body.innerHTML = "";
  setScheme(null);
});

describe("light-scheme arbitrary-value overlay inversion", () => {
  it("loads tokens.css from disk (not an empty css import)", () => {
    // Trap #2: a css raw import yields "" under vitest; node:fs must be used.
    expect(TOKENS_CSS.length).toBeGreaterThan(1000);
  });

  // Trap #3: derive the covered overlay set straight out of tokens.css instead
  // of restating it. `[class~="x"]` is the exact selector form the inversion
  // layer uses, so every covered token — plain-fraction AND arbitrary-value —
  // shows up here without a hand-maintained copy that can drift.
  const COVERED_OVERLAYS = new Set(
    Array.from(TOKENS_CSS.matchAll(/\[class~="([^"]+)"\]/g), (m) => m[1]),
  );

  it("reads a non-empty covered set out of tokens.css", () => {
    // Guard the guard: an empty covered set would flag every overlay (noisy)
    // and, worse, silently change what this test means.
    expect(COVERED_OVERLAYS.size).toBeGreaterThan(10);
    expect(COVERED_OVERLAYS.has("bg-white/[0.04]")).toBe(true);
  });

  it("declares inversion rules for every white overlay used in desktop/src", () => {
    // Scope to white overlays only: black overlays (e.g. bg-black/[0.18])
    // already read on the light background and are deliberately not inverted.
    // Matches both arbitrary-value (bg-white/[0.04]) and plain-fraction
    // (bg-white/5, hover:bg-white/20, divide-white/5) forms.
    const WHITE_OVERLAY_RE =
      /(?:^|:)(bg|text|border|ring|outline|shadow|divide|from|via|to)-white\/(?:\d+|\[[^\]]*\])/;

    const srcFiles = collectSourceFiles(resolve(process.cwd(), "src"));
    expect(srcFiles.length).toBeGreaterThan(100);

    const offenders = new Set<string>();
    for (const file of srcFiles) {
      const src = readFileSync(file, "utf8");
      // Strip comments first: prose about `bg-white/[0.04]` is not a rendered
      // overlay, and the file documents the very classes it avoids.
      const code = src
        .replace(/\/\*[\s\S]*?\*\//g, " ")
        .replace(/(^|[^:])\/\/[^\n]*/g, "$1 ");
      // Every whitespace/quote-delimited class token in the file, so an overlay
      // added anywhere in the markup — including under a variant prefix like
      // `data-[state=active]:` — is seen.
      for (const tok of code.split(/[\s"'`{}()]+/)) {
        if (WHITE_OVERLAY_RE.test(tok) && !COVERED_OVERLAYS.has(tok)) {
          offenders.add(tok);
        }
      }
    }
    expect(Array.from(offenders)).toEqual([]);
  });

  it("maps each hover token to its inverted colour on :hover", () => {
    // jsdom cannot apply :hover for getComputedStyle, so assert the exact
    // declaration value inside the :hover rule — selector AND mapped colour —
    // rather than only that the token's selector appears somewhere in the css.
    // A regression that maps hover:bg-white/[0.06] to rgba(0, 0, 0, 0.05)
    // would pass the selector-presence loop above but fail here.
    const normalized = TOKENS_CSS.replace(/\s+/g, " ");
    const hoverRules: Array<[string, string, string]> = [
      ["hover:bg-white/[0.03]", "background-color", "rgba(0, 0, 0, 0.03)"],
      ["hover:bg-white/[0.04]", "background-color", "rgba(0, 0, 0, 0.04)"],
      ["hover:bg-white/[0.05]", "background-color", "rgba(0, 0, 0, 0.05)"],
      ["hover:bg-white/[0.06]", "background-color", "rgba(0, 0, 0, 0.06)"],
      ["hover:bg-white/[0.08]", "background-color", "rgba(0, 0, 0, 0.08)"],
      ["hover:bg-white/[0.1]", "background-color", "rgba(0, 0, 0, 0.1)"],
      ["hover:border-white/[0.06]", "border-color", "rgba(0, 0, 0, 0.06)"],
      ["hover:bg-white/3", "background-color", "rgba(0, 0, 0, 0.03)"],
      ["hover:bg-white/8", "background-color", "rgba(0, 0, 0, 0.06)"],
      ["hover:bg-white/15", "background-color", "rgba(0, 0, 0, 0.08)"],
      ["hover:bg-white/20", "background-color", "rgba(0, 0, 0, 0.10)"],
      ["hover:border-white/15", "border-color", "rgba(0, 0, 0, 0.14)"],
      ["hover:border-white/20", "border-color", "rgba(0, 0, 0, 0.16)"],
      ["hover:text-white/40", "color", "rgba(0, 0, 0, 0.45)"],
      ["hover:text-white/50", "color", "rgba(0, 0, 0, 0.50)"],
    ];
    for (const [token, property, value] of hoverRules) {
      const rule = `[class~="${token}"]:hover { ${property}: ${value}; }`;
      expect(
        normalized,
        `hover rule missing or mis-mapped for ${token}`,
      ).toContain(rule);
    }
  });

  it("inverts the computed background across schemes (bg-white/[0.04])", () => {
    setScheme("dark");
    const dark = bgColor("bg-white/[0.04]");
    setScheme("light");
    const light = bgColor("bg-white/[0.04]");
    // Dark keeps the additive white overlay; light flips to subtractive black.
    expect(dark).toBe("rgba(255, 255, 255, 0.04)");
    expect(light).toBe("rgba(0, 0, 0, 0.04)");
    expect(light).not.toBe(dark);
  });

  it("inverts the computed border across schemes (border-white/[0.06])", () => {
    setScheme("dark");
    const dark = borderColor("border-white/[0.06]");
    setScheme("light");
    const light = borderColor("border-white/[0.06]");
    expect(dark).toBe("rgba(255, 255, 255, 0.06)");
    expect(light).toBe("rgba(0, 0, 0, 0.06)");
    expect(light).not.toBe(dark);
  });

  it("inverts every arbitrary background value 1:1", () => {
    const cases: Array<[string, string]> = [
      ["bg-white/[0.01]", "rgba(0, 0, 0, 0.01)"],
      ["bg-white/[0.02]", "rgba(0, 0, 0, 0.02)"],
      ["bg-white/[0.03]", "rgba(0, 0, 0, 0.03)"],
      ["bg-white/[0.04]", "rgba(0, 0, 0, 0.04)"],
      ["bg-white/[0.05]", "rgba(0, 0, 0, 0.05)"],
      ["bg-white/[0.06]", "rgba(0, 0, 0, 0.06)"],
      ["bg-white/[0.07]", "rgba(0, 0, 0, 0.07)"],
      ["bg-white/[0.08]", "rgba(0, 0, 0, 0.08)"],
      ["bg-white/[0.1]", "rgba(0, 0, 0, 0.1)"],
    ];
    setScheme("light");
    for (const [className, expected] of cases) {
      expect(bgColor(className), className).toBe(expected);
    }
  });

  it("inverts every plain-fraction background value 1:1", () => {
    const cases: Array<[string, string]> = [
      ["bg-white/3", "rgba(0, 0, 0, 0.03)"],
      ["bg-white/5", "rgba(0, 0, 0, 0.04)"],
      ["bg-white/8", "rgba(0, 0, 0, 0.05)"],
      ["bg-white/10", "rgba(0, 0, 0, 0.06)"],
      ["bg-white/15", "rgba(0, 0, 0, 0.08)"],
      ["bg-white/20", "rgba(0, 0, 0, 0.1)"],
    ];
    setScheme("light");
    for (const [className, expected] of cases) {
      expect(bgColor(className), className).toBe(expected);
    }
  });

  describe("light-scheme bg-white/N plain-fraction alpha scales stay strictly increasing", () => {
    // N = 5, 8, 10, 15, 20 are the plain-fraction white-overlay steps emitted by
    // Tailwind. Their light-scheme inverted alphas must climb without ties or
    // backsteps on BOTH the base scale and the hover mirror: /8 sits between /5
    // and /10, so it must not collide with a neighbour or exceed /10. The base
    // scale had /8 = 0.08 (tied /15, exceeded /10); the hover mirror then had
    // /8 = 0.05 (tied /5). The two scales use different alpha ramps, so each is
    // asserted against its own expected values.
    const steps = [5, 8, 10, 15, 20] as const;
    const cases: Array<[string, RegExp, number[]]> = [
      [
        "base",
        /\[class~="bg-white\/(\d+)"\]\s*\{[^}]*background-color:\s*rgba\(\s*0,\s*0,\s*0,\s*([\d.]+)\s*\)/g,
        [0.04, 0.05, 0.06, 0.08, 0.1],
      ],
      [
        "hover",
        /\[class~="hover:bg-white\/(\d+)"\]:hover\s*\{[^}]*background-color:\s*rgba\(\s*0,\s*0,\s*0,\s*([\d.]+)\s*\)/g,
        [0.05, 0.06, 0.07, 0.08, 0.1],
      ],
    ];
    for (const [label, re, expected] of cases) {
      it(`${label} scale`, () => {
        const alphaOf: Record<number, number> = {};
        for (const m of TOKENS_CSS.matchAll(re)) {
          alphaOf[Number(m[1])] = parseFloat(m[2]);
        }
        for (const n of steps) {
          expect(alphaOf[n], `${label} bg-white/${n} has no light-scheme rule`).toBeDefined();
        }
        const vals = steps.map((n) => alphaOf[n]);
        expect(vals).toEqual(expected);
        for (let i = 0; i < vals.length - 1; i++) {
          expect(
            vals[i],
            `${label} bg-white/${steps[i]} alpha ${vals[i]} must be less than bg-white/${steps[i + 1]} alpha ${vals[i + 1]}`,
          ).toBeLessThan(vals[i + 1]);
        }
      });
    }
  });

  it("inverts every arbitrary border value 1:1", () => {
    const cases: Array<[string, string]> = [
      ["border-white/[0.04]", "rgba(0, 0, 0, 0.04)"],
      ["border-white/[0.06]", "rgba(0, 0, 0, 0.06)"],
      ["border-white/[0.08]", "rgba(0, 0, 0, 0.08)"],
      ["border-white/[0.18]", "rgba(0, 0, 0, 0.18)"],
    ];
    setScheme("light");
    for (const [className, expected] of cases) {
      expect(borderColor(className), className).toBe(expected);
    }
  });

  it("inverts every plain-fraction border value 1:1", () => {
    const cases: Array<[string, string]> = [
      ["border-white/5", "rgba(0, 0, 0, 0.08)"],
      ["border-white/8", "rgba(0, 0, 0, 0.1)"],
      ["border-white/10", "rgba(0, 0, 0, 0.12)"],
      ["border-white/15", "rgba(0, 0, 0, 0.14)"],
      ["border-white/20", "rgba(0, 0, 0, 0.16)"],
      ["border-white/25", "rgba(0, 0, 0, 0.18)"],
    ];
    setScheme("light");
    for (const [className, expected] of cases) {
      expect(borderColor(className), className).toBe(expected);
    }
  });

  it("inverts the active-tab trigger only when data-state=active, and only in light scheme", () => {
    // The class token `data-[state=active]:bg-white/[0.08]` is carried by every
    // trigger; only the active one sets data-state="active". Dark must keep the
    // white tint, light must flip it, and an inactive trigger must stay clear.
    setScheme("dark");
    const darkActive = bgColor("data-[state=active]:bg-white/[0.08]", {
      "data-state": "active",
    });
    setScheme("light");
    const lightActive = bgColor("data-[state=active]:bg-white/[0.08]", {
      "data-state": "active",
    });
    const lightInactive = bgColor("data-[state=active]:bg-white/[0.08]", {
      "data-state": "inactive",
    });
    expect(darkActive).toBe("rgba(255, 255, 255, 0.08)");
    expect(lightActive).toBe("rgba(0, 0, 0, 0.08)");
    expect(lightInactive).toBe("rgba(0, 0, 0, 0)");
  });

  it("inverts the divide row separators across schemes (divide-white/[0.04])", () => {
    setScheme("dark");
    const dark = divideColor("divide-white/[0.04]");
    setScheme("light");
    const light = divideColor("divide-white/[0.04]");
    expect(dark).toBe("rgba(255, 255, 255, 0.04)");
    expect(light).toBe("rgba(0, 0, 0, 0.04)");
    expect(light).not.toBe(dark);
  });

  it("inverts the computed divide row separators across schemes (divide-white/5)", () => {
    setScheme("dark");
    const dark = divideColor("divide-white/5");
    setScheme("light");
    const light = divideColor("divide-white/5");
    expect(dark).toBe("rgba(255, 255, 255, 0.05)");
    expect(light).toBe("rgba(0, 0, 0, 0.08)");
    expect(light).not.toBe(dark);
  });

  it("inverts the unchecked state only when data-state=unchecked, and only in light scheme", () => {
    setScheme("dark");
    const darkUnchecked = bgColor("data-[state=unchecked]:bg-white/10", {
      "data-state": "unchecked",
    });
    setScheme("light");
    const lightUnchecked = bgColor("data-[state=unchecked]:bg-white/10", {
      "data-state": "unchecked",
    });
    const lightChecked = bgColor("data-[state=unchecked]:bg-white/10", {
      "data-state": "checked",
    });
    expect(darkUnchecked).toBe("rgba(255, 255, 255, 0.1)");
    expect(lightUnchecked).toBe("rgba(0, 0, 0, 0.06)");
    expect(lightChecked).toBe("rgba(0, 0, 0, 0)");
  });
});
