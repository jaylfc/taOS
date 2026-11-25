// SPDX-License-Identifier: AGPL-3.0-or-later
#include "iconprovider.h"

#include <QFileInfo>
#include <QHash>
#include <QMutex>
#include <QUrl>

namespace taos::launcher {

static QStringList pixmapDirs() {
    QStringList d;
    for (const QString &p : qEnvironmentVariable("TAOS_PIXMAP_DIRS").split(QLatin1Char(':'), Qt::SkipEmptyParts)) d << p;
    d << QStringLiteral("/usr/share/pixmaps");
    for (const QString &x : qEnvironmentVariable("XDG_DATA_DIRS", QStringLiteral("/usr/local/share:/usr/share")).split(QLatin1Char(':'), Qt::SkipEmptyParts))
        d << x + QStringLiteral("/pixmaps");
    return d;
}

QIcon resolveIcon(const QString &id) {
    if (id.isEmpty()) return {};
    if (id.startsWith(QLatin1Char('/'))) return QFileInfo::exists(id) ? QIcon(id) : QIcon();
    QIcon ic = QIcon::fromTheme(id);
    if (ic.isNull()) ic = QIcon::fromTheme(id.toLower());
    if (!ic.isNull()) return ic;
    // a hicolor tree without an index.theme (a build tree, a bare prefix): look for the scalable file
    for (const QString &d : QIcon::themeSearchPaths()) {
        const QString f = d + QStringLiteral("/hicolor/scalable/apps/") + id + QStringLiteral(".svg");
        if (QFileInfo::exists(f)) return QIcon(f);
        const QString g = d + QStringLiteral("/hicolor/scalable/apps/") + id.toLower() + QStringLiteral(".svg");
        if (QFileInfo::exists(g)) return QIcon(g);
    }
    for (const QString &d : pixmapDirs())
        for (const QString &ext : { QStringLiteral(".svg"), QStringLiteral(".png"), QStringLiteral(".xpm") }) {
            const QString f = d + QLatin1Char('/') + id + ext;
            if (QFileInfo::exists(f)) return QIcon(f);
        }
    return {};
}

static QMutex g_mu;
static QHash<QString, bool> g_cache;

bool hasIcon(const QString &name) {
    if (name.isEmpty()) return false;
    { QMutexLocker l(&g_mu); const auto it = g_cache.constFind(name); if (it != g_cache.constEnd()) return *it; }
    const bool ok = !resolveIcon(name).isNull();
    QMutexLocker l(&g_mu);
    g_cache.insert(name, ok);
    return ok;
}

void clearIconCache() { QMutexLocker l(&g_mu); g_cache.clear(); }

QPixmap IconProvider::requestPixmap(const QString &idIn, QSize *size, const QSize &requested) {
    // RowModel::iconUrl percent-encodes the name; accept both a decoded and a still encoded id
    const QString id = idIn.contains(QLatin1Char('%')) ? QUrl::fromPercentEncoding(idIn.toUtf8()) : idIn;
    const QIcon ic = resolveIcon(id);
    const QSize want = (requested.width() > 0 && requested.height() > 0) ? requested : QSize(64, 64);
    const QPixmap pm = ic.pixmap(want);
    if (size) *size = pm.size();
    return pm;
}

}  // namespace taos::launcher
