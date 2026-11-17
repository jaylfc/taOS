mod blur;

use std::cell::{Cell, RefCell};
use std::io::Write;
use std::path::PathBuf;
use std::rc::Rc;
use std::time::Duration;

use adw::prelude::*;
use gtk::{gdk, gio, glib};
use gtk4_layer_shell::{Edge, Layer, LayerShell};

use blur::BlurScene;

const DARK_CSS: &str = "\
window.poc { background: #0E0E10; color: #F2F2F4; }\n\
window.poc-bar { background: #17171A; color: #F2F2F4; }\n\
listview.poc-list { background: rgba(23,23,26,0.7); color: #F2F2F4; }\n\
.poc-card-text { color: #F2F2F4; }\n";

const LIGHT_CSS: &str = "\
window.poc { background: #F6F6F8; color: #1C1C1E; }\n\
window.poc-bar { background: #FFFFFF; color: #1C1C1E; }\n\
listview.poc-list { background: rgba(255,255,255,0.7); color: #1C1C1E; }\n\
.poc-card-text { color: #1C1C1E; }\n";

const COMMON_CSS: &str = "\
window.poc { font-size: 16px; }\n\
listview.poc-list > row { padding: 0; min-height: 0; background: transparent; }\n\
.poc-title { font-size: 16px; }\n\
.poc-sub { font-size: 13px; opacity: 0.7; }\n\
.poc-bar-label { font-size: 16px; }\n";

struct Config {
    wallpaper: PathBuf,
    t0_ns: Option<u64>,
    framelog: Option<PathBuf>,
    no_blur: bool,
    blur_radius: f64,
    autocard: u32,
    autoscroll: u32,
    autoquit_ms: Option<u64>,
}

fn env_nonempty(k: &str) -> Option<String> {
    std::env::var(k).ok().filter(|v| !v.is_empty())
}

impl Config {
    fn from_env() -> Config {
        let default_wp = PathBuf::from(env!("CARGO_MANIFEST_DIR")).join("../common/wallpaper.png");
        Config {
            wallpaper: env_nonempty("POC_WALLPAPER").map(PathBuf::from).unwrap_or(default_wp),
            t0_ns: env_nonempty("POC_T0_NS").and_then(|v| v.parse().ok()),
            framelog: env_nonempty("POC_FRAMELOG").map(PathBuf::from),
            no_blur: env_nonempty("POC_NO_BLUR").is_some_and(|v| v == "1"),
            blur_radius: env_nonempty("POC_BLUR_RADIUS").and_then(|v| v.parse().ok()).unwrap_or(24.0),
            autocard: env_nonempty("POC_AUTOCARD").and_then(|v| v.parse().ok()).unwrap_or(0),
            autoscroll: env_nonempty("POC_AUTOSCROLL").and_then(|v| v.parse().ok()).unwrap_or(0),
            autoquit_ms: env_nonempty("POC_AUTOQUIT_MS").and_then(|v| v.parse().ok()),
        }
    }
}

fn css(text: &str) -> gtk::CssProvider {
    let p = gtk::CssProvider::new();
    p.load_from_string(text);
    p
}

fn read_battery() -> String {
    if let Ok(rd) = std::fs::read_dir("/sys/class/power_supply") {
        let mut dirs: Vec<_> = rd.flatten().map(|e| e.path()).collect();
        dirs.sort();
        for d in dirs {
            let is_batt = std::fs::read_to_string(d.join("type")).is_ok_and(|t| t.trim() == "Battery");
            if is_batt && let Ok(c) = std::fs::read_to_string(d.join("capacity")) {
                return format!("{}%", c.trim());
            }
        }
    }
    "--%".to_string()
}

fn clock_text() -> String {
    glib::DateTime::now_local()
        .and_then(|d| d.format("%H:%M"))
        .map(|s| s.to_string())
        .unwrap_or_else(|_| "--:--".into())
}

