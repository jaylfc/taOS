// SPDX-License-Identifier: AGPL-3.0-or-later
// taos-store: the generated entry point of taos_add_native_app would do, but the Store's own QML types
// (Taos.Store) must be registered before the engine loads Main.qml, so main() is written out. Everything else is
// Taos::runQmlApp: single instance (a second `taos-store --app <id>` navigates the running window), the shared
// desktop session, the Taos module and the theme.
#include "storeengine.h"
#include "taosqt/qmlapp.h"

int main(int argc, char *argv[])
{
    Taos::Store::registerStoreQmlTypes();
    return Taos::runQmlApp(argc, argv, {QStringLiteral("my.taos.store"), QStringLiteral("Store"), QStringLiteral("0.1")},
                           QUrl(QStringLiteral("qrc:/my.taos.store/Main.qml")));
}
