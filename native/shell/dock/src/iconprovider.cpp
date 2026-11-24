// SPDX-License-Identifier: AGPL-3.0-or-later
#include "iconprovider.h"

#include <QFileInfo>
#include <QIcon>

QPixmap DockIconProvider::requestPixmap(const QString &id, QSize *size, const QSize &requested) {
    const QSize want = requested.isValid() && requested.width() > 0 ? requested : QSize(64, 64);
    QIcon ic;
    if (id.startsWith(QLatin1Char('/'))) { if (QFileInfo::exists(id)) ic = QIcon(id); }
    else {
        ic = QIcon::fromTheme(id);
        if (ic.isNull()) ic = QIcon::fromTheme(id.toLower());
        if (ic.isNull())   // a hicolor tree without an index.theme (a build tree, a bare prefix): look for the scalable file
            for (const QString &d : QIcon::themeSearchPaths()) {
                const QString f = d + QStringLiteral("/hicolor/scalable/apps/") + id + QStringLiteral(".svg");
                if (QFileInfo::exists(f)) { ic = QIcon(f); break; }
            }
    }
    QPixmap pm = ic.isNull() ? QPixmap() : ic.pixmap(want);
    if (pm.isNull()) { pm = QPixmap(1, 1); pm.fill(Qt::transparent); }   // "no icon": the QML draws its fallback glyph
    if (size) *size = pm.size();
    return pm;
}