fn build_bar(app: &adw::Application) {
    if !gtk4_layer_shell::is_supported() {
        eprintln!("taos-poc-gtk: layer-shell not supported by this compositor; bar skipped");
        return;
    }
    let bar = gtk::ApplicationWindow::new(app);
    bar.add_css_class("poc-bar");
    bar.set_decorated(false);
    bar.init_layer_shell(); // before the window is realized
    bar.set_layer(Layer::Top);
    for e in [Edge::Top, Edge::Left, Edge::Right] {
        bar.set_anchor(e, true);
    }
    bar.set_namespace(Some("taos-poc-bar"));
    bar.set_default_size(0, 48);
    bar.set_exclusive_zone(48);

    let row = gtk::CenterBox::new();
    row.set_height_request(48);
    let clock = gtk::Label::new(Some(&clock_text()));
    clock.add_css_class("poc-bar-label");
    clock.set_margin_start(84); // clears the 0..80 camera cut-out
    let batt = gtk::Label::new(Some(&read_battery()));
    batt.add_css_class("poc-bar-label");
    batt.set_margin_end(16);
    row.set_start_widget(Some(&clock));
    row.set_end_widget(Some(&batt));
    bar.set_child(Some(&row));

    glib::timeout_add_seconds_local(5, move || {
        clock.set_text(&clock_text());
        batt.set_text(&read_battery());
        glib::ControlFlow::Continue
    });
    bar.present();
}

fn build_list() -> gtk::ScrolledWindow {
    let model = gtk::StringList::new(&[]);
    for i in 0..500 {
        model.append(&format!("Row {i}"));
    }
    let factory = gtk::SignalListItemFactory::new();
    factory.connect_setup(|_, obj| {
        let item = obj.downcast_ref::<gtk::ListItem>().unwrap();
        let hb = gtk::Box::new(gtk::Orientation::Horizontal, 12);
        hb.set_height_request(56);
        hb.set_margin_start(16);
        hb.set_margin_end(16);
        let icon = gtk::Image::from_icon_name("folder-symbolic");
        icon.set_pixel_size(24);
        let vb = gtk::Box::new(gtk::Orientation::Vertical, 0);
        vb.set_valign(gtk::Align::Center);
        let title = gtk::Label::new(None);
        title.set_xalign(0.0);
        title.add_css_class("poc-title");
        let sub = gtk::Label::new(None);
        sub.set_xalign(0.0);
        sub.add_css_class("poc-sub");
        vb.append(&title);
        vb.append(&sub);
        hb.append(&icon);
        hb.append(&vb);
        item.set_child(Some(&hb));
    });
    factory.connect_bind(|_, obj| {
        let item = obj.downcast_ref::<gtk::ListItem>().unwrap();
        let s = item.item().and_downcast::<gtk::StringObject>().unwrap().string();
        let n = s.strip_prefix("Row ").unwrap_or(&s).to_string();
        let vb = item
            .child()
            .and_downcast::<gtk::Box>()
            .unwrap()
            .last_child()
            .and_downcast::<gtk::Box>()
            .unwrap();
        vb.first_child().and_downcast::<gtk::Label>().unwrap().set_text(&s);
        vb.last_child()
            .and_downcast::<gtk::Label>()
            .unwrap()
            .set_text(&format!("Second line for row {n}"));
    });
    let lv = gtk::ListView::new(Some(gtk::NoSelection::new(Some(model))), Some(factory));
    lv.add_css_class("poc-list");
    let sw = gtk::ScrolledWindow::new();
    sw.set_kinetic_scrolling(true);
    sw.set_hscrollbar_policy(gtk::PolicyType::Never);
    sw.set_child(Some(&lv));
    sw.set_vexpand(true);
    sw.set_margin_start(16);
    sw.set_margin_end(16);
    sw
}

fn install_autoscroll(sw: &gtk::ScrolledWindow, n: u32) {
    let sw = sw.clone();
    glib::timeout_add_local_once(Duration::from_secs(2), move || {
        let adj = sw.vadjustment();
        let dir = Cell::new(1.0_f64); // +1 down, -1 up
        let trips = Cell::new(0u32);
        let last: Cell<Option<i64>> = Cell::new(None);
        sw.add_tick_callback(move |_, clock| {
            let now = clock.frame_time();
            let dt = last.get().map_or(0.0, |l| (now - l) as f64 / 1e6);
            last.set(Some(now));
            let max = (adj.upper() - adj.page_size()).max(0.0);
            let mut v = adj.value() + dir.get() * 3000.0 * dt;
            if v >= max {
                v = max;
                dir.set(-1.0);
            } else if v <= 0.0 && dir.get() < 0.0 {
                v = 0.0;
                dir.set(1.0);
                trips.set(trips.get() + 1);
            }
            adj.set_value(v);
            if trips.get() >= n {
                return glib::ControlFlow::Break;
            }
            glib::ControlFlow::Continue
        });
    });
}

