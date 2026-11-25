// SPDX-License-Identifier: AGPL-3.0-or-later
#pragma once
// The person's own map data: markers, the manually set position and contacts, in one JSON file in the data folder,
// written atomically. Nothing here leaves the device.
#include <QJsonArray>
#include <QJsonObject>
#include <QString>

namespace Taos::Survival {

class StateStore
{
public:
    explicit StateStore(QString dataDir);
    bool load();
    bool save(QString *error = nullptr) const;
    QJsonArray markers() const { return m_markers; }
    QJsonArray contacts() const { return m_contacts; }
    /// Adds a marker and returns its id.
    QString addMarker(const QString &name, const QString &note, double lon, double lat);
    bool removeMarker(const QString &id);
    QString addContact(const QString &callsign, const QString &nodeId, const QString &note);
    bool removeContact(const QString &id);
    bool hasMe() const { return m_hasMe; }
    double meLon() const { return m_meLon; }
    double meLat() const { return m_meLat; }
    void setMe(double lon, double lat) { m_meLon = lon; m_meLat = lat; m_hasMe = true; }
    void clearMe() { m_hasMe = false; }
    QString path() const { return m_dir + QStringLiteral("/state.json"); }

private:
    QString m_dir;
    QJsonArray m_markers, m_contacts;
    bool m_hasMe = false;
    double m_meLon = 0, m_meLat = 0;
};

} // namespace Taos::Survival
