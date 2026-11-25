// SPDX-License-Identifier: AGPL-3.0-or-later
#pragma once
// What the app itself remembers about the mesh, apart from the engine's store and the map's state.json: whether the
// owner has turned sending on (off until they do) and when each thread was last read. One small JSON file.
#include <QHash>
#include <QString>

namespace Taos::Survival {

class MeshPrefs
{
public:
    explicit MeshPrefs(QString dataDir);
    bool load();
    bool save(QString *error = nullptr) const;
    bool sendingOn() const { return m_sendingOn; }
    void setSendingOn(bool on) { m_sendingOn = on; }
    qint64 lastRead(const QString &thread) const { return m_read.value(thread, 0); }
    void setLastRead(const QString &thread, qint64 ms) { m_read.insert(thread, ms); }
    QString path() const { return m_dir + QStringLiteral("/mesh-prefs.json"); }

private:
    QString m_dir;
    bool m_sendingOn = false;
    QHash<QString, qint64> m_read;
};

} // namespace Taos::Survival
