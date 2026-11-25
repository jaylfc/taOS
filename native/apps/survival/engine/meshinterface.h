// SPDX-License-Identifier: AGPL-3.0-or-later
#pragma once
// The seam between taOStak and the mesh radio engine (taos-mesh, a separate daemon). taOStak only talks to this
// interface: radios, messages, identities, positions and send. FakeMeshBackend answers honestly while no engine
// runs: no radios, no messages, and every action refuses with a reason. SocketMeshBackend (socketmeshbackend.h) is
// the real one, over the engine's unix socket.
//
// All values are plain data. Ids are strings owned by the engine. Times are UTC milliseconds since the epoch.
// Unknown is "has...: false" or -1, never a zero.
#include <QList>
#include <QObject>
#include <QString>
#include <QVariantMap>

namespace Taos::Survival {

struct MeshRadio
{
    QString id;            // engine id of the radio
    QString name;
    QString transport;     // meshtastic | meshcore | reticulum
    QString link;          // usb | ble | tcp
    QString state;         // connected | connecting | offline
    QString firmware;      // read from the radio itself; empty until it has said
    QString hardware;      // the board model the radio reports, e.g. "Heltec V3"; empty until it has said
    QString port;          // the serial path or ble:<address> the engine opened (what a release takes)
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
    QString threadId;      // a direct peer id or a channel id ("ch:<network>:<index>")
    QString fromId;
    QString toId;
    QString text;
    qint64 timeMs = 0;
    bool outgoing = false;
    QString delivery;      // queued | sent | heard | acknowledged | failed (outgoing only)
    QString trust;         // encrypted | signed | claimed
    // from the engine's own record of the frame
    QString network;
    QString senderIdentity;   // agent:<id> | person:<id> | contact:<id>, or empty when nobody is assigned
    QString senderName;       // the name that identity has, or empty
    bool hasPosition = false; // the sender's position when the frame was stored
    double lon = 0, lat = 0;
    qint64 positionTimeMs = 0;   // 0: the position carries no time
    bool positionIsLastKnown = false;
    bool hasSignal = false;
    double rssi = 0, snr = 0;
    int hops = -1;            // -1: unknown
};

struct MeshSendResult
{
    bool accepted = false;
    QString messageId;     // set when accepted and the engine already answered
    QString error;         // plain words when not
};

/// A named person who is not a taOS user.
struct MeshContact
{
    QString id;
    QString name;
    QString notes;
    QString linkedAccount;   // person:<id>, or empty
    bool told = false;       // the contact has been told positions are stored
    QString toldDate;        // the date the owner confirmed it
    bool locationsHidden = false;
};

/// A radio (node id) assigned to an identity.
struct MeshAssignment
{
    QString node;
    QString identity;        // agent:<id> | person:<id> | contact:<id>
    QString name;            // the display name given at assignment, may be empty
};

/// The last known place of someone.
struct MeshPlace
{
    QString node;
    QString identity;        // empty when unassigned
    QString name;
    QString network;
    double lon = 0, lat = 0;
    qint64 positionTimeMs = 0;   // when the position was taken (heard time when the radio gave none)
    qint64 heardMs = 0;
};

struct MeshTrailPoint
{
    double lon = 0, lat = 0;
    qint64 timeMs = 0;
};

/// A radio port the engine could open: a USB serial device, or a Bluetooth radio BlueZ already knows.
struct MeshDevice
{
    QString id;          // what radios.connect takes: a serial path, or ble:<address>
    QString kind;        // usb | ble
    QString label;       // product or name, in words
    QString detail;      // path and ids, or address and signal
    QString hint;        // what the engine guesses it is
    bool saved = false;  // the engine opens it again after a restart
    bool supported = true;   // false: the engine cannot open this kind yet
};

/// What the engine can tell about one radio's own settings. `values` is the engine's JSON as a map; its shape is in
/// native/mesh/docs/DEVICE-SETTINGS.md. `state`: loading | unsupported | ok | error.
struct MeshDeviceConfig
{
    QString radioId;
    QString state = QStringLiteral("loading");
    QString note;
    QVariantMap values;
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
    /// Queues a text message to a peer or channel. Never blocks on the radio or the socket; the outcome of the
    /// engine's own answer comes as actionDone().
    virtual MeshSendResult send(const QString &threadId, const QString &text) = 0;

