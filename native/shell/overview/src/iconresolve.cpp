// SPDX-License-Identifier: AGPL-3.0-or-later
#include "iconresolve.h"

#include <QFileInfo>
#include <QStandardPaths>

namespace {
QIcon byName(const QString &name) {
    if (name.isEmpty()) return {};
    if (name.startsWith(QLatin1Char('/'))) return QFileInfo::exists(name) ? QIcon(name) : QIcon();
    QIcon ic = QIcon::fromTheme(name);
    if (ic.isNull()) ic = QIcon::fromTheme(name.toLower());
    if (ic.isNull())   // a hicolor tree without an index.theme (a build tree, a bare prefix): the scalable file
        for (const QString &d : QIcon::themeSearchPaths()) {
            const QString f = d + QStringLiteral("/hicolor/scalable/apps/") + name + QStringLiteral(".svg");
            if (QFileInfo::exists(f)) return QIcon(f);
        }
    if (ic.isNull()) {
        QStringList dirs = QStandardPaths::standardLocations(QStandardPaths::GenericDataLocation);
        dirs << QStringLiteral("/usr/share");
        for (const QString &d : dirs)
            for (const char *ext : { ".png", ".svg", ".xpm" }) {
                const QString f = d + QStringLiteral("/pixmaps/") + name + QLatin1String(ext);
                if (QFileInfo::exists(f)) return QIcon(f);
            }
    }
    return ic;
}
}  // namespace

namespace Icons {
QIcon resolve(const AppInfo *apps, const QString &appId) {
    if (appId.isEmpty()) return {};
    QIcon ic;
    if (apps) ic = byName(apps->lookup(appId).icon);
    if (ic.isNull()) ic = byName(appId);
    return ic;
}
}  // namespace Icons
