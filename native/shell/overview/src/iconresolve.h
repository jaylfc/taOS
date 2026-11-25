// SPDX-License-Identifier: AGPL-3.0-or-later
#pragma once
#include <QIcon>
#include <QString>

#include "appinfo.h"

namespace Icons {
// The icon for a window's app, resolved the way the dock does it: the entry's Icon (an absolute path, or a
// theme name tried as written and lower case), a bare hicolor tree without index.theme, /usr/share/pixmaps,
// then the app id itself as a theme name. Null when nothing is found (the caller draws its fallback tile).
QIcon resolve(const AppInfo *apps, const QString &appId);
}
