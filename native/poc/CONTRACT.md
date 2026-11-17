# Native toolkit proof of concept: shared contract

Two apps, `gtk/` and `qt/`, show the SAME content and expose the SAME hooks, so
one harness can run and measure both. Research notes: `SPEC-research.md`.

## Content (identical in both)
- Layer-shell TOP BAR, 48 logical px high, anchored top + left + right, with an
  exclusive zone. Clock text starts at x >= 84 (camera cut-out keep-out is
  x 0..80, y 0..48). Battery text right-aligned, 16 px from the right edge.
  Bar namespace/scope: `taos-poc-bar`.
- MAIN WINDOW, title `taos-poc-gtk` / `taos-poc-qt`. Back to front:
  1. wallpaper: `wallpaper.png` (path from env `POC_WALLPAPER`, default
     `../common/wallpaper.png` relative to the executable's source dir), scaled
     to cover the window.
  2. a control row at the top, 56 px high, 16 px side margins: a single-line
     text entry (placeholder "Type here"), a button "Card", a button "Theme".
  3. a kinetic-scrolling list of 500 rows under the control row. Each row is
     56 px high: a 24 px icon (any stock glyph), a title "Row N" (16 px) and a
     subtitle "Second line for row N" (13 px). 16 px side margins. The list
     background is the theme surface at 70% opacity so the wallpaper shows.
  4. a CARD, 16 px side margins, 320 px high, radius 16, anchored 120 px above
     the bottom edge. Translucent surface tint over a BLUR of the wallpaper
     behind it. "Card" toggles it with a spring animation (it slides up from
     below the bottom edge and back). It contains one line of text.
- Theme: dark = ground #0E0E10, surface #17171A, text #F2F2F4; light = ground
  #F6F6F8, surface #FFFFFF, text #1C1C1E. "Theme" switches at runtime.

## Hooks (environment variables, identical in both)
- `POC_T0_NS`: CLOCK_MONOTONIC nanoseconds taken by the launcher just before
  exec. At the FIRST presented frame the app prints to stderr exactly:
  `coldstart_ms=<float>` (now - POC_T0_NS). If unset, print `coldstart_ms=na`.
- `POC_FRAMELOG=<file>`: append one line per presented frame of the MAIN
  window: the interval in microseconds (integer) since the previous presented
  frame, CLOCK_MONOTONIC. GTK: the frame clock's after-paint, only on cycles
  whose `paint` signal fired (a cycle that drew nothing is not a frame); Qt:
  QQuickWindow::frameSwapped. First frame writes nothing.
- `POC_NO_BLUR=1`: the card is the same translucent surface with NO blur pass.
- `POC_BLUR_RADIUS=<px>`: blur radius, default 24.
- `POC_AUTOCARD=<n>`: after 2 s, toggle the card n times, one toggle per
  1200 ms, then keep running. For measuring the animation without touch.
- `POC_AUTOSCROLL=<n>`: after 2 s, scroll the list programmatically from top to
  bottom and back n times at a constant 3000 px/s. A fallback when touch
  injection is not available; real flings are injected from outside.
- `POC_AUTOQUIT_MS=<ms>`: quit after that long.
- On start print to stderr exactly one line `renderer=<name>` naming the
  renderer/graphics backend actually in use (GTK: the GSK renderer type name;
  Qt: the scene graph RHI backend name), and `toolkit=<gtk x.y.z|qt x.y.z>`
  with the RUNTIME library version.

## Launcher
`common/poc-launch <binary> [args...]`: a tiny C program (so it starts in
microseconds on both bases) that reads CLOCK_MONOTONIC, sets `POC_T0_NS`, and
execs the binary.

## Build
Each app has `build.sh <alpine|debian>` that builds inside a container on an
arm64 host and leaves the binary in `out/<base>/`. Dependencies are vendored or
pinned; nothing is downloaded at run time.
