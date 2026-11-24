// SPDX-License-Identifier: AGPL-3.0-or-later
#pragma once
#include <QSize>
#include <QString>

// WallpaperProvider: picks the wallpaper file the same way the session does
// (`taos-wallpaper path --variant V --size WxH`): the default id of
// <dir>/index.json, the requested variant, the candidate of the same
// orientation with the nearest aspect ratio that is at least as large as the
// output (else the largest).
namespace WallpaperProvider {
// Returns the absolute file path, or an empty string when nothing matches.
QString pick(const QString &dir, const QString &variant, const QSize &size, const QString &id = QString());
QString defaultDir();  // env TAOS_WALLPAPER_DIR or /usr/share/taos/wallpapers
}
