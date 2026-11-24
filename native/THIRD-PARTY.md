# Third-party material in native/

The taOS-written code in this tree is AGPL-3.0-or-later (see [`LICENSE.md`](LICENSE.md)). The items
below are not taOS-written, or are derived from other projects. This is the complete list of files
in the tree that carry third-party text. Shell-specific link-time dependencies and data sources are
in [`shell/THIRD-PARTY.md`](shell/THIRD-PARTY.md).

## Files in the tree

| File | Source | Licence | Where the notice lives |
|---|---|---|---|
| `shell/protocols/ext-foreign-toplevel-list-v1.xml` | wayland-protocols | HPND-style permission notice (as shipped by wayland-protocols) | the `<copyright>` element at the top of the file |
| `shell/protocols/ext-image-capture-source-v1.xml` | wayland-protocols | MIT | the `<copyright>` element at the top of the file |
| `shell/protocols/ext-image-copy-capture-v1.xml` | wayland-protocols | MIT | the `<copyright>` element at the top of the file |
| `shell/frame/tests/vptool/wlr-virtual-pointer-unstable-v1.xml` | wlr-protocols (`unstable/wlr-virtual-pointer-unstable-v1.xml`), Copyright 2019 Josef Gajdusek | MIT | the `<copyright>` element at the top of the file (verbatim upstream copy); used only by the test tool `vptool` |
| `compositor/patches/swayfx-0.6.1-minimize-registration.patch` | a patch written for taOS against swayfx 0.6.1 (swayfx is a fork of sway) | MIT, the licence of swayfx and sway (Copyright 2016-2017 Drew DeVault and contributors). A patch to an MIT project is distributed under that project's licence. | this file and `compositor/README.md`; the patch itself is left byte-identical because `compositor/build.sh` pins its sha256 |

## Fetched at build time, not in the tree

`compositor/build.sh` downloads sha256-pinned source tarballs of wayland, wayland-protocols,
libdrm, pixman, wlroots, scenefx and swayfx (MIT or MIT-style licences). They are built into a
private prefix and are not part of this repository.

## Libraries linked, not copied

Qt 6 and LayerShellQt (LGPL-3.0, dynamically linked), libwayland (MIT), and the Rust crates named in
`Cargo.lock` (each under its own licence; none is vendored here). No library source is copied
into the tree.

## Artwork, fonts, sounds, wallpapers

* Icons: the nine SVG files under `apps/icons/` and `apps/settings/icons/` were drawn for taOS from
  plain shapes (rectangles, paths, strokes) and are AGPL-3.0-or-later like the rest of the tree. They
  are not derived from any third-party icon set.
* Fonts: none are bundled. The UI asks for `Inter`, `Noto Sans` or `DejaVu Sans` from the system.
* Sounds: none.
* Wallpapers and images: none are in this tree. The greeter and session read wallpapers from
  `/usr/share/taos/wallpapers` (or `TAOS_WALLPAPER_DIR`); when no wallpaper is installed they draw
  a plain colour. A wallpaper set, if one is installed, is licensed separately by its own notice.
* Weather data and the WMO code table: see `shell/THIRD-PARTY.md`. `shell/weather/wmo-codes.tsv` is a
  taOS-written mapping from WMO code numbers to icon ids and short English phrases.
