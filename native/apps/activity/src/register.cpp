// SPDX-License-Identifier: AGPL-3.0-or-later
// The generated main() (taos_add_native_app) has no registration hook, so the
// app's QML types (module Taos.Activity) are registered from a Qt startup
// function that runs when the QGuiApplication is created.
#include <QCoreApplication>

#include "types.h"

static void registerActivityTypes() { Taos::Activity::registerTypes(); }
Q_COREAPP_STARTUP_FUNCTION(registerActivityTypes)
