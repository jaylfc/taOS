#include "wallpaperprovider.h"

#include <QFile>
#include <QFileInfo>
#include <QJsonArray>
#include <QJsonDocument>
#include <QJsonObject>

namespace WallpaperProvider {

QString defaultDir() {
    const QString e = qEnvironmentVariable("TAOS_WALLPAPER_DIR");
    return e.isEmpty() ? QStringLiteral("/usr/share/taos/wallpapers") : e;
}

QString pick(const QString &dir, const QString &variant, const QSize &size, const QString &idIn) {
    QFile f(dir + QStringLiteral("/index.json"));
    if (!f.open(QIODevice::ReadOnly)) return QString();
    const QJsonDocument d = QJsonDocument::fromJson(f.read(1024 * 1024));
    if (!d.isObject()) return QString();
    const QString id = idIn.isEmpty() ? d.object().value(QStringLiteral("default")).toString() : idIn;
    QJsonObject files;
    for (const QJsonValue &v : d.object().value(QStringLiteral("wallpapers")).toArray()) {
        const QJsonObject o = v.toObject();
        if (o.value(QStringLiteral("id")).toString() == id) {
            files = o.value(QStringLiteral("files")).toObject().value(variant).toObject();
            break;
        }
    }
    const qint64 w = qMax(1, size.width()), h = qMax(1, size.height());
    QString best;
    QString bestScore;
    for (auto it = files.begin(); it != files.end(); ++it) {
        const QString key = it.key();
        const int x = key.indexOf(QLatin1Char('x'));
        if (x <= 0) continue;
        bool okw = false, okh = false;
        const qint64 cw = key.left(x).toLongLong(&okw), ch = key.mid(x + 1).toLongLong(&okh);
        if (!okw || !okh || cw <= 0 || ch <= 0) continue;
        const int orient = ((cw >= ch) == (w >= h)) ? 0 : 1;
        qint64 aspect = cw * h - ch * w;
        if (aspect < 0) aspect = -aspect;
        aspect = aspect * 1000 / (ch * h);
        if (aspect > 9999) aspect = 9999;
        const qint64 fit = (cw >= w && ch >= h) ? (cw * ch - w * h) : (1000000000 - cw * ch);
        // Same score as the shell helper: orientation, aspect (4 digits), fit (10 digits).
        const QString score = QStringLiteral("%1%2%3").arg(orient).arg(aspect, 4, 10, QLatin1Char('0')).arg(fit, 10, 10, QLatin1Char('0'));
        if (best.isEmpty() || score < bestScore) {
            best = it.value().toString();
            bestScore = score;
        }
    }
    if (best.isEmpty() || best.contains(QStringLiteral(".."))) return QString();
    const QString path = dir + QLatin1Char('/') + best;
    return QFileInfo::exists(path) ? path : QString();
}

}  // namespace WallpaperProvider
