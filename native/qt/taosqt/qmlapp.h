#pragma once
#include <QUrl>

#include "taosqt/app.h"

namespace Taos {

/// The whole `main()` of a QML app: QGuiApplication, App::configure, single
/// instance (a second launch raises the first window), a Session on the
/// controller (`TAOS_URL` or http://127.0.0.1:6969) whose TokenSource is the
/// shared desktop session (DesktopSessionTokenSource), the default Client, the
/// Taos QML module (types plus the `Taos` singleton) and the style module
/// `Taos.Style`; then loads `mainQml` and starts the Session. Generated into
/// every app by taos_add_native_app; apps do not write C++. Needs Qt Qml.
TAOSQT_EXPORT int runQmlApp(int argc, char *argv[], const AppInfo &info, const QUrl &mainQml);
} // namespace Taos
