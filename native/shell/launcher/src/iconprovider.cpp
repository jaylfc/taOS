// SPDX-License-Identifier: AGPL-3.0-or-later
#include "iconprovider.h"

#include <QFileInfo>
#include <QUrl>

namespace taos::launcher {

QPixmap IconProvider::requestPixmap(const QString &idIn, QSize *size, const QSize &requested) {
    // RowModel::iconUrl percent-encodes the name; accept both a decoded and a still encoded id
    const QString id = idIn.contains(QLatin1Char('%')) ? QUrl::fromPercentEncoding(idIn.toUtf8()) : idIn;
    QIcon ic;
    if (id.startsWith(QLatin1Char('/'))) { if (QFileInfo::exists(id)) ic = QIcon(id); }
    else {
        ic = QIcon::fromTheme(id);
        if (ic.isNull()) ic = QIcon::fromTheme(id.toLower());
    }
    const QSize want = (requested.width() > 0 && requested.height() > 0) ? requested : QSize(64, 64);
    const QPixmap pm = ic.pixmap(want);
    if (size) *size = pm.size();
    return pm;
}

}  // namespace taos::launcher
