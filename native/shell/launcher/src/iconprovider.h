// SPDX-License-Identifier: AGPL-3.0-or-later
#pragma once
#include <QIcon>
#include <QQuickImageProvider>

namespace taos::launcher {

// One icon resolution for the menu, the search rows and the image provider; it follows the dock's rules: an
// absolute path, the icon theme (also lower case), a bare hicolor tree without an index.theme, then the legacy
// pixmaps folders (/usr/share/pixmaps, plus TAOS_PIXMAP_DIRS, colon separated).
QIcon resolveIcon(const QString &name);
bool hasIcon(const QString &name);   // cached; clearIconCache() after something was installed
void clearIconCache();

// image://icon/<percent-encoded theme name or absolute path>
class IconProvider : public QQuickImageProvider {
public:
    IconProvider() : QQuickImageProvider(QQuickImageProvider::Pixmap) {}
    QPixmap requestPixmap(const QString &id, QSize *size, const QSize &requested) override;
};
}
