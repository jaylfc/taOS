// SPDX-License-Identifier: AGPL-3.0-or-later
#include "appearance.h"

#include <QDir>
#include <QFile>
#include <QFileInfo>
#include <QJsonDocument>
#include <QJsonObject>
#include <QSaveFile>

#include "themedef.h"

namespace AppearanceFile {

QString userPath() {
    const QString e = qEnvironmentVariable("TAOS_APPEARANCE");
    if (!e.isEmpty()) return e;
    const QString xdg = qEnvironmentVariable("XDG_CONFIG_HOME");
    return (xdg.isEmpty() ? QDir::homePath() + QStringLiteral("/.config") : xdg) + QStringLiteral("/taos/appearance.json");
}

QString systemPath() {
    const QString e = qEnvironmentVariable("TAOS_SYSTEM_APPEARANCE");
    return e.isEmpty() ? QStringLiteral("/etc/taos/appearance.json") : e;
}

QString mirrorDir() {
    const QString e = qEnvironmentVariable("TAOS_APPEARANCE_MIRROR_DIR");
    return e.isEmpty() ? QStringLiteral("/var/lib/taos/appearance") : e;
}

bool validMode(const QString &m) {
    return m == QLatin1String("dark") || m == QLatin1String("light") || m == QLatin1String("auto");
}

bool read(const QString &path, Appearance *out, QString *error) {
    *out = Appearance();
    QFile f(path);
    if (!f.open(QIODevice::ReadOnly)) {
        if (error) *error = QStringLiteral("cannot read %1").arg(path);
        return false;
    }
    QJsonParseError pe;
    const QJsonDocument d = QJsonDocument::fromJson(f.read(64 * 1024), &pe);
    if (pe.error != QJsonParseError::NoError || !d.isObject()) {
        if (error) *error = QStringLiteral("%1 is not a JSON object").arg(path);
        return false;
    }
    const QString theme = d.object().value(QStringLiteral("theme")).toString();
    const QString mode = d.object().value(QStringLiteral("mode")).toString();
    if (validThemeId(theme)) out->theme = theme;
    if (validMode(mode)) out->mode = mode;
    const QString side = d.object().value(QStringLiteral("windowButtons")).toString();
    if (validButtonSide(side)) out->windowButtons = side;
    return true;
}

bool write(const QString &path, const Appearance &a, QString *error, bool worldReadable) {
    if (!QDir().mkpath(QFileInfo(path).absolutePath())) {
        if (error) *error = QStringLiteral("cannot create the folder for %1").arg(path);
        return false;
    }
    QSaveFile f(path);
    f.setDirectWriteFallback(false);
    if (!f.open(QIODevice::WriteOnly)) {
        if (error) *error = QStringLiteral("cannot write %1").arg(path);
        return false;
    }
    QJsonObject o;
    o.insert(QStringLiteral("theme"), a.theme);
    o.insert(QStringLiteral("mode"), a.mode);
    if (validButtonSide(a.windowButtons)) o.insert(QStringLiteral("windowButtons"), a.windowButtons);
    f.write(QJsonDocument(o).toJson(QJsonDocument::Indented));
    if (!f.commit()) {
        if (error) *error = QStringLiteral("cannot save %1").arg(path);
        return false;
    }
    if (worldReadable)
        QFile::setPermissions(path, QFileDevice::ReadOwner | QFileDevice::WriteOwner | QFileDevice::ReadGroup | QFileDevice::ReadOther);
    return true;
}

QString resolveVariant(const QString &mode, const QDateTime &now) {
    if (mode == QLatin1String("light")) return QStringLiteral("light");
    if (mode == QLatin1String("auto")) {
        const int h = now.time().hour();
        return (h >= kAutoLightFromHour && h < kAutoLightToHour) ? QStringLiteral("light") : QStringLiteral("dark");
    }
    return QStringLiteral("dark");
}

}  // namespace AppearanceFile
