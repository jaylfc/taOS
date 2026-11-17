mod config;
mod model;

use core::pin::Pin;
use cxx_qt_lib::{QGuiApplication, QQmlApplicationEngine, QUrl};

extern "C" {
    /// src/shell.cpp: layer-shell bar, frame probe, autoquit, window show.
    fn taos_shell_attach(engine: *mut core::ffi::c_void) -> i32;
}

fn main() {
    cxx_qt::init_crate!(cxx_qt_lib);
    cxx_qt::init_qml_module!("Poc");

    if std::env::var_os("QSG_RENDER_LOOP").is_none() {
        std::env::set_var("QSG_RENDER_LOOP", "threaded");
    }

    let mut app = QGuiApplication::new();
    let mut engine = QQmlApplicationEngine::new();
    if let Some(mut e) = engine.as_mut() {
        e.as_mut().load(&QUrl::from("qrc:/qt/qml/Poc/qml/Bar.qml"));
        e.as_mut().load(&QUrl::from("qrc:/qt/qml/Poc/qml/Main.qml"));
    }
    let eng: *mut QQmlApplicationEngine = engine.as_mut().map_or(core::ptr::null_mut(), |e| {
        // SAFETY: only the address is taken; the engine stays owned by `engine`.
        unsafe { Pin::get_unchecked_mut(e) as *mut _ }
    });
    if unsafe { taos_shell_attach(eng as *mut _) } != 0 {
        std::process::exit(1);
    }
    let rc = app.as_mut().map_or(1, |a| a.exec());
    unsafe { taos_shell_finish() };
    // Destroy engine then app before exiting (process::exit skips drops; Qt statics must not outlive them).
    drop(engine);
    drop(app);
    std::process::exit(rc);
}

extern "C" {
    fn taos_shell_finish();
}
