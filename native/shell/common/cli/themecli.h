#pragma once
#include <QStringList>
#include <QTextStream>

// `taos-theme`: list, get and set the theme from a terminal or over SSH. Exit codes: 0 done,
// 1 the request could not be done (unknown theme, file not writable), 2 usage.
//   taos-theme list [--json]
//   taos-theme get [--json]
//   taos-theme set <theme> [--mode dark|light|auto]
//   taos-theme set --mode dark|light|auto
int runThemeCli(const QStringList &args, QTextStream &out, QTextStream &err);