    /// connected | connecting | absent (no engine) | failed (the engine answers but is unwell)
    virtual QString state() const { return QStringLiteral("absent"); }
    /// true: the engine refuses to write any send to a radio.
    virtual bool receiveOnly() const { return true; }
    /// true when the engine reports radio settings (read) and accepts them (write).
    virtual bool deviceConfigReadable() const { return false; }
    virtual bool deviceConfigWritable() const { return false; }
    /// true when the engine can be told to open and forget radios.
    virtual bool canConnect() const { return false; }
    virtual QList<MeshDevice> devices() const { return {}; }
    virtual QList<MeshContact> contacts() const { return {}; }
    virtual QList<MeshAssignment> assignments() const { return {}; }
    virtual QList<MeshPlace> places() const { return {}; }
    virtual QList<MeshTrailPoint> trail(const QString &node) const { Q_UNUSED(node); return {}; }
    virtual MeshDeviceConfig deviceConfig(const QString &radioId) const { MeshDeviceConfig c; c.radioId = radioId; c.state = QStringLiteral("unsupported"); return c; }

    // Actions answer through actionDone(what, ok, error). The default refuses in words.
    virtual void refresh() {}
    /// Looks for ports (read only: a listing, nothing is opened). Answers through devicesChanged().
    virtual void discoverDevices() {}
    /// Opens a port through the engine (it runs a read-only handshake and remembers the port). actionDone("connect:<id>").
    virtual void connectDevice(const QString &id, const QString &network = QString()) { Q_UNUSED(id); Q_UNUSED(network); refuse(QStringLiteral("connect")); }
    virtual void forgetDevice(const QString &id) { Q_UNUSED(id); refuse(QStringLiteral("forget")); }
    /// Closes the radio on this port and stops the engine reopening it, so the firmware flasher can have the port.
    /// actionDone("release:<port>").
    virtual void releaseDevice(const QString &port) { Q_EMIT actionDone(QStringLiteral("release:") + port, false, unavailableReason()); }
    virtual void assign(const QString &node, const QString &identity, const QString &name) { Q_UNUSED(node); Q_UNUSED(identity); Q_UNUSED(name); refuse(QStringLiteral("assign")); }
    virtual void createContact(const QString &name, const QString &notes, const QString &assignNode = QString()) { Q_UNUSED(name); Q_UNUSED(notes); Q_UNUSED(assignNode); refuse(QStringLiteral("contact")); }
    virtual void setTold(const QString &contact, bool told, const QString &date) { Q_UNUSED(contact); Q_UNUSED(told); Q_UNUSED(date); refuse(QStringLiteral("told")); }
    virtual void link(const QString &contact, const QString &account) { Q_UNUSED(contact); Q_UNUSED(account); refuse(QStringLiteral("link")); }
    virtual void requestDeviceConfig(const QString &radioId) { Q_UNUSED(radioId); }
    /// Writes settings to a radio. The only caller is the confirm step of the settings screens; the answer is a
    /// refusal in words unless deviceConfigWritable().
    virtual void applyDeviceConfig(const QString &radioId, const QVariantMap &changes, bool confirmRegion = false) { Q_UNUSED(radioId); Q_UNUSED(changes); Q_UNUSED(confirmRegion); refuse(QStringLiteral("device")); }

Q_SIGNALS:
    void radiosChanged();
    void identitiesChanged();
    void messagesChanged();
    void stateChanged();
    void devicesChanged();
    void actionDone(const QString &what, bool ok, const QString &error);

protected:
    void refuse(const QString &what) { Q_EMIT actionDone(what, false, unavailableReason()); }
};

/// The stand-in while no engine runs: empty lists, every action refused with the reason.
class FakeMeshBackend : public MeshBackend
{
    Q_OBJECT
public:
    using MeshBackend::MeshBackend;
    bool available() const override { return false; }
    QString unavailableReason() const override { return m_reason.isEmpty() ? tr("The mesh engine is not running.") : m_reason; }
    void setReason(const QString &r) { m_reason = r; }
    QList<MeshRadio> radios() const override { return {}; }
    QList<MeshIdentity> identities() const override { return {}; }
    QList<MeshMessage> messages(const QString &threadId = QString()) const override { Q_UNUSED(threadId); return {}; }
    MeshSendResult send(const QString &, const QString &) override { return {false, QString(), unavailableReason()}; }

private:
    QString m_reason;
};

} // namespace Taos::Survival
