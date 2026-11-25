// SPDX-License-Identifier: AGPL-3.0-or-later
// taos-survival: Survival Studio. runQmlApp gives it the shared theme, single instance and the desktop session; the
// Survival engines are registered before the window loads. The data folder is $TAOS_SURVIVAL_DIR or the person's
// own data folder: packs and markers never live in the repository.
#include "backend.h"
#include "paths.h"
#include "taosqt/qmlapp.h"

int main(int argc, char *argv[])
{
    // a function-local static: it must outlive the engine runQmlApp creates and destroys
    static Taos::Survival::SurvivalBackend *backend = nullptr;
    // the backend needs QCoreApplication (timers, futures); runQmlApp builds it, so create the backend lazily when
    // the first QML engine asks for the singleton
    struct Lazy { static Taos::Survival::SurvivalBackend *get() {
        if (!backend) backend = new Taos::Survival::SurvivalBackend(Taos::Survival::survivalDataDir());
        return backend; } };
    Taos::Survival::registerSurvivalQmlLazy(&Lazy::get);
    return Taos::runQmlApp(argc, argv, {QStringLiteral("my.taos.survival"), QStringLiteral("Survival Studio"), QStringLiteral("0.1")},
                           QUrl(QStringLiteral("qrc:/my.taos.survival/Main.qml")));
}
