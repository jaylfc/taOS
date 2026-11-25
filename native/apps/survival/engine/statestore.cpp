// SPDX-License-Identifier: AGPL-3.0-or-later
#include "statestore.h"

#include <QDateTime>
#include <QDir>
#include <QFile>
#include <QJsonDocument>
#include <QSaveFile>
#include <QUuid>

namespace Taos::Survival {

StateStore::StateStore(QString dataDir) : m_dir(std::move(dataDir)) {}

bool StateStore::load()
{
    QFile f(path());
    if (!f.exists())
        return true;
    if (!f.open(QIODevice::ReadOnly))
        return false;
    QJsonParseError pe;
    const QJsonDocument d = QJsonDocument::fromJson(f.readAll(), &pe);
    if (pe.error != QJsonParseError::NoError || !d.isObject())
        return false;   // a damaged file is reported, never silently replaced with an empty one
    const QJsonObject o = d.object();
    m_markers = o.value(QStringLiteral("markers")).toArray();
    m_contacts = o.value(QStringLiteral("contacts")).toArray();
    const QJsonObject me = o.value(QStringLiteral("me")).toObject();
    m_hasMe = me.contains(QStringLiteral("lon")) && me.contains(QStringLiteral("lat"));
    m_meLon = me.value(QStringLiteral("lon")).toDouble();
    m_meLat = me.value(QStringLiteral("lat")).toDouble();
    return true;
}

bool StateStore::save(QString *error) const
{
    QDir().mkpath(m_dir);
    QJsonObject o{{QStringLiteral("version"), 1}, {QStringLiteral("markers"), m_markers}, {QStringLiteral("contacts"), m_contacts}};
    if (m_hasMe)
        o.insert(QStringLiteral("me"), QJsonObject{{QStringLiteral("lon"), m_meLon}, {QStringLiteral("lat"), m_meLat}});
    QSaveFile f(path());
    if (!f.open(QIODevice::WriteOnly)) {
        if (error) *error = QStringLiteral("Cannot write %1").arg(f.errorString());
        return false;
    }
    f.write(QJsonDocument(o).toJson());
    if (!f.commit()) {
        if (error) *error = QStringLiteral("Cannot save: %1").arg(f.errorString());
        return false;
    }
    return true;
}

QString StateStore::addMarker(const QString &name, const QString &note, double lon, double lat)
{
    const QString id = QUuid::createUuid().toString(QUuid::WithoutBraces).left(8);
    m_markers.append(QJsonObject{{QStringLiteral("id"), id}, {QStringLiteral("name"), name}, {QStringLiteral("note"), note},
                                 {QStringLiteral("lon"), lon}, {QStringLiteral("lat"), lat},
                                 {QStringLiteral("created"), QDateTime::currentDateTimeUtc().toString(Qt::ISODate)}});
    return id;
}

bool StateStore::removeMarker(const QString &id)
{
    for (int i = 0; i < m_markers.size(); ++i)
        if (m_markers[i].toObject().value(QStringLiteral("id")).toString() == id) { m_markers.removeAt(i); return true; }
    return false;
}

QString StateStore::addContact(const QString &callsign, const QString &nodeId, const QString &note)
{
    const QString id = QUuid::createUuid().toString(QUuid::WithoutBraces).left(8);
    m_contacts.append(QJsonObject{{QStringLiteral("id"), id}, {QStringLiteral("callsign"), callsign}, {QStringLiteral("nodeId"), nodeId},
                                  {QStringLiteral("note"), note}});
    return id;
}

bool StateStore::removeContact(const QString &id)
{
    for (int i = 0; i < m_contacts.size(); ++i)
        if (m_contacts[i].toObject().value(QStringLiteral("id")).toString() == id) { m_contacts.removeAt(i); return true; }
    return false;
}

} // namespace Taos::Survival
