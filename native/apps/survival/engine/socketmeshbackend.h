// SPDX-License-Identifier: AGPL-3.0-or-later
#pragma once
// MeshBackend over the taos-mesh unix socket. It keeps a cache of what the engine last said and refreshes it when
// the engine's record stream says something was stored (coalesced), so the window never waits on the socket.
// Actions go out as requests and come back as actionDone(). It never calls `connect`, `discover` or anything else
// that touches a radio: it reads the store through the API and queues sends, nothing more.
#include <QHash>
#include <QSet>
#include <QTimer>
#include <memory>

#include "meshclient.h"
#include "meshinterface.h"

namespace Taos::Survival {

struct FetchAcc;

class SocketMeshBackend : public MeshBackend
{
    Q_OBJECT
public:
    explicit SocketMeshBackend(MeshClient *client, QObject *parent = nullptr);
    bool available() const override { return m_state == QLatin1String("connected"); }
    QString unavailableReason() const override;
    QList<MeshRadio> radios() const override { return m_radios; }
    QList<MeshIdentity> identities() const override { return m_ids; }
    QList<MeshMessage> messages(const QString &threadId = QString()) const override;
    MeshSendResult send(const QString &threadId, const QString &text) override;
    QString state() const override { return m_state; }
    bool receiveOnly() const override { return m_receiveOnly; }
    bool deviceConfigReadable() const override { return m_cfgRead; }
    bool deviceConfigWritable() const override { return m_cfgWrite; }
    bool canConnect() const override { return m_canConnect; }
    QList<MeshDevice> devices() const override { return m_devices; }
    QList<MeshContact> contacts() const override { return m_contacts; }
    QList<MeshAssignment> assignments() const override { return m_assign; }
    QList<MeshPlace> places() const override { return m_places; }
    QList<MeshTrailPoint> trail(const QString &node) const override;
    MeshDeviceConfig deviceConfig(const QString &radioId) const override;
    void refresh() override;
    void discoverDevices() override;
    void connectDevice(const QString &id, const QString &network) override;
    void forgetDevice(const QString &id) override;
    void releaseDevice(const QString &port) override;
    /// The engine's error in words a person can act on (permission, busy port).
    static QString explainConnectError(const QString &engineMessage);
    void assign(const QString &node, const QString &identity, const QString &name) override;
    void createContact(const QString &name, const QString &notes, const QString &assignNode) override;
    void setTold(const QString &contact, bool told, const QString &date) override;
    void link(const QString &contact, const QString &account) override;
    void requestDeviceConfig(const QString &radioId) override;
    void applyDeviceConfig(const QString &radioId, const QVariantMap &changes) override;

    // parsing, public so the tests can drive it with the engine's real JSON
    static MeshMessage messageFromRecord(const QJsonObject &r);
    static QString threadOf(const QJsonObject &r);

private:
    void recompute();
    void apply(const std::shared_ptr<FetchAcc> &acc);
    void done(const QString &what, const MeshReply &r);
    void fetchAll();
    MeshClient *m_client;
    QString m_state = QStringLiteral("absent");
    QString m_error;
    bool m_receiveOnly = true, m_cfgRead = false, m_cfgWrite = false, m_canConnect = false;
    QList<MeshDevice> m_devices;
    QStringList m_saved;
    QList<MeshRadio> m_radios;
    QList<MeshIdentity> m_ids;
    QList<MeshMessage> m_msgs;
    QList<MeshContact> m_contacts;
    QList<MeshAssignment> m_assign;
    QList<MeshPlace> m_places;
    QHash<QString, QList<MeshTrailPoint>> m_trails;
    QHash<QString, MeshDeviceConfig> m_cfg;
    QSet<QString> m_cfgWanted;
    QTimer m_coalesce;
    int m_inflight = 0;
    bool m_again = false;
    int m_gen = 0;
};

} // namespace Taos::Survival
