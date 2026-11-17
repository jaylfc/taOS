# Native PoC spec (GTK4/Rust vs Qt6/QML)

Date 2026-11-17. Sections A-G complete; UNVERIFIED items are collected at the end. Builds on the two prior reports (distro-20261116/native-toolkit.md, opus-rerun/native-toolkit-opus.md) without repeating their survey.

## A. GTK/Rust pin set

Sources: crates.io API `https://crates.io/api/v1/crates/<name>/versions` and the crate sources `https://static.crates.io/crates/<name>/<name>-<ver>.crate` (Cargo.toml `[features]`, `rust-version`, `package.metadata.system-deps`), read 2026-11-17.

| crate | version | rust-version (crates.io) | edition | binds | cap features present |
|---|---|---|---|---|---|
| gtk4 | 0.9.6 (= Debian librust-gtk4-dev 0.9.6) | 1.70 | 2021 | glib/gio 0.20, gdk4 0.9 | `v4_18` exists (gtk4-sys: pkg-config `gtk4 >= 4.17`) |
| gtk4 | 0.10.3 | 1.83 | 2021 | glib/gio 0.21 | `v4_18` (needs `gtk4 >= 4.18`), up to `v4_20` |
| gtk4 | 0.11.5 (newest, 2026-09-20) | 1.92 | 2024 | glib/gio 0.22 | `v4_18`, up to `v4_24` |
| libadwaita | 0.7.2 (= Debian) | not declared | 2021 | gtk4 0.9 | `v1_7` exists (needs `libadwaita-1 >= 1.7`) |
| libadwaita | 0.8.1 | not declared | 2021 | gtk4 0.10 | `v1_7`, up to `v1_9` |
| libadwaita | 0.9.2 (newest, 2026-07-07) | not declared (edition 2024; gtk4 0.11 forces 1.92) | 2024 | gtk4 0.11 | `v1_7`, up to `v1_10` |
| gtk4-layer-shell | 0.4.0 (= Debian) | 1.70 | 2021 | gtk4 0.9, -sys 0.3.0 | none |
| gtk4-layer-shell | 0.7.1 | 1.70 | 2021 | gtk4 0.10, -sys 0.5 | opt-in `v1_3` |
| gtk4-layer-shell | 0.8.1 (newest, 2026-08-10) | 1.85 | 2024 | gtk4 0.11, -sys 0.6 | opt-in `v1_3` |

