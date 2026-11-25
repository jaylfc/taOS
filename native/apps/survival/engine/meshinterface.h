// SPDX-License-Identifier: AGPL-3.0-or-later
#pragma once
// The seam between taOStak and the mesh radio engine (taos-mesh, built separately). taOStak only talks to this
// interface: radios, messages, identities and send. Until the engine exists, FakeMeshBackend answers honestly: no
// radios, no messages, and send refuses with a reason. The engine's builder implements MeshBackend (for example over
// its local service API) and hands it to SurvivalBackend::setMesh(); nothing in the QML changes.
//
// All values are plain data. Ids are strings owned by the engine. Times are UTC milliseconds since the epoch.
#include <QList>
#include <QObject>
#include <QString>

namespace Taos::Survival {

struct MeshRadio
{
    QString id;            // engine id of the radio
    QString name;
    QString transport;     // meshtastic | meshcore | reticulum
    QString link;          // usb | ble | tcp
    QString state;         // connected | connecting | offline
    QString firmware;
    int batteryPercent = -1;   // -1: unknown
};

struct MeshIdentity
{
    QString id;            // node id, e.g. "!a1b2c3d4"
    QString callsign;
    QString owner;         // the taOS identity that owns it, or empty for a contact heard on the air
    bool isSelf = false;
    bool hasPosition = false;
    double lon = 0, lat = 0;
    qint64 lastHeardMs = 0;
};

struct MeshMessage
{
    QString id;
    QString threadId;      // a direct peer id or a channel id
    QString fromId;
    QString toId;
    QString text;
    qint64 timeMs = 0;
    bool outgoing = false;
    QString delivery;      // queued | sent | heard | acknowledged | failed (outgoing only)
    QString trust;         // encrypted | signed | claimed
};

struct MeshSendResult
{
    bool accepted = false;
    QString messageId;     // set when accepted
    QString error;         // plain words when not
};

class MeshBackend : public QObject
{
    Q_OBJECT
public:
    using QObject::QObject;
    /// false: there is no engine behind this backend (the UI says so instead of offering to send).
    virtual bool available() const = 0;
    virtual QString unavailableReason() const = 0;
    virtual QList<MeshRadio> radios() const = 0;
    virtual QList<MeshIdentity> identities() const = 0;
    virtual QList<MeshMessage> messages(const QString &threadId = QString()) const = 0;
    /// Queues a text message to a peer or channel. Never blocks on the radio.
    virtual MeshSendResult send(const QString &threadId, const QString &text) = 0;
Q_SIGNALS:
    void radiosChanged();
    void identitiesChanged();
    void messagesChanged();
};

/// The stand-in until the mesh engine exists: empty lists, send refused.
class FakeMeshBackend : public MeshBackend
{
    Q_OBJECT
public:
    using MeshBackend::MeshBackend;
    bool available() const override { return false; }
    QString unavailableReason() const override { return tr("The mesh engine is not installed yet."); }
    QList<MeshRadio> radios() const override { return {}; }
    QList<MeshIdentity> identities() const override { return {}; }
    QList<MeshMessage> messages(const QString &threadId = QString()) const override { return {}; }
    MeshSendResult send(const QString &, const QString &) override { return {false, QString(), unavailableReason()}; }
};

} // namespace Taos::Survival
