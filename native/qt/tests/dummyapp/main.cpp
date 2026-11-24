// SPDX-License-Identifier: AGPL-3.0-or-later
// A stand-in app that is built through taos_add_native_app, so the CMake
// function and the .desktop template are exercised by the test suite.
#include <QGuiApplication>

#include "taosqt/app.h"

int main(int argc, char *argv[])
{
    QGuiApplication app(argc, argv);
    Taos::App::configure({QStringLiteral(TAOS_APP_ID), QStringLiteral(TAOS_APP_NAME), QString()});
    return QGuiApplication::desktopFileName() == QStringLiteral(TAOS_APP_ID) ? 0 : 1;
}
