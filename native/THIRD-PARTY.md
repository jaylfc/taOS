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

Qt 6 and LayerShellQt (LGPL-3.0, dynamically linked), libwayland (MIT), libzim and libxapian (GPL-2.0-or-later, dynamically linked, from the distribution; Survival Studio, see `apps/survival/NOTICE`), and the Rust crates named in
`Cargo.lock` (each under its own licence; none is vendored here). No library source is copied
into the tree.

## Artwork, fonts, sounds, wallpapers

* Icons: the SVG application icons under `apps/icons/` and `apps/settings/icons/` were drawn for taOS from
  plain shapes (rectangles, paths, strokes) and are AGPL-3.0-or-later like the rest of the tree. They
  are not derived from any third-party icon set.
* Line icons: `apps/common/qml/Taos/Apps/Glyph.qml` holds path data from the Lucide icon set (ISC), itself a fork of
  Feather (MIT); these shapes ARE derived from third-party work. The two permission notices follow.
* Store artwork (`apps/store/assets/`, details in `apps/store/assets/NOTICE`):
  * `icons/webapp-*.svg` (8 files): glyphs from Simple Icons 13.21.0, CC0 1.0, drawn white on a rounded square in the
    brand colour. CC0 does not waive trademark or brand-guideline rights; the marks only identify the sites they open.
  * `icons/*.png` (47 files, 128 px): app and project icons from the dashboard-icons collection
    (https://github.com/homarr-labs/dashboard-icons, Apache-2.0), plus `hermes-agent.png` from this repository.
    The names and logos belong to their owners and are used only to identify the apps in the Store.
  * `covers/*.jpg` (15 files): the banner art of the web Store (`desktop/public/store-covers` in this repository),
    which are the projects' own promotional pictures or screenshots, converted from webp. They identify the apps and
    are not covered by this tree's licence; each stays under its owner's terms.
* Fonts: the UI asks for `Inter`, `Noto Sans` or `DejaVu Sans` from the system and bundles none, except the Parallax
  theme (`shell/themes/parallax/fonts/`), which carries six TrueType files used under the SIL Open Font License 1.1,
  each family with its licence text beside it (`OFL-<family>.txt`) and listed in `shell/themes/parallax/NOTICE`:
  Chakra Petch (Copyright 2018 The Chakra Petch Project Authors), IBM Plex Mono (Copyright © 2017 IBM Corp. with Reserved Font Name "Plex") and
  Barlow (Copyright 2017 The Barlow Project Authors). The fonts are unmodified.
* Sounds: none.
* Wallpapers: `shell/themes/parallax/wallpaper-ember.png`, `wallpaper-paper.png`, `wallpaper-signal.png` and
  `shell/themes/workbench/wallpaper.png` were generated for the themes from plain shapes and gradients (a polar grid
  and scattered points, a flat gradient) and are part of taOS, AGPL-3.0-or-later. No photograph or third-party image
  is used.
* Other wallpapers: none are in this tree. The greeter and session read wallpapers from
  `/usr/share/taos/wallpapers` (or `TAOS_WALLPAPER_DIR`); when no wallpaper is installed they draw
  a plain colour. A wallpaper set, if one is installed, is licensed separately by its own notice.
* Weather data and the WMO code table: see `shell/THIRD-PARTY.md`. `shell/weather/wmo-codes.tsv` is a
  taOS-written mapping from WMO code numbers to icon ids and short English phrases.

### Lucide and Feather permission notices (Glyph.qml)

    Lucide (ISC) and Feather (MIT) notices for the line icons in Taos.Apps Glyph.qml
    
      Glyph.qml holds path data taken from, or traced on, the Lucide icon set (https://lucide.dev), which is itself a fork
      of Feather. Some of the shapes are therefore derived from third-party work, not drawn for taOS from scratch.
    
      ISC License (Lucide)
      Copyright (c) for portions of Lucide are held by Cole Bemis 2013-2022 as part of Feather (MIT).
      All other copyright (c) for Lucide are held by Lucide Contributors 2022.
      Permission to use, copy, modify, and/or distribute this software for any purpose with or without fee is hereby
      granted, provided that the above copyright notice and this permission notice appear in all copies.
      THE SOFTWARE IS PROVIDED "AS IS" AND THE AUTHOR DISCLAIMS ALL WARRANTIES WITH REGARD TO THIS SOFTWARE INCLUDING ALL
      IMPLIED WARRANTIES OF MERCHANTABILITY AND FITNESS. IN NO EVENT SHALL THE AUTHOR BE LIABLE FOR ANY SPECIAL, DIRECT,
      INDIRECT, OR CONSEQUENTIAL DAMAGES OR ANY DAMAGES WHATSOEVER RESULTING FROM LOSS OF USE, DATA OR PROFITS, WHETHER IN
      AN ACTION OF CONTRACT, NEGLIGENCE OR OTHER TORTIOUS ACTION, ARISING OUT OF OR IN CONNECTION WITH THE USE OR
      PERFORMANCE OF THIS SOFTWARE.
    
      MIT License (Feather)
      Copyright (c) 2013-2023 Cole Bemis
      Permission is hereby granted, free of charge, to any person obtaining a copy of this software and associated
      documentation files (the "Software"), to deal in the Software without restriction, including without limitation the
      rights to use, copy, modify, merge, publish, distribute, sublicense, and/or sell copies of the Software, and to
      permit persons to whom the Software is furnished to do so, subject to the following conditions: The above copyright
      notice and this permission notice shall be included in all copies or substantial portions of the Software.
      THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR IMPLIED, INCLUDING BUT NOT LIMITED TO THE
      WARRANTIES OF MERCHANTABILITY, FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE AUTHORS OR
      COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR
      OTHERWISE, ARISING FROM, OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE SOFTWARE.