fn build_main(app: &adw::Application, cfg: &Rc<Config>) {
    let win = gtk::ApplicationWindow::new(app);
    win.set_title(Some("taos-poc-gtk"));
    win.add_css_class("poc");
    win.set_default_size(720, 1280);

    let texture = match gdk::Texture::from_filename(&cfg.wallpaper) {
        Ok(t) => Some(t),
        Err(e) => {
            eprintln!("taos-poc-gtk: cannot load wallpaper {}: {e}", cfg.wallpaper.display());
            None
        }
    };

    let overlay = gtk::Overlay::new();
    let pic = gtk::Picture::new();
    if let Some(t) = &texture {
        pic.set_paintable(Some(t));
    }
    pic.set_content_fit(gtk::ContentFit::Cover);
    pic.set_can_shrink(true);
    overlay.set_child(Some(&pic));

    let column = gtk::Box::new(gtk::Orientation::Vertical, 0);
    let controls = gtk::Box::new(gtk::Orientation::Horizontal, 12);
    controls.set_height_request(56);
    controls.set_margin_start(16);
    controls.set_margin_end(16);
    let entry = gtk::Entry::new();
    entry.set_placeholder_text(Some("Type here"));
    entry.set_hexpand(true);
    entry.set_valign(gtk::Align::Center);
    let card_btn = gtk::Button::with_label("Card");
    card_btn.set_valign(gtk::Align::Center);
    let theme_btn = gtk::Button::with_label("Theme");
    theme_btn.set_valign(gtk::Align::Center);
    controls.append(&entry);
    controls.append(&card_btn);
    controls.append(&theme_btn);
    column.append(&controls);
    let sw = build_list();
    column.append(&sw);
    overlay.add_overlay(&column);

    let scene = BlurScene::new(texture, !cfg.no_blur, cfg.blur_radius, "Blurred card: the wallpaper behind this surface");
    overlay.add_overlay(&scene);
    win.set_child(Some(&overlay));

    // Card spring toggle.
    let shown = Rc::new(Cell::new(false));
    let anim: Rc<RefCell<Option<adw::SpringAnimation>>> = Rc::new(RefCell::new(None));
    let toggle: Rc<dyn Fn()> = {
        let (scene, shown, anim) = (scene.clone(), shown.clone(), anim.clone());
        Rc::new(move || {
            if let Some(old) = anim.borrow_mut().take() {
                old.pause();
            }
            let to = if shown.get() { 0.0 } else { 1.0 };
            shown.set(!shown.get());
            let s2 = scene.clone();
            let target = adw::CallbackAnimationTarget::new(move |v| s2.set_progress(v));
            let a = adw::SpringAnimation::new(&scene, scene.progress(), to, adw::SpringParams::new(0.7, 1.0, 300.0), target);
            a.play();
            *anim.borrow_mut() = Some(a);
        })
    };
    {
        let t = toggle.clone();
        card_btn.connect_clicked(move |_| t());
    }

    // Theme: runtime dark/light via StyleManager, two swapped providers.
    let sm = adw::StyleManager::default();
    sm.set_color_scheme(adw::ColorScheme::ForceDark);
    {
        let sm = sm.clone();
        theme_btn.connect_clicked(move |_| {
            sm.set_color_scheme(if sm.is_dark() { adw::ColorScheme::ForceLight } else { adw::ColorScheme::ForceDark });
        });
    }
    let (dark, light) = (css(DARK_CSS), css(LIGHT_CSS));
    let display = gdk::Display::default().unwrap();
    gtk::style_context_add_provider_for_display(&display, &css(COMMON_CSS), gtk::STYLE_PROVIDER_PRIORITY_APPLICATION);
    let apply = {
        let scene = scene.clone();
        move |sm: &adw::StyleManager| {
            let d = gdk::Display::default().unwrap();
            let (on, off) = if sm.is_dark() { (&dark, &light) } else { (&light, &dark) };
            gtk::style_context_remove_provider_for_display(&d, off);
            gtk::style_context_add_provider_for_display(&d, on, gtk::STYLE_PROVIDER_PRIORITY_APPLICATION);
            scene.queue_draw();
        }
    };
    apply(&sm);
    sm.connect_dark_notify(move |sm| apply(sm));

    // Frame log + cold start, at the frame clock's after-paint of the MAIN window.
    {
        let cfg = cfg.clone();
        let log = cfg.framelog.as_ref().and_then(|p| {
            std::fs::OpenOptions::new()
                .create(true)
                .append(true)
                .open(p)
                .map_err(|e| eprintln!("taos-poc-gtk: POC_FRAMELOG {}: {e}", p.display()))
                .ok()
        });
        let state = Rc::new(RefCell::new((None::<i64>, true, log))); // prev, first, file
        win.connect_realize(move |w| {
            let Some(clock) = w.frame_clock() else { return };
            let (cfg, state) = (cfg.clone(), state.clone());
            // after-paint fires on every frame-clock cycle, including cycles in
            // which nothing was drawn; `paint` is emitted only when a paint was
            // requested. Log a frame only if this cycle painted, so the log
            // counts drawn frames, as Qt's frameSwapped does.
            let painted = Rc::new(Cell::new(false));
            {
                let painted = painted.clone();
                clock.connect_paint(move |_| painted.set(true));
            }
            clock.connect_after_paint(move |_| {
                if !painted.replace(false) {
                    return;
                }
                let t = glib::monotonic_time(); // CLOCK_MONOTONIC, microseconds
                let mut st = state.borrow_mut();
                if st.1 {
                    st.1 = false;
                    match cfg.t0_ns {
                        Some(t0) => eprintln!("coldstart_ms={:.3}", (t as f64 * 1000.0 - t0 as f64) / 1e6),
                        None => eprintln!("coldstart_ms=na"),
                    }
                }
                if let Some(p) = st.0 {
                    if let Some(f) = st.2.as_mut() {
                        let _ = writeln!(f, "{}", t - p);
                    }
                }
                st.0 = Some(t);
            });
        });
    }

    // Renderer name once the surface/renderer exist.
    {
        let done = Cell::new(false);
        win.connect_map(move |w| {
            if done.replace(true) {
                return;
            }
            let name = w.renderer().map(|r| r.type_().name().to_string()).unwrap_or_else(|| "none".into());
            eprintln!("renderer={name}");
        });
    }

    // Autos.
    if cfg.autocard > 0 {
        let n = cfg.autocard;
        let toggle = toggle.clone();
        glib::timeout_add_local_once(Duration::from_secs(2), move || {
            toggle();
            let mut left = n - 1;
            if left > 0 {
                glib::timeout_add_local(Duration::from_millis(1200), move || {
                    toggle();
                    left -= 1;
                    if left == 0 { glib::ControlFlow::Break } else { glib::ControlFlow::Continue }
                });
            }
        });
    }
    if cfg.autoscroll > 0 {
        install_autoscroll(&sw, cfg.autoscroll);
    }
    if let Some(ms) = cfg.autoquit_ms {
        let app = app.clone();
        glib::timeout_add_local_once(Duration::from_millis(ms), move || app.quit());
    }

    win.present();
}

