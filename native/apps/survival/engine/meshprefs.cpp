// SPDX-License-Identifier: AGPL-3.0-or-later
#include "meshprefs.h"

#include <QDir>
#include <QFile>
#include <QJsonDocument>
#include <QJsonObject>
#include <QSaveFile>

namespace Taos::Survival {

MeshPrefs::MeshPrefs(QString dataDir) : m_dir(std::move(dataDir)) {}

bool MeshPrefs::load()
{
    QFile f(path());
    if (!f.exists())
        return true;
    if (!f.open(QIODevice::ReadOnly))
        return false;
    QJsonParseError pe;
    const QJsonDocument d = QJsonDocument::fromJson(f.readAll(), &pe);
    if (pe.error != QJsonParseError::NoError || !d.isObject())
        return false;   // damaged: reported, and sending stays off
    const QJsonObject o = d.object();
    m_sendingOn = o.value(QStringLiteral("sendingOn")).toBool(false);
    const QJsonObject r = o.value(QStringLiteral("lastRead")).toObject();
    for (auto it = r.begin(); it != r.end(); ++it)
        m_read.insert(it.key(), qint64(it.value().toDouble()));
    return true;
}

bool MeshPrefs::save(QString *error) const
{
    QDir().mkpath(m_dir);
    QJsonObject r;
    for (auto it = m_read.begin(); it != m_read.end(); ++it)
        r.insert(it.key(), double(it.value()));
    QSaveFile f(path());
    if (!f.open(QIODevice::WriteOnly)) {
        if (error) *error = f.errorString();
        return false;
    }
    f.write(QJsonDocument(QJsonObject{{QStringLiteral("version"), 1}, {QStringLiteral("sendingOn"), m_sendingOn}, {QStringLiteral("lastRead"), r}}).toJson());
    if (!f.commit()) {
        if (error) *error = f.errorString();
        return false;
    }
    return true;
}

} // namespace Taos::Survival
