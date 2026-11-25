// SPDX-License-Identifier: AGPL-3.0-or-later
#pragma once
// A radio's saved settings per network, kept when the person moves it to another network and put back when they come
// back. Keyed by the firmware manager's device id (stable for one board on one USB port) and the network. The values
// are what the mesh engine reported (names, region, channels and their keys), so the file is private to the owner:
// mode 0600, written whole through a temporary file, and a failed write is reported, never swallowed.
#include <QString>
#include <QVariantMap>

namespace Taos::Survival {

class RadioProfiles
{
public:
    explicit RadioProfiles(QString dataDir);
    /// Replaces the saved profile of this board on this network. false + error in words when it cannot be written.
    bool save(const QString &deviceId, const QString &network, const QVariantMap &values, const QString &hardware, const QString &firmware, QString *error = nullptr);
    bool has(const QString &deviceId, const QString &network) const;
    /// {savedMs, hardware, firmware, values}, or an empty map.
    QVariantMap load(const QString &deviceId, const QString &network) const;
    /// Networks saved for this board, oldest first, as {network, savedMs, hardware, firmware}.
    QVariantList list(const QString &deviceId) const;
    QString path() const { return m_dir + QStringLiteral("/radio-profiles.json"); }

private:
    QVariantMap readAll() const;
    QString m_dir;
};

} // namespace Taos::Survival
