// SPDX-License-Identifier: AGPL-3.0-or-later
#pragma once
#include <QString>
#include <QStringList>

// `taos-dock get [key] | set <key> <value> | pin <id> | unpin <id> | list`: edits dock.json, which a
// running dock picks up live. Output goes to `out`, problems to `err`; returns the exit code.
namespace DockCli {
bool isCommand(const QString &word);
int run(const QStringList &args, const QString &configPath, QString *out, QString *err, const QStringList &appDirs = {});
}
