### Fixed
- The server-rendered sign-in (`/auth/login`) and first-run setup (`/auth/setup`)
  pages now draw the taOS brand mark as an inline SVG instead of a bare Unicode
  glyph (`⌗` on login, `✦` on setup). Those code points are host-font-dependent
  and rendered as a missing-glyph box (TOFU) on machines whose installed fonts
  lack coverage, while these pages are deliberately JS-free and CDN-free so they
  work on any device. The SVG uses `currentColor` and scales to its `.icon`
  container, so no external asset or webfont fetch is required (#2525).
