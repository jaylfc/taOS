// SPDX-License-Identifier: AGPL-3.0-or-later
#pragma once
#include <QProcessEnvironment>
#include <QString>

#include "types.h"

namespace taos::search {

// Executes an Action. Detached: nothing it starts is a child of the launcher.
//   launch  argv from a desktop entry; wrapped in a terminal when data == "terminal"
//   exec    argv as is
//   focus-window / open / display-scale / copy (copy is done by the caller: needs the GUI clipboard)
// Returns false and fills `error` when it could not start.
bool runAction(const Action &a, QString *error = nullptr);

QProcessEnvironment childEnvironment();
// Terminal emulator argv prefix ("foot", "-e" ...) or empty when none is installed.
QStringList terminalPrefix();
// Name of the focused output from `swaymsg -t get_outputs -r`; empty when unknown.
QString focusedOutput();
QString focusedOutputFromJson(const QByteArray &json);

}  // namespace taos::search