fn main() -> glib::ExitCode {
    let args: Vec<String> = std::env::args().collect();
    for a in &args[1..] {
        match a.as_str() {
            "-h" | "--help" => {
                println!("usage: taos-poc-gtk [-h|--help] [-V|--version]\nbehaviour is controlled by POC_* environment variables (see CONTRACT.md)");
                return glib::ExitCode::SUCCESS;
            }
            "-V" | "--version" => {
                println!("taos-poc-gtk {}", env!("CARGO_PKG_VERSION"));
                return glib::ExitCode::SUCCESS;
            }
            other => {
                eprintln!("taos-poc-gtk: unknown argument '{other}'");
                return glib::ExitCode::from(2);
            }
        }
    }
    let cfg = Rc::new(Config::from_env());
    if let Err(e) = adw::init() {
        eprintln!("taos-poc-gtk: cannot initialise GTK (no display?): {e}");
        return glib::ExitCode::FAILURE;
    }
    eprintln!("toolkit=gtk {}.{}.{}", gtk::major_version(), gtk::minor_version(), gtk::micro_version());

    let app = adw::Application::builder()
        .application_id("org.taos.poc.gtk")
        .flags(gio::ApplicationFlags::NON_UNIQUE)
        .build();
    app.connect_activate(move |app| {
        build_bar(app);
        build_main(app, &cfg);
    });
    app.run_with_args::<&str>(&[])
}
