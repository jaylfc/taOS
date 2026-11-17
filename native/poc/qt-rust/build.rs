use cxx_qt_build::{CxxQtBuilder, QmlFile, QmlModule};

fn main() {
    // SAFETY-free build; QMAKE is exported by build.sh.
    CxxQtBuilder::new_qml_module(
        QmlModule::new("Poc")
            .version(1, 0)
            .qml_files([
                QmlFile::from("qml/Main.qml"),
                QmlFile::from("qml/Bar.qml"),
                QmlFile::from("qml/Theme.qml").singleton(true),
            ]),
    )
    .files(["src/config.rs", "src/model.rs"])
    .qt_module("Quick")
    .cpp_file("src/shell.cpp")
    .build();
    // LayerShellQt ships no .pc file; link it by name (headers are under /usr/include/LayerShellQt).
    println!("cargo:rustc-link-lib=LayerShellQtInterface");
    println!("cargo:rustc-link-lib=Qt6Quick");
}
