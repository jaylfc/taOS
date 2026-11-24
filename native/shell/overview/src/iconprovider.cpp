#include "iconprovider.h"

#include <QFileInfo>

QIcon IconProvider::resolve(const AppInfo *apps, const QString &appId) {
    if (appId.isEmpty()) return {};
    QString name = apps ? apps->lookup(appId).icon : QString();
    if (name.isEmpty()) name = appId;
    QIcon ic;
    if (name.startsWith(QLatin1Char('/'))) {
        if (QFileInfo::exists(name)) ic = QIcon(name);
    } else {
        ic = QIcon::fromTheme(name);
        if (ic.isNull()) ic = QIcon::fromTheme(name.toLower());
    }
    return ic;
}

QPixmap IconProvider::requestPixmap(const QString &id, QSize *size, const QSize &requested) {
    const QIcon ic = resolve(m_apps, id);
    const QSize want = (requested.width() > 0 && requested.height() > 0) ? requested : QSize(64, 64);
    const QPixmap pm = ic.pixmap(want);
    if (size) *size = pm.size();
    return pm;
}
