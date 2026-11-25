// SPDX-License-Identifier: AGPL-3.0-or-later
#include "iconprovider.h"

#include "iconresolve.h"

QIcon IconProvider::resolve(const AppInfo *apps, const QString &appId) { return Icons::resolve(apps, appId); }

QPixmap IconProvider::requestPixmap(const QString &id, QSize *size, const QSize &requested) {
    const QIcon ic = resolve(m_apps, id);
    const QSize want = (requested.width() > 0 && requested.height() > 0) ? requested : QSize(64, 64);
    const QPixmap pm = ic.pixmap(want);
    if (size) *size = pm.size();
    return pm;
}
