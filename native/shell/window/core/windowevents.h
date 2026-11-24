// SPDX-License-Identifier: AGPL-3.0-or-later
#pragma once
#include <QString>
#include <functional>

#include "windowmodel.h"

namespace TaosWin {

// Follows sway's window and workspace events and reports what changed in terms of windows:
// new, close, focus, title, minimise, restore. It diffs the tree after every event, so a minimise (marks and a
// move to the scratchpad) is one `minimise`, not a burst of raw events.
// Blocks until `stop` returns true or sway goes away. Returns false when sway could not be reached.
bool followWindows(const QString &socketPath, const std::function<void(const WindowEvent &)> &sink,
                   const std::function<bool()> &stop, QString *error, int pollMs = 500);

// The always-on helper behind desktop mode: a window that appears on a workspace in desktop mode is floated at a
// sensible size and cascaded (see newWindowCommands). Runs until `stop` or sway goes away.
bool runModeDaemon(const QString &socketPath, const std::function<bool()> &stop, QString *error);

}  // namespace TaosWin
