// SPDX-License-Identifier: AGPL-3.0-or-later
#include "config.h"

#include <QDir>
#include <QFile>
#include <QJsonArray>
#include <QJsonDocument>
#include <QJsonObject>

namespace taos::search {

QString LauncherConfig::defaultPath() {
    QString base = qEnvironmentVariable("XDG_CONFIG_HOME");
    if (base.isEmpty()) base = QDir::homePath() + QStringLiteral("/.config");
    return base + QStringLiteral("/taos/launcher.json");
}

void LauncherConfig::load() {
    bool open = false;
    QStringList ask;
    QFile f(m_path);
    if (f.open(QIODevice::ReadOnly)) {
        const QJsonDocument d = QJsonDocument::fromJson(f.read(256 * 1024));
        if (d.isObject()) {
            const QJsonObject o = d.object();
            open = o.value(QStringLiteral("mentionAction")).toString() == QLatin1String("send_and_open");
            for (const QJsonValue &v : o.value(QStringLiteral("askCommand")).toArray()) if (v.isString()) ask << v.toString();
        }
    }
    QMutexLocker l(&m_mu);
    m_sendAndOpen = open;
    m_ask = ask;
}

}  // namespace taos::search