C library: every gtk4-layer-shell-sys (0.3.0/0.4.0/0.5.2/0.6.1) asks pkg-config for `gtk4-layer-shell-0 >= 1`; only the opt-in `v1_3` feature raises that to 1.3 (it adds `gtk_layer_{set,get}_respect_close`, which the C CHANGELOG lists under 1.3.0: https://github.com/wmww/gtk4-layer-shell/blob/main/CHANGELOG.md). No crate binds session lock. **Every listed layer-shell crate works with C 1.0.4 provided `v1_3` stays off.**
API break to know: `LayerShell::set_namespace(&str)` (0.4.0) became `set_namespace(Option<&str>)` (0.7.1, 0.8.1).

Toolchain fact: Debian **trixie-backports** carries `rustc` and `cargo` 1.96.1+dfsg1-2~bpo13+1 for amd64 and arm64 (https://api.ftp-master.debian.org/madison?package=rustc&text=on and `?package=cargo`). A >= 1.92 toolchain is therefore a Debian package, no rustup required. Alpine edge has rust 1.100.0.

**Compile check (done for this spec, 2026-11-17):** the section-B program (layer-shell bar, ListView, blur widget subclass, spring, StyleManager, frame clock, renderer query) was `cargo check`ed against fake `.pc` files carrying trixie's versions (gtk4 4.18.6, libadwaita-1 1.7.6, gtk4-layer-shell-0 1.0.4). Results, zero warnings each:
- (ii) gtk4 0.9.6 + libadwaita 0.7.2 + gtk4-layer-shell 0.4.0 on **rustc 1.85.x**: OK (resolver 3 held `target-lexicon` back to an MSRV-compatible version).
- (iii) gtk4 0.10.3 + libadwaita 0.8.1 + gtk4-layer-shell 0.7.1 on **rustc 1.85.x**: OK.
- (i) gtk4 0.11.5 + libadwaita 0.9.2 + gtk4-layer-shell 0.8.1 on rustc 1.100: OK. (Not run on 1.92 or 1.96 itself.)
Limit: `cargo check` does not link, so symbol availability at link/run time on the real 4.18/1.7/1.0.4 libraries is not proven; the feature caps are what guarantee no newer symbol is referenced.

**Recommendation: (i), newest crates, toolchain floor 1.92.** Debian builds use trixie-backports `rustc`/`cargo` 1.96.1, or rustup with `rust-toolchain.toml` `channel = "1.96.1"`. Alpine builds use the system rust 1.100, which ignores `rust-toolchain.toml` unless rustup is installed. Reasons:
1. 0.9.x is a dead branch: its last release was 0.9.7 on 2025-06-21. If GTK wins, the code moves to 0.11 anyway, and the 0.9 to 0.11 port is not free. Two breaks are known: the `set_namespace` signature change, and from 0.10 the `@implements gtk::Accessible, gtk::Buildable, gtk::ConstraintTarget` that `glib::wrapper!` must now carry. The 0.10/0.11 compile failed without it.
2. The toolchain objection to (i) is gone because Debian packages 1.96.1 in backports.
3. The feature caps `v4_18` + `v1_7` hold the API to the trixie floor, so the newer bindings cost nothing in reach.
Fallback: (ii) is verified to check on 1.85. Use it only if the PoC must build with stock trixie `cargo` and no backports. (iii) also checks on 1.85, and it is the better 1.85 choice because it is closer to 0.11's API.

```toml
[package]
name = "poc-gtk"
version = "0.1.0"
edition = "2024"
rust-version = "1.92"

[dependencies]
gtk = { package = "gtk4", version = "=0.11.5", features = ["v4_18"] }
adw = { package = "libadwaita", version = "=0.9.2", features = ["v1_7"] }
gtk4-layer-shell = "=0.8.1"          # do NOT enable "v1_3" (trixie has C 1.0.4)
```
Fallback (ii) block: `gtk = { package = "gtk4", version = "=0.9.6", features = ["v4_18"] }`, `adw = { package = "libadwaita", version = "=0.7.2", features = ["v1_7"] }`, `gtk4-layer-shell = "=0.4.0"`, with `edition = "2021"`, `rust-version = "1.85"`, `resolver = "3"` in `[package]`. Commit `Cargo.lock` and build with `--locked` on both bases.

## B. GTK API shapes (pinned set (i), with `v4_18`/`v1_7` caps)

The Rust fragments below come from a program that `cargo check`ed cleanly with zero warnings, for (i) 0.11.5/0.9.2/0.8.1 and for (ii)/(iii) with the `set_namespace` difference. The B5 provider swap was checked on (i) only. The B3 `Picture` split and the B2 CSS reset were added afterwards and were not checked. The full checked file is `gtkpoc/v-i/src/main.rs` in the scratchpad next to this spec. Imports: `use adw::prelude::*; use gtk::{gdk, glib, graphene, gsk, subclass::prelude::*}; use gtk4_layer_shell::{Edge, Layer, LayerShell};`

**B1. Layer-shell bar.** Call `init_layer_shell()` before the window is realized, which means before `present()`. The crate doc says it "must be called before the `window` is realized" (gtk4-layer-shell 0.8.1 `src/auto/functions.rs`, `init_for_window`). The size comes from `set_default_size`. With left and right anchored, width 0 means "stretch".
```rust
let bar = gtk::ApplicationWindow::new(app);
bar.init_layer_shell();
bar.set_layer(Layer::Top);
for e in [Edge::Top, Edge::Left, Edge::Right] { bar.set_anchor(e, true); }
bar.auto_exclusive_zone_enable();          // or bar.set_exclusive_zone(48)
bar.set_namespace(Some("poc-bar"));        // 0.4.0: bar.set_namespace("poc-bar")
bar.set_default_size(0, 48);
let row = gtk::CenterBox::new();
let clock = gtk::Label::new(Some("12:00")); clock.set_margin_start(84);   // clears the 0..80 cut-out
let batt  = gtk::Label::new(Some("87%"));   batt.set_margin_end(12);
row.set_start_widget(Some(&clock)); row.set_end_widget(Some(&batt));
bar.set_child(Some(&row));
bar.present();
```
Link order: the dynamic linker must load `libgtk4-layer-shell.so` before `libwayland-client.so`, because the library shims libwayland symbols (https://github.com/wmww/gtk4-layer-shell/blob/main/linking.md). Check `ldd target/release/poc-gtk` and confirm the layer-shell library comes before libwayland. If it does not, use `LD_PRELOAD=libgtk4-layer-shell.so.0` as the fallback that doc gives. Nobody has checked this for a Rust binary (UNVERIFIED).

**B2. 500-row list.** Use `StringList`, `NoSelection`, `SignalListItemFactory`, `ListView` and `ScrolledWindow`. Kinetic scrolling is the `ScrolledWindow` `kinetic-scrolling` property, set explicitly here. Its default value is UNVERIFIED. Row height: GTK/Adwaita theme CSS may add padding to `listview > row`. Load `listview > row { padding: 0; min-height: 0; }` in the app provider and confirm 56 px in the GTK inspector (`GTK_DEBUG=interactive`). The computed height is UNVERIFIED.
```rust
let model = gtk::StringList::new(&[]);
for i in 0..500 { model.append(&format!("Row {i}")); }
let factory = gtk::SignalListItemFactory::new();
factory.connect_setup(|_, obj| {
    let item = obj.downcast_ref::<gtk::ListItem>().unwrap();
    let hb = gtk::Box::new(gtk::Orientation::Horizontal, 12);
    hb.set_height_request(56); hb.set_margin_start(16); hb.set_margin_end(16);
    let vb = gtk::Box::new(gtk::Orientation::Vertical, 0); vb.set_valign(gtk::Align::Center);
    vb.append(&gtk::Label::new(None)); vb.append(&gtk::Label::new(None));
    hb.append(&gtk::Image::from_icon_name("folder-symbolic")); hb.append(&vb);
    item.set_child(Some(&hb));
});
factory.connect_bind(|_, obj| {
    let item = obj.downcast_ref::<gtk::ListItem>().unwrap();
    let s  = item.item().and_downcast::<gtk::StringObject>().unwrap();
    let vb = item.child().and_downcast::<gtk::Box>().unwrap().last_child().and_downcast::<gtk::Box>().unwrap();
    vb.first_child().and_downcast::<gtk::Label>().unwrap().set_text(&s.string());
    vb.last_child().and_downcast::<gtk::Label>().unwrap().set_text("secondary line");
});
let lv = gtk::ListView::new(Some(gtk::NoSelection::new(Some(model))), Some(factory));
let sw = gtk::ScrolledWindow::new();
sw.set_kinetic_scrolling(true); sw.set_hscrollbar_policy(gtk::PolicyType::Never);
sw.set_child(Some(&lv)); sw.set_vexpand(true);
```

**B3. Blur options in GTK.**
| method | 4.18 (trixie) | 4.24 (Alpine) | what it blurs |
|---|---|---|---|
| CSS `filter: blur(Npx)` | yes (property registered in `gtk/gtkcssstylepropertyimpl.c` at tag 4.18.6) | yes | the widget's **own** rendering and its children. It does not blur what lies behind it. |
| CSS `backdrop-filter` | **no**: the property is absent at 4.18.6 and 4.20.0. An unknown property only gets a CSS parse warning. | yes: NEWS 4.21.2 says "the backdrop-filter property is supported", and fixes followed in 4.21.3/4.21.4 (https://gitlab.gnome.org/GNOME/gtk/-/raw/4.24.1/NEWS) | what is behind the widget in the same window |
| custom widget, `snapshot.push_blur(r)` around an appended texture (GskBlurNode) | yes | yes | whatever you append inside the push/pop |

**Answer:** the floor is 4.18, and `backdrop-filter` does not exist there. The only method that gives "blurred wallpaper behind a translucent card" on both bases is a custom widget that draws the wallpaper texture a second time inside `push_blur`, clipped to the card's rounded rect. Using `backdrop-filter` on Alpine only would fork the code (see G).
```rust
mod imp { use super::*;
  #[derive(Default)] pub struct BlurScene { pub texture: RefCell<Option<gdk::Texture>>, pub card_y: Cell<f32>, pub blur_on: Cell<bool> }
  #[glib::object_subclass] impl ObjectSubclass for BlurScene {
      const NAME: &'static str = "PocBlurScene"; type Type = super::BlurScene; type ParentType = gtk::Widget; }
  impl ObjectImpl for BlurScene {}
  impl WidgetImpl for BlurScene { fn snapshot(&self, snapshot: &gtk::Snapshot) {
      let (w, h) = (self.obj().width() as f32, self.obj().height() as f32);
      let full = graphene::Rect::new(0.0, 0.0, w, h);
      let Some(tex) = self.texture.borrow().clone() else { return };
      snapshot.append_texture(&tex, &full);                                   // wallpaper
      let card = graphene::Rect::new(16.0, self.card_y.get(), w - 32.0, 200.0);
      snapshot.push_rounded_clip(&gsk::RoundedRect::from_rect(card, 16.0));
      if self.blur_on.get() {
          let r = 24.0_f64;
          snapshot.push_blur(r);
          snapshot.push_clip(&card.inset_r(-(r as f32) * 2.0, -(r as f32) * 2.0)); // bound blur input
          snapshot.append_texture(&tex, &full);
          snapshot.pop(); snapshot.pop();
      }
      snapshot.append_color(&gdk::RGBA::new(0.09, 0.09, 0.10, 0.55), &card);  // #17171A @ 55%
      snapshot.pop();
  } }
}
glib::wrapper! { pub struct BlurScene(ObjectSubclass<imp::BlurScene>) @extends gtk::Widget,
    @implements gtk::Accessible, gtk::Buildable, gtk::ConstraintTarget; }   // @implements is REQUIRED from gtk4 0.10
```
The wallpaper loads with `gdk::Texture::from_filename(path)`. **Z-order is the same in both PoCs: wallpaper, then list and controls, then card. The card's blur samples the wallpaper only, not the list.** In GTK, drop the first `append_texture(&tex, &full)` from the fragment. Make the `gtk::Overlay` child `gtk::Picture::for_paintable(&tex)` with `set_content_fit(gtk::ContentFit::Fill)`, so it maps 1:1 to `full`. Add the list and controls box as the first overlay and the `BlurScene` as the last overlay, with `set_can_target(false)` so touches reach the list. The no-blur control sets `blur_on` from `POC_NO_BLUR`. The inner `push_clip` is there so the blur does not process the whole window. Whether GSK already culls that work is UNVERIFIED, so measure both variants.

**B4. Spring (libadwaita >= 1.0 API).**
```rust
let s2 = scene.clone();
let target = adw::CallbackAnimationTarget::new(move |v| { s2.imp().card_y.set(v as f32); s2.queue_draw(); });
let anim = adw::SpringAnimation::new(&scene, 900.0, 300.0, adw::SpringParams::new(0.7, 1.0, 300.0), target);
// on press: anim.set_value_from(anim.value()); anim.set_value_to(if show {300.0} else {900.0}); anim.play();
```
`SpringParams::new(damping_ratio, mass, stiffness)`.

**B5. Runtime dark/light.** `adw::StyleManager::default().set_color_scheme(adw::ColorScheme::ForceDark | ForceLight)`, read back with `is_dark()`. Custom tokens: the CSS `@media (prefers-color-scheme: dark)` query is **not** in 4.18.6. It appears in `gtk/gtkcssprovider.c` at 4.20.0. So swap two `CssProvider`s on `connect_dark_notify` (checked):
```rust
let apply = move |sm: &adw::StyleManager| { let d = gdk::Display::default().unwrap();
    let (on, off) = if sm.is_dark() { (&dark_css, &light_css) } else { (&light_css, &dark_css) };
    gtk::style_context_remove_provider_for_display(&d, off);
    gtk::style_context_add_provider_for_display(&d, on, gtk::STYLE_PROVIDER_PRIORITY_APPLICATION); };
apply(&sm); sm.connect_dark_notify(move |sm| apply(sm));
// dark_css: "window.poc { background:#0E0E10; color:#F2F2F4; font-size:16px } .surface { background:#17171A }"
```
`CssProvider::load_from_string` needs GTK 4.12+, which both bases meet.

**B6. Frame timing and cold start.**
- Shared definition for both toolkits: one line per **frame submitted by the toolkit**. The timestamp is `CLOCK_MONOTONIC` in µs, taken when the toolkit signals end of frame. GTK uses `GdkFrameClock::after-paint`; Qt uses `QQuickWindow::frameSwapped`. The logged value is the difference from the previous line. Neither is a presentation timestamp.
- GTK: connect on realize. `glib::monotonic_time()` is `g_get_monotonic_time`, which is CLOCK_MONOTONIC on Linux and is also the frame clock's timescale.
```rust
win.connect_realize(move |w| { let clock = w.frame_clock().unwrap();
    clock.connect_after_paint(move |clock| { let t = glib::monotonic_time(); /* write t - prev */
        if let Some(tm) = clock.current_timings() { let _pt = tm.presentation_time(); } }); });
```
- `after-paint` is emitted in the AFTER_PAINT phase of **every** frame-clock cycle that is not frozen (`gdk/gdkframeclockidle.c` at 4.18.6, lines 639-643), and that includes cycles with no paint. So during the run there must be **no** `add_tick_callback`, because a tick callback calls `gdk_frame_clock_begin_updating` (`gtk/gtkwidget.c` at 4.18.6) and makes the clock tick every vblank.
- Optional second column: `GdkFrameTimings::presentation_time()`. It is "in the timescale of g_get_monotonic_time(), or 0 if no presentation time is available" (`gdk/gdkframetimings.c` at 4.18.6). It is complete only some frames later, so read an older frame's timings (exact call not compile-checked, UNVERIFIED). Sway 1.12 does create `wlr_presentation` v2 (F), so GTK should get presentation feedback.
- Cold start: one shared launcher stamps CLOCK_MONOTONIC and then execs the app, because a shell cannot read CLOCK_MONOTONIC: `python3 -c 'import os,sys,time; os.environ["POC_T0_US"]=str(time.clock_gettime_ns(time.CLOCK_MONOTONIC)//1000); os.execvp(sys.argv[1], sys.argv[1:])' ./poc-gtk`, and the same command with `./poc-qt`. Python's own startup happens before the stamp, so it is not counted. The app prints `cold_start_ms=` at the first `after-paint` of the **main** window. Without `POC_T0_US`, the app falls back to the time at `main()` entry, which undercounts dynamic-linking time.

**B7. Renderer.** The selection order in `gsk/gskrenderer.c` (4.18.6 and 4.24.1) is: display data, `GSK_RENDERER`, backend, Vulkan, GL, Vulkan-as-fallback, GL-as-fallback, cairo.
- 4.18.6: Vulkan is the default **on Wayland**, provided Vulkan initialises, the device is not `VK_PHYSICAL_DEVICE_TYPE_CPU` and dmabuf formats exist. Otherwise GL. `ngl`/`opengl` select GL silently. `gl` warns "old GL renderer has been removed" and then uses GL.
- 4.24.1: same outcome through `gdk_display_set_prefer_vulkan(TRUE)` in `gdk_wayland_display_init`. `gl`/`opengl` select GL silently. `ngl` warns "renamed to gl".
- Values: `vulkan`, `opengl` (the **only** GL spelling that is silent on both), `cairo`, `help`.
- Debian builds GTK with Vulkan on Linux: `debian/rules` only disables it off-Linux or for the udeb, and `debian/control` build-depends on `libvulkan-dev [linux-any]`. The Alpine APKBUILD build-depends on `vulkan-loader-dev`.
- On the phone, Vulkan means turnip, which needs `mesa-vulkan-freedreno` installed. Without it GTK falls back to GL.
- To learn which renderer was used, print `win.renderer().unwrap().type_().name()` after map. That gives `GskVulkanRenderer` / `GskGLRenderer` / `GskCairoRenderer` (G_DEFINE_TYPE in `gsk/gpu/gskvulkanrenderer.c`, `gsk/gpu/gskglrenderer.c`, `gsk/gskcairorenderer.c`, same at both tags). `GSK_DEBUG=renderer` also prints the "Not using Vulkan: ..." reasons.

**B8. Text input.** Both 4.18.6 and 4.24.1 implement only `zwp_text_input_v3` (`gtk/gtkimcontextwayland.c`).
- Selection order (`gtk/gtkimmodule.c`, same at both tags): `GTK_IM_MODULE` first, then the `gtk-im-module` **setting**, and only then the highest-priority module whose backend matches (`wayland` at priority 100, valid only when `zwp_text_input_manager_v3` is advertised).
- **On Wayland the setting is not empty by default.** The settings fallback table in `gdk/wayland/gdkdisplay-wayland.c`, at 4.18.6 and 4.24.1 (line 1771), maps `org.gnome.desktop.interface gtk-im-module` with default **`"simple"`**. `lookup_immodule` returns `gtk-im-context-simple` for it. So without a portal or gsettings value of `wayland`, GTK uses **simple** and never binds text-input-v3.
- That matches the 2026-06-22 report (https://whynothugo.nl/journal/2026/06/22/input-methods-in-gtk4-apps-on-wayland/).
- **`GTK_IM_MODULE=wayland` is required** in the PoC launcher. Verify it with `WAYLAND_DEBUG=1 2>&1 | grep zwp_text_input`.

## C. Qt/QML shapes (valid on Qt 6.8.2 and 6.12.0)

Nothing in this section has been compiled, because neither host had Qt or docker. The APIs below were checked against tag-pinned upstream source: qtbase, qtwayland and qtdeclarative at `v6.8.2` and `v6.12.0`, from github.com/qt/*, and layer-shell-qt at `v6.3.4` and `v6.8.3`, from invent.kde.org/plasma/layer-shell-qt. Compile it on both bases first.

**C1. CMake (one form for both).** `qt_add_qml_module` and `QQmlApplicationEngine::loadFromModule` are expected to work on both 6.8 and 6.12. `loadFromModule` and the `REQUIRES` argument of `qt_standard_project_setup` both date from 6.5 (UNVERIFIED: from memory, not re-read).
```cmake
cmake_minimum_required(VERSION 3.21)
project(poc-qt LANGUAGES CXX)
find_package(Qt6 6.8 REQUIRED COMPONENTS Gui Qml Quick)
find_package(LayerShellQt REQUIRED)            # LayerShellQtConfig.cmake, both 6.3.4 and 6.8.3
qt_standard_project_setup(REQUIRES 6.8)
qt_add_executable(poc-qt main.cpp)
qt_add_qml_module(poc-qt URI Poc VERSION 1.0
    QML_FILES Main.qml Bar.qml
    RESOURCES wallpaper.jpg)
target_link_libraries(poc-qt PRIVATE Qt6::Gui Qt6::Quick LayerShellQt::Interface)
```
The target name comes from `install(EXPORT LayerShellQtTargets NAMESPACE LayerShellQt::)` plus `EXPORT_NAME Interface` (layer-shell-qt `CMakeLists.txt`/`src/CMakeLists.txt` at both tags). The header is `<LayerShellQt/Window>`, generated by `ecm_generate_headers`. layer-shell-qt 6.3.4 requires Qt >= 6.7.0 and 6.8.3 requires Qt >= 6.11.0 (`QT_MIN_VERSION`).

**C2. main.cpp.** Bar and main window live in one process. The bar must be visible:false in QML and is shown after configuration.
```cpp
#include <QGuiApplication>
#include <QQmlApplicationEngine>
#include <QQuickWindow>
#include <LayerShellQt/Window>
#include <cstdio>
#include <ctime>
static qint64 now_us() { timespec ts; clock_gettime(CLOCK_MONOTONIC, &ts); return qint64(ts.tv_sec) * 1000000 + ts.tv_nsec / 1000; }
int main(int argc, char **argv) {
    const qint64 t0 = qEnvironmentVariableIsSet("POC_T0_US") ? qgetenv("POC_T0_US").toLongLong() : now_us();
    QGuiApplication app(argc, argv);
    QQmlApplicationEngine engine;
    engine.loadFromModule("Poc", "Bar");      // Window { visible: false; height: 48 }
    engine.loadFromModule("Poc", "Main");     // Window { visible: false }
    if (engine.rootObjects().size() != 2) return 1;
    auto *bar = qobject_cast<QQuickWindow *>(engine.rootObjects().at(0));
    auto *win = qobject_cast<QQuickWindow *>(engine.rootObjects().at(1));
    using LSW = LayerShellQt::Window;
    LSW *ls = LSW::get(bar);                                  // before bar->show()
    ls->setLayer(LSW::LayerTop);
    ls->setAnchors(LSW::Anchors(LSW::AnchorTop) | LSW::AnchorLeft | LSW::AnchorRight);
    ls->setExclusiveZone(48);
    ls->setScope(QStringLiteral("poc-bar"));                  // = wlr namespace
    ls->setKeyboardInteractivity(LSW::KeyboardInteractivityNone);
    bar->show();
    FILE *log = qEnvironmentVariableIsSet("POC_FRAMELOG") ? std::fopen(qgetenv("POC_FRAMELOG").constData(), "w") : nullptr;
    qint64 last = 0; bool first = true;
    QObject::connect(win, &QQuickWindow::frameSwapped, win, [&] {   // runs on the render thread
        const qint64 t = now_us();
        if (first) { first = false; std::fprintf(stderr, "cold_start_ms=%.1f
", (t - t0) / 1000.0); }
        if (log) { if (last) std::fprintf(log, "%lld
", (long long)(t - last)); last = t; }
    }, Qt::DirectConnection);
    win->show();
    return app.exec();
}
```
- `Anchors` is declared with `Q_DECLARE_FLAGS`, but the header has **no** `Q_DECLARE_OPERATORS_FOR_FLAGS`. Plain `AnchorTop | AnchorLeft` is therefore an `int` and does not convert to `Anchors`, which is why the fragment wraps the first operand.
- Same API at both tags. `get`, `setLayer`, `setAnchors`, `setExclusiveZone`, `setScope`, `setKeyboardInteractivity` and `setMargins` exist in `src/interfaces/window.h` at v6.3.4 and v6.8.3. Only 6.8.3 adds `setDesiredSize`, `setActivateOnShow`, `setScreen` and `setWantsToBeOnActiveScreen`, so do not use them.
- Behaviour differs between the tags. At 6.3.4, `Window::get()` calls `window->create()` and attaches the layer-shell integration at once. At 6.8.3 it installs an event filter and attaches on `SurfaceCreated`, and it warns if the window is already visible. Calling `get()` and the setters before `show()` is correct for both. `setScope` "must be set before the platform window is created" (comment in 6.3.4 `window.cpp`), and at 6.3.4 `get()` creates it, so read back the namespace with `swaymsg -t get_tree`/`WAYLAND_DEBUG` (UNVERIFIED whether 6.3.4 honours a late scope).
- **Do not set `QT_WAYLAND_SHELL_INTEGRATION=layer-shell`.** Both tags call `waylandWindow->setShellIntegration()` **per window**, so the main window stays xdg-shell. The env var would turn every window into a layer surface.
- On a non-Wayland platform the library only warns ("is not a wayland window").

**C3. Main.qml pieces.** Explicit `import QtQuick.Controls.Basic` pins the Basic style at compile time.
```qml
import QtQuick
import QtQuick.Controls.Basic
import QtQuick.Effects
Window {
    id: root; width: 360; height: 800; visible: false; color: theme.ground
    QtObject { id: theme; property bool dark: true
        readonly property color ground:  dark ? "#0E0E10" : "#F2F2F4"
        readonly property color surface: dark ? "#17171A" : "#FFFFFF"
        readonly property color text:    dark ? "#F2F2F4" : "#0E0E10" }
    Image { id: wall; anchors.fill: parent; source: "wallpaper.jpg"; fillMode: Image.PreserveAspectCrop }
    ListView { id: list; anchors.fill: parent; anchors.topMargin: 120; model: 500; reuseItems: true
        // Flickable defaults kept: flickDeceleration, maximumFlickVelocity, boundsBehavior
        delegate: Item { width: ListView.view.width; height: 56
            Row { x: 16; width: parent.width - 32; spacing: 12; anchors.verticalCenter: parent.verticalCenter
                Rectangle { width: 24; height: 24; radius: 4; color: theme.surface }
                Column { Text { text: "Row " + index; color: theme.text; font.pixelSize: 16 }
                         Text { text: "secondary line"; color: theme.text; font.pixelSize: 14 } } } } }
    Item { id: card; x: 16; width: parent.width - 32; height: 200; y: shown ? 300 : 900
        property bool shown: false
        Behavior on y { SpringAnimation { spring: 4; damping: 0.3 } }
        ShaderEffectSource { id: wallRegion; sourceItem: wall; visible: false
            sourceRect: Qt.rect(card.x, card.y, card.width, card.height) }
        Rectangle { id: mask; anchors.fill: parent; radius: 16; visible: false; layer.enabled: true }
        MultiEffect { anchors.fill: parent; source: wallRegion; visible: !noBlur
            blurEnabled: true; blur: 1.0; blurMax: 32; autoPaddingEnabled: false
            maskEnabled: true; maskSource: mask }
        Rectangle { anchors.fill: parent; radius: 16; color: Qt.rgba(0.09, 0.09, 0.10, 0.55) } }
    Column { x: 16; y: 16; spacing: 8
        TextField { width: 300; placeholderText: "type" }
        Button { text: "Card";  onClicked: card.shown = !card.shown }
        Button { text: "Theme"; onClicked: theme.dark = !theme.dark } }
    readonly property bool noBlur: Qt.application.arguments.indexOf("--no-blur") >= 0
}
```
- **Blur.** `MultiEffect` lives in `QtQuick.Effects`, which is in qtdeclarative, and is LGPL. Its properties are `blurEnabled`, `blur` (0..1), `blurMax` (int, px), `blurMultiplier`, `autoPaddingEnabled`, `maskEnabled` and `maskSource`, per https://doc.qt.io/qt-6.8/qml-qtquick-effects-multieffect.html.
- **Cost.** `ShaderEffectSource` renders `wall`'s `sourceRect` into an **offscreen texture**. `sourceRect` changes every frame while the card springs, so that texture and the blur passes rerun every frame. This is the same per-frame work as the GTK `push_blur` widget, so the comparison is fair. The docs warn that "Blur and shadow are the heaviest effects", that you should prefer a larger `blurMultiplier` over a larger `blurMax`, and that you should avoid animated sources. `blurMax` "causes shader change; do not change this property during animation". The `ShaderEffectSource` pattern for using part of a source item comes from the same page.
- **Spring.** QML `SpringAnimation` (`spring`, `damping`, `mass`, `epsilon`) is not parameterised like `adw::SpringParams` (damping_ratio, mass, stiffness). Pick values so both settle in about 500 ms, and log the settle time.
- **Dark/light.** Use a plain `QtObject` of colour tokens, which needs no Controls style and no GPL module. `QStyleHints::setColorScheme` exists at both tags (`qtbase/src/gui/kernel/qstylehints.h`), but the PoC does not need it.

**C4. Licences.** The SPDX header is `LicenseRef-Qt-Commercial OR LGPL-3.0-only OR GPL-2.0-only OR GPL-3.0-only` at **both** v6.8.2 and v6.12.0 for all of these files:
- `src/effects/qquickmultieffect.cpp`
- the Basic, Material, Fusion, Universal, Imagine and FluentWinUI3 style sources
- `src/quick/items/qquickflickable.cpp`
- `src/quicklayouts/qquicklinearlayout.cpp`
- `src/quickshapes/qquickshape.cpp`

**Qt Virtual Keyboard** is `LicenseRef-Qt-Commercial OR GPL-3.0-only` at both tags (`src/virtualkeyboard/qvirtualkeyboardinputcontext.cpp`). Do not install it, and never set `QT_IM_MODULE=qtvirtualkeyboard`. The Qt Wayland client is LGPL at 6.12 (`qtbase/src/plugins/platforms/wayland/qwaylandwindow.cpp`). Other GPL-only modules (Wayland Compositor, Quick 3D, Graphs and others) are listed at https://doc.qt.io/qt-6/licensing.html. The PoC uses none of them.

**C5. Frame timing / renderer / software control.**
- Frame timing: `QQuickWindow::frameSwapped`, connected with `Qt::DirectConnection` and timestamped with `CLOCK_MONOTONIC`. This matches B6.
- The render loop on Linux is platform-chosen. Force `QSG_RENDER_LOOP=threaded` on both bases so the thread that emits `frameSwapped` is the same (https://doc.qt.io/qt-6.8/qtquick-visualcanvas-scenegraph.html).
- `QSG_INFO=1`, the legacy alias of logging category `qt.scenegraph.general`, prints the graphics API, GL renderer string and render loop at startup.
- `QSG_RHI_BACKEND=opengl|vulkan` forces the RHI API (https://doc.qt.io/qt-6.8/qtquick-visualcanvas-adaptations.html). The Linux default is OpenGL (UNVERIFIED from the docs page; read it from `QSG_INFO`).
- Software controls:
  - `QT_QUICK_BACKEND=software`, the Qt Quick software adaptation. **MultiEffect/ShaderEffect do not render there**, so pair it with `--no-blur`.
  - `LIBGL_ALWAYS_SOFTWARE=1`, Mesa llvmpipe, which keeps the shaders. Use the same variable for GTK's GL path.
  - The GTK counterpart is `GSK_RENDERER=cairo`.

**C6. Text input.** The source is identical at qtwayland `v6.8.2` `src/client/qwaylanddisplay.cpp` and qtbase `v6.12.0` `src/plugins/platforms/wayland/qwaylanddisplay.cpp`. **At 6.12 the Wayland client lives in qtbase**, and `qtwayland/src/client` returns 404 at v6.12.0.
- The Wayland input context is used when `QT_IM_MODULES`/`QT_IM_MODULE` is empty or contains `wayland` (`qwaylanddisplay_p.h`, `QPlatformInputContextFactory::requested()`).
- The preference order is `qt_text_input_method_v1` > `zwp_text_input_v2` > `zwp_text_input_v3` > `zwp_text_input_v1`. The code comment says "zwp_text_input_v2 is preferred over zwp_text_input_v3 because v3 is not as feature rich".
- Sway exposes only `zwp_text_input_manager_v3`, so Qt falls through to v3 by default.
- To force v3: `QT_WAYLAND_TEXT_INPUT_PROTOCOL=zwp_text_input_v3` (`;`-separated list).
- Trap: any non-empty `QT_IM_MODULE` other than `wayland` (for example `maliit`, `ibus`, `qtvirtualkeyboard`) disables the Wayland text-input path. Run with `QT_IM_MODULE` unset or set to `wayland`.

## D. cxx-qt (time-boxed Rust-on-Qt attempt)

- **Version.** Latest is cxx-qt / cxx-qt-lib / cxx-qt-build **0.10.0** (2026-08-24). The declared `rust-version` is **1.85.0** for all three (crates.io API). Trixie's rustc 1.85.1 therefore meets it, but the transitive deps were not checked on 1.85.
- **Qt support.** The README at tag v0.10.0 lists "Qt 5.15 LTS" and "All versions of Qt 6", and says "CXX-Qt is in early development and the API changes frequently" (https://github.com/KDAB/cxx-qt/blob/v0.10.0/README.md). Qt 6.12 is not named; covered only by "all versions of Qt 6".
- **Build-time discovery: qmake, not CMake, for a Cargo-only build.** In `qt-build-utils` 0.10.0 the default feature is `qmake`. `QtBuild::new` tries `$QMAKE` first and fails hard if it is set but broken. It then searches `PATH` for `["qmake6", "qmake-qt5", "qmake"]` in that order (`src/lib.rs`, `src/installation/qmake.rs`). An opt-in `qt_minimal` feature downloads Qt artifacts over the network, so do not enable it.
- **CMake.** CMake is only needed for the upstream "CMake drives Cargo via Corrosion" layout, where the README says "The CXX-Qt build system is based on CMake". The Cargo-only layout needs `qmake6` on PATH: Debian package `qmake6`, which `qt6-base-dev` 6.8.2 already Depends on, and `qt6-qtbase-dev` on Alpine. **E** lists the exact binaries, and the PoC should set `QMAKE=$(command -v qmake6)` explicitly.
- **Smallest known-good layout ("QML UI + one Rust QObject").** This is upstream `examples/cargo_without_cmake` at v0.10.0:
```
Cargo.toml   [dependencies] cxx = "1", cxx-qt = "=0.10.0", cxx-qt-lib = { version = "=0.10.0", features = ["full"] }
             [build-dependencies] cxx-qt-build = "=0.10.0"
build.rs     use cxx_qt_build::{CxxQtBuilder, QmlModule};
             fn main() { CxxQtBuilder::new_qml_module(QmlModule::new("com.example.poc").qml_file("qml/main.qml"))
                 .files(["src/cxxqt_object.rs"]).build(); }
src/cxxqt_object.rs   #[cxx_qt::bridge] pub mod qobject { unsafe extern "C++" { include!("cxx-qt-lib/qstring.h"); type QString = cxx_qt_lib::QString; }
                      extern "RustQt" { #[qobject] #[qml_element] #[qproperty(i32, number)] type MyObject = super::MyObjectRust;
                                        #[qinvokable] #[cxx_name = "incrementNumber"] fn increment_number(self: Pin<&mut Self>); } }
                      #[derive(Default)] pub struct MyObjectRust { number: i32 }
src/main.rs  QGuiApplication::new(); QQmlApplicationEngine::new();
             engine.load(&QUrl::from("qrc:/qt/qml/com/example/poc/qml/main.qml")); app.exec();
```
- **Static Qt.** The example's comment "The link_qt_object_files feature is required for statically linking Qt 6" does not apply, because distro Qt is shared.
- **Layer shell and frame timing.** `CxxQtBuilder` 0.10.0 has `cpp_file`/`cpp_files`/`include_dir`/`qt_module` (`src/lib.rs`). The layer-shell setup (C2) and `frameSwapped` timing need a small C++ file plus a `#[cxx::bridge]` call into it. Add `.cpp_file("src/shell.cpp")`, and link `LayerShellQtInterface` with `println!("cargo:rustc-link-lib=LayerShellQtInterface")`. That link line is UNVERIFIED because no upstream example links an external Qt library. `.qt_module("WaylandClient")` is not required, because the LayerShellQt public header only needs QtGui (UNVERIFIED).
- **Alpine/musl aarch64.** No report found. A GitHub issue search for "musl OR alpine OR aarch64" in KDAB/cxx-qt returned 10 hits. None is about musl. #128 "Figure out what changes are required for building on Linux arm64 / armhf" (closed 2022-04-21) was not read because the API was rate-limited. **UNVERIFIED: cxx-qt on Alpine/musl aarch64.** No Alpine or Debian package of cxx-qt exists (prior report, distro table).
- **Time box.** If `cargo build` with `QMAKE=qmake6` does not produce a working window in about half a day on either base, record the error and stop. The C++ Qt PoC is the real comparison.

## E. Build-container packages (names checked against the indexes, 2026-11-17)

Indexes: Debian `https://deb.debian.org/debian/dists/trixie/main/binary-amd64/Packages.xz` and `.../trixie-backports/main/binary-amd64/Packages.xz`. Alpine `https://dl-cdn.alpinelinux.org/alpine/edge/{main,community,testing}/aarch64/APKINDEX.tar.gz`. Versions are as listed in those indexes.

**Debian 13 trixie (amd64)**
- Common: `build-essential` 12.12, `pkg-config` 1.8.1-4, `cmake` 3.31.6-2, `ninja-build` 1.12.1-1, `libwayland-dev` 1.23.1-3, `wayland-protocols` 1.44-1, `mesa-vulkan-drivers` 25.0.7-2+deb13u1 (runtime, for GSK Vulkan).
- GTK PoC: `libgtk-4-dev` 4.18.6+ds-2, `libadwaita-1-dev` 1.7.6-1~deb13u1, `libgtk4-layer-shell-dev` 1.0.4-2. Toolchain for pin set (i): `apt install -t trixie-backports rustc cargo` gives **1.96.1+dfsg1-2~bpo13+1**. Fallback (ii) uses stock `rustc`/`cargo` 1.85.1+dfsg1-1+deb13u1.
- Qt PoC (build): `qt6-base-dev` 6.8.2+dfsg-9+deb13u2, `qt6-declarative-dev` 6.8.2+dfsg-7, `qt6-wayland-dev` 6.8.2-4, `liblayershellqtinterface-dev` 6.3.4-1. That last one depends on `liblayershellqtinterface6` and on `qml6-module-org-kde-layershell` 6.3.4-1, a QML attached-property module. `qt6-base-dev` already Depends on `qmake6` and `qt6-base-dev-tools`, and `qt6-declarative-dev` on `qt6-declarative-dev-tools`. cxx-qt therefore needs nothing extra.
- Qt PoC (runtime):
  - `qt6-wayland` 6.8.2-4, the "Qt 6 Wayland platform plugin", which pulls in `libqt6waylandclient6`.
  - `qt6-qpa-plugins` 6.8.2+dfsg-9+deb13u2.
  - `qml6-module-qtqml`, `qml6-module-qtqml-workerscript`, `qml6-module-qtquick`, `qml6-module-qtquick-window`, `qml6-module-qtquick-templates`, `qml6-module-qtquick-controls`, `qml6-module-qtquick-shapes`, `qml6-module-qtquick-effects`, `qml6-module-qtquick-layouts`, all 6.8.2+dfsg-7.
  - `qml6-module-qtquick-controls` contains the style plugins, Basic included, and depends on `-templates`, `-shapes` and `-window`. That Basic is inside it is UNVERIFIED by file list; the Depends line was read.
- Do NOT install `qt6-virtualkeyboard*` (GPL-3.0-only).

**Alpine edge / postmarketOS (aarch64)**
- Common: `build-base` 0.5-r4, `pkgconf` 3.0.11-r0, `cmake` 4.3.4-r0, `samurai` 1.3-r0 (Alpine's ninja; there is **no** `ninja` package), `wayland-dev` 1.26.0-r0, `wayland-protocols` 1.49-r0, `mesa-vulkan-freedreno` 26.3.0-r0 in the index (runtime, turnip; the phone was reported on mesa 26.2).
- GTK PoC: `rust` + `cargo` 1.100.0-r0, `gtk4.0-dev` 4.24.1-r0, `libadwaita-dev` 1.10.0-r0, `gtk4-layer-shell-dev` 1.3.0-r0 (provides `pc:gtk4-layer-shell-0=1.3.0`).
- Qt PoC (build):
  - `qt6-qtbase-dev` 6.12.0-r0. It provides `cmd:qmake6` and `pc:Qt6WaylandClient`, and depends on `qt6-qtbase-wayland`.
  - `qt6-qtdeclarative-dev` 6.12.0-r2.
  - `layer-shell-qt-dev` 6.8.3-r1, which depends on `qt6-qtdeclarative-dev` and `qt6-qtwayland-dev`.
- Qt PoC (runtime):
  - `qt6-qtbase-wayland` 6.12.0-r0 provides `libQt6WaylandClient.so.6` and `libQt6WlShellIntegration.so.6`. **At 6.12 the Wayland client is in qtbase.** `qt6-qtwayland` 6.12.0-r0 now ships only the compositor libraries.
  - `qt6-qtdeclarative` 6.12.0-r2 is one package that contains `libQt6Quick`, `libQt6QuickControls2Basic`, `libQt6QuickControls2BasicStyleImpl`, `libQt6QuickTemplates2`, `libQt6QuickEffects`, `libQt6QuickLayouts` and `libQt6QuickShapes` (from its `p:` provides). There are no separate qml module packages.
  - `layer-shell-qt` 6.8.3-r1.
- Do NOT install `qt6-qtvirtualkeyboard` (6.12.0-r0 exists).
- No `clang`/`clang-dev` package name exists in edge's aarch64 index (versioned `clangNN` only). Neither PoC needs libclang: gtk-rs does not use bindgen, and cxx does not need clang.

## F. Blur protocol on sway

**No.** Sway 1.12 `sway/server.c` creates no background-effect or blur global. It does create `wlr_text_input_manager_v3`, `wlr_input_method_manager_v2`, `wlr_presentation` (v2) and layer-shell v5 (https://github.com/swaywm/sway/blob/1.12/sway/server.c). Sway 1.12 builds against wlroots `>=0.20.0 <0.21.0` (`meson.build`). wlroots tag 0.20.0 has no `include/wlr/types/wlr_ext_background_effect_v1.h` (404), but master has it (200). So `ext-background-effect-v1` support is post-0.20 in wlroots and not used by sway 1.12. KDE's blur protocol is not implemented either (no global in `server.c`). All blur in both PoCs is therefore in-window.

## G. Known traps (these exact versions)

1. gtk4 0.10+ `glib::wrapper!` for a Widget subclass needs `@implements gtk::Accessible, gtk::Buildable, gtk::ConstraintTarget`, or `WidgetImpl` fails to compile. gtk4-layer-shell 0.7+ `set_namespace` takes `Option<&str>`. Never enable gtk4-layer-shell's `v1_3` feature, because trixie has C 1.0.4.
2. Use `GSK_RENDERER=opengl`. On 4.18, `gl` warns "old GL renderer removed". On 4.24, `ngl` warns "renamed to gl". Wayland defaults to **Vulkan** on both bases, so the phone needs `mesa-vulkan-freedreno` or it silently falls back to GL. Log `renderer().type_().name()` on every run.
3. CSS `backdrop-filter` and `@media (prefers-color-scheme)` exist on Alpine's 4.24 but not on trixie's 4.18. Code that works on the phone will silently fail on the desktop. Keep to the B3/B5 shapes.
4. A GTK `add_tick_callback` keeps the frame clock running every vblank and inflates the frame log. `after-paint` also fires on cycles with no paint.
5. libgtk4-layer-shell must load before libwayland-client. Check `ldd` order on both builds, with `LD_PRELOAD` as the fallback.
6. layer-shell-qt links Qt **private** QtWaylandClient API, so it only works with the Qt it was built against (6.3.4 needs Qt >= 6.7, 6.8.3 needs >= 6.11). Never mix a self-built Qt with distro layer-shell-qt. Never set `QT_WAYLAND_SHELL_INTEGRATION=layer-shell`, because it turns the main window into a layer surface too.
7. On Alpine 6.12 the Qt Wayland plugin is `qt6-qtbase-wayland`, not `qt6-qtwayland`. A runtime image without it falls back to another QPA or fails.
8. IME: Qt uses text-input only if `QT_IM_MODULE` is unset or `wayland`. GTK **needs** `GTK_IM_MODULE=wayland`, because its Wayland settings fallback says `simple` (B8). Neither toolkit will use v1/v2 on sway, because only v3 is advertised.
9. `QT_QUICK_BACKEND=software` cannot render ShaderEffect, and therefore MultiEffect ("ShaderEffect components in QtQuick 2 cannot be rendered by the Software adaptation", https://doc.qt.io/qt-6.8/qtquick-visualcanvas-adaptations-software.html). For software controls use `LIBGL_ALWAYS_SOFTWARE=1` for both toolkits, or pair `software`/`cairo` with blur off.
10. The two springs are parameterised differently: Adwaita uses damping ratio/mass/stiffness, QML uses spring/damping/mass. Match on settle time, not on numbers. Force `QSG_RENDER_LOOP=threaded` so `frameSwapped` fires from the same thread on both bases.

## UNVERIFIED list

- Link/run of the GTK pin set against the real 4.18.6/1.7.6/1.0.4 libraries. Only `cargo check` was run, and no rustc 1.92/1.96 was used for (i).
- GTK `ldd` link order for a Rust binary (B1).
- Whether GSK culls blur work outside the clip, which would make the inner `push_clip` redundant (B3).
- `clock.timings(counter)` usage for presentation time, which was not compile-checked.
- Whether the phone has a settings portal or gsettings that overrides GTK's `simple` fallback (B8). Irrelevant once `GTK_IM_MODULE=wayland` is set.
- `ScrolledWindow` kinetic default, and the computed 56 px row height under Adwaita CSS (B2).
- The B3 `Picture` + top-overlay split, which was not compile-checked.
- `LIBGL_ALWAYS_SOFTWARE=1` as the Mesa llvmpipe switch, which was not re-read from Mesa docs.
- Every Qt/QML fragment, which was not compiled. Also `qt_standard_project_setup(REQUIRES …)` and `QQmlApplicationEngine::loadFromModule` both being available since 6.5 (from memory and the docs index, not re-read), and the RHI default being OpenGL on Linux.
- Whether layer-shell-qt 6.3.4 honours `setScope` called after `Window::get()` (C2).
- That the Debian `qml6-module-qtquick-controls` file list contains the Basic style (inferred from Depends).
- cxx-qt: building on Alpine/musl aarch64, transitive deps on rustc 1.85, the link line for `LayerShellQtInterface`, and Qt 6.12 explicitly.
