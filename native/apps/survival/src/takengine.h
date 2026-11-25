// SPDX-License-Identifier: AGPL-3.0-or-later
#pragma once
// The faces' view of the mesh and the firmware engines for taOStak: QObjects with properties and invokable methods for
// QML. All logic lives in engine/ and in the daemons; these classes translate to and from QML values, remember what
// the window shows (unread marks, the owner's "sending on" switch) and refuse actions in words when they must.
#include <QObject>
#include <QTimer>
#include <memory>
#include <QVariantList>
#include <QVariantMap>

#include "firmwareclient.h"
#include "meshinterface.h"
#include "meshprefs.h"
#include "radioprofile.h"
#include "radioswitch.h"


namespace Taos::Survival {

class FirmwareEngine;

class TakEngine : public QObject
{
    Q_OBJECT
    Q_PROPERTY(QString state READ state NOTIFY changed)               // connected | connecting | absent | failed
    Q_PROPERTY(bool available READ available NOTIFY changed)
    Q_PROPERTY(QString reason READ reason NOTIFY changed)
    Q_PROPERTY(bool receiveOnly READ receiveOnly NOTIFY changed)
    Q_PROPERTY(bool sendingOn READ sendingOn WRITE setSendingOn NOTIFY changed)
    Q_PROPERTY(bool canSend READ canSend NOTIFY changed)
    Q_PROPERTY(QString sendBlocked READ sendBlocked NOTIFY changed)   // empty when sending is possible
    Q_PROPERTY(bool configReadable READ configReadable NOTIFY changed)
    Q_PROPERTY(bool configWritable READ configWritable NOTIFY changed)
    Q_PROPERTY(bool canConnect READ canConnect NOTIFY changed)
    Q_PROPERTY(QVariantList devices READ devices NOTIFY changed)
    Q_PROPERTY(QString connecting READ connecting NOTIFY changed)           // the port being opened now, or empty
    Q_PROPERTY(QVariantMap connectErrors READ connectErrors NOTIFY changed) // port -> what went wrong, in words
    Q_PROPERTY(QVariantList radios READ radios NOTIFY changed)
    /// One row per physical radio: what the engine has open, what is plugged in but not open, and what the firmware manager
    /// sees on the same port, joined on the port. Fields are in takengine.cpp (radioRows).
    Q_PROPERTY(QVariantList radioRows READ radioRows NOTIFY changed)
    /// The networks that have a radio, a person or a conversation, for the filter: [{id, name, radios, people}]. More than one
    /// means the screens show a network badge and a filter.
    Q_PROPERTY(QVariantList networks READ networks NOTIFY changed)
    Q_PROPERTY(QString networkFilter READ networkFilter WRITE setNetworkFilter NOTIFY changed)   // "" = all networks
    Q_PROPERTY(QVariantList people READ people NOTIFY changed)
    Q_PROPERTY(QVariantList contacts READ contacts NOTIFY changed)
    Q_PROPERTY(QVariantList threads READ threads NOTIFY changed)
    Q_PROPERTY(QVariantList places READ places NOTIFY changed)
    Q_PROPERTY(int unreadTotal READ unreadTotal NOTIFY changed)
    Q_PROPERTY(QString notice READ notice NOTIFY noticeChanged)       // the last action's outcome in words
    Q_PROPERTY(bool noticeIsError READ noticeIsError NOTIFY noticeChanged)
    Q_PROPERTY(bool canStartEngine READ canStartEngine CONSTANT)
    /// The scripted --demo launch states (they drive the screens for pictures, including a flash plan) work only when
    /// TAOS_SURVIVAL_DEMO is set in the environment. A person launching the app never has it set.
    Q_PROPERTY(bool demoEnabled READ demoEnabled CONSTANT)
public:
    TakEngine(MeshBackend *backend, const QString &dataDir, QObject *parent = nullptr);
    void setBackend(MeshBackend *b);
    MeshBackend *backend() const { return m_b; }
    QString state() const { return m_b->state(); }
    bool available() const { return m_b->available(); }
    QString reason() const { return m_b->unavailableReason(); }
    bool receiveOnly() const { return m_b->receiveOnly(); }
    bool sendingOn() const { return m_prefs.sendingOn(); }
    void setSendingOn(bool on);
    bool canSend() const { return sendBlocked().isEmpty(); }
    QString sendBlocked() const;
    bool configReadable() const { return m_b->deviceConfigReadable(); }
    bool configWritable() const { return m_b->deviceConfigWritable(); }
    bool canConnect() const { return m_b->canConnect(); }
    QVariantList devices() const;
    QString connecting() const { return m_connecting; }
    QVariantMap connectErrors() const { return m_connectErrors; }
    Q_INVOKABLE void discover() { m_b->discoverDevices(); }
    /// Opens a port through the engine; the engine runs a read-only handshake and remembers the port.
    Q_INVOKABLE void connectDevice(const QString &id);
    Q_INVOKABLE void forgetDevice(const QString &id) { m_b->forgetDevice(id); }
    QVariantList radios() const;
    QVariantList radioRows() const;
    Q_INVOKABLE QVariantMap radioRow(const QString &key) const;
    QVariantList networks() const;
    QString networkFilter() const { return m_filter; }
    void setNetworkFilter(const QString &n);
    void setFirmware(FirmwareEngine *fw);
    RadioProfiles *profiles() { return &m_profiles; }
    /// Which network a node id belongs to (the engine's own rule: Meshtastic ids start with "!").
    Q_INVOKABLE QString networkOfNode(const QString &node) const;
    /// Empty when a message to this thread can go out; else why not, naming the network (needs a connected radio on it).
    Q_INVOKABLE QString sendBlockedFor(const QString &threadId) const;
    /// "Sends from <radio> on <network>", or empty when no radio could carry it.
    Q_INVOKABLE QString sendVia(const QString &threadId) const;
    QVariantList people() const;
    QVariantList contacts() const;
    QVariantList threads() const;
    QVariantList places() const;
    int unreadTotal() const;
    QString notice() const { return m_notice; }
    bool noticeIsError() const { return m_noticeError; }
    bool canStartEngine() const;
    bool demoEnabled() const { return qEnvironmentVariableIsSet("TAOS_SURVIVAL_DEMO"); }

    /// A thread's messages in time order, each with who is behind it, the position and signal in words.
    Q_INVOKABLE QVariantList messages(const QString &threadId) const;
    Q_INVOKABLE QVariantMap thread(const QString &threadId) const;
    Q_INVOKABLE void markRead(const QString &threadId);
    /// Returns an error in words, or an empty string when the message was handed to the engine.
    Q_INVOKABLE QString send(const QString &threadId, const QString &text);
    Q_INVOKABLE QVariantMap person(const QString &node) const;
    Q_INVOKABLE QVariantList trail(const QString &node) const;
    Q_INVOKABLE QString ago(double ms) const;
    Q_INVOKABLE QString displayName(const QString &node) const;
    Q_INVOKABLE void assign(const QString &node, const QString &identity, const QString &name = QString());
    Q_INVOKABLE void createContact(const QString &name, const QString &notes, const QString &assignNode = QString());
    Q_INVOKABLE void confirmTold(const QString &contactId);
    Q_INVOKABLE void clearTold(const QString &contactId);
    Q_INVOKABLE void link(const QString &contactId, const QString &accountId);
    Q_INVOKABLE void refresh() { m_b->refresh(); }
    Q_INVOKABLE void requestConfig(const QString &radioId) { m_b->requestDeviceConfig(radioId); }
    Q_INVOKABLE QVariantMap config(const QString &radioId) const;
    /// Applying settings: refused in words unless the engine reports write support. Never called by tests.
    Q_INVOKABLE QString applyConfig(const QString &radioId, const QVariantMap &changes);
    Q_INVOKABLE QStringList qr(const QString &text) const;
    Q_INVOKABLE void startEngine();
    Q_INVOKABLE void clearNotice();
    /// The identity string for an assignment: agent:<id>, person:<id> or contact:<id>.
    Q_INVOKABLE QString identityOf(const QString &kind, const QString &id) const { return id.trimmed().isEmpty() ? QString() : kind + QLatin1Char(':') + id.trimmed(); }

Q_SIGNALS:
    void changed();
    void noticeChanged();

private:
    void setNotice(const QString &text, bool error);
    QString nameForIdentity(const QString &identity) const;
    QString kindOf(const QString &identity) const;
    MeshBackend *m_b;
    MeshPrefs m_prefs;
    RadioProfiles m_profiles;
    FirmwareEngine *m_fw = nullptr;
    QString m_filter;
    QString m_notice, m_connecting;
    QVariantMap m_connectErrors;
    bool m_noticeError = false;
    QTimer m_noticeTimer;
};

/// The switch sequence for QML: starts it from a row of the radios list and the firmware chosen, and reports its progress.
class SwitchEngine : public QObject
{
    Q_OBJECT
    Q_PROPERTY(QString step READ step NOTIFY changed)             // idle | saving | releasing | flashing | reconnecting | restoring | done | failed
    Q_PROPERTY(bool running READ running NOTIFY changed)
    Q_PROPERTY(QString failedAt READ failedAt NOTIFY changed)
    Q_PROPERTY(QString error READ error NOTIFY changed)
    Q_PROPERTY(QString recovery READ recovery NOTIFY changed)
    Q_PROPERTY(QStringList notes READ notes NOTIFY changed)
    Q_PROPERTY(QVariantList stages READ stages NOTIFY changed)    // [{id, label, state: pending|active|done|failed}]
    Q_PROPERTY(QString elapsed READ elapsed NOTIFY changed)
    Q_PROPERTY(bool profileSaved READ profileSaved NOTIFY changed)
    Q_PROPERTY(int restored READ restored NOTIFY changed)
    Q_PROPERTY(QVariantMap flash READ flash NOTIFY changed)
    /// The library entry of the firmware the radio ran before the last switch began ("" when it is not stored): the way back.
    Q_PROPERTY(QString previous READ previous NOTIFY changed)
    /// The radio row as it was when the switch began (the live row changes as the board is released and comes back).
    Q_PROPERTY(QVariantMap subject READ subject NOTIFY changed)
public:
    SwitchEngine(TakEngine *tak, FirmwareEngine *fw, FirmwareClient *client, QObject *parent = nullptr);
    QString step() const { return m_sw ? RadioSwitch::stepName(m_sw->step()) : QStringLiteral("idle"); }
    bool running() const { return m_sw && m_sw->running(); }
    QString failedAt() const { return m_sw ? RadioSwitch::stepName(m_sw->failedAt()) : QString(); }
    QString error() const { return m_sw ? m_sw->error() : QString(); }
    QString recovery() const { return m_sw ? m_sw->recovery() : QString(); }
    QStringList notes() const { return m_sw ? m_sw->notes() : QStringList(); }
    QVariantList stages() const;
    QString elapsed() const;
    bool profileSaved() const { return m_sw && m_sw->profileSaved(); }
    int restored() const { return m_sw ? m_sw->restored() : 0; }
    QVariantMap flash() const { return m_sw ? m_sw->flashResult() : QVariantMap(); }
    QString previous() const { return m_previous; }
    QVariantMap subject() const { return m_subject; }
    /// Starts the switch for the radio row `rowKey` to the stored firmware `entryId`. The board name is what the person
    /// typed. Returns an error in words (nothing started) or an empty string.
    Q_INVOKABLE QString begin(const QString &rowKey, const QString &entryId, const QString &typedName, bool ackUnsigned);
    Q_INVOKABLE void reset();
    /// The firmware the row is running now, as a library entry id, so the way back is one tap: "" when it is not stored.
    Q_INVOKABLE QString previousEntry(const QString &rowKey) const;

Q_SIGNALS:
    void changed();

private:
    TakEngine *m_tak;
    FirmwareEngine *m_fw;
    FirmwareClient *m_c;
    std::unique_ptr<RadioSwitch> m_sw;
    QString m_previous;
    QVariantMap m_subject;
    QTimer *m_tick;
};

class FirmwareEngine : public QObject
{
    Q_OBJECT
    Q_PROPERTY(QString state READ state NOTIFY changed)            // unknown | offline | ready
    Q_PROPERTY(QString error READ error NOTIFY changed)
    Q_PROPERTY(QVariantList library READ library NOTIFY changed)   // entries, sorted by network, board, version
    Q_PROPERTY(QVariantList pending READ pending NOTIFY changed)   // licences not accepted yet: {network,name,licence_id,licence_url}
    Q_PROPERTY(QVariantList accepted READ accepted NOTIFY changed)
    Q_PROPERTY(QVariantList devices READ devices NOTIFY changed)
    Q_PROPERTY(QVariantMap boards READ boards NOTIFY changed)
    Q_PROPERTY(bool busy READ busy NOTIFY changed)
    Q_PROPERTY(QString refreshNote READ refreshNote NOTIFY changed)
    Q_PROPERTY(QVariantMap plan READ plan NOTIFY planChanged)      // the last plan reply, {} when none
    Q_PROPERTY(QString planError READ planError NOTIFY planChanged)
    Q_PROPERTY(QVariantMap result READ result NOTIFY resultChanged) // the last flash reply
    Q_PROPERTY(QString totalSize READ totalSize NOTIFY changed)
    Q_PROPERTY(QVariantList deviceFirmware READ deviceFirmware NOTIFY changed)
public:
    explicit FirmwareEngine(FirmwareClient *client, const QString &accountName, QObject *parent = nullptr);
    QString state() const { return m_state; }
    QString error() const { return m_error; }
    QVariantList library() const { return m_library; }
    QVariantList pending() const { return m_pending; }
    QVariantList accepted() const { return m_accepted; }
    QVariantList devices() const { return m_devices; }
    QVariantMap boards() const { return m_boards; }
    bool busy() const { return m_busy; }
    QString refreshNote() const { return m_refreshNote; }
    QVariantMap plan() const { return m_plan; }
    QString planError() const { return m_planError; }
    QVariantMap result() const { return m_result; }
    QString totalSize() const;
    QVariantList deviceFirmware() const { return m_devFw; }
    Q_INVOKABLE void reload();
    Q_INVOKABLE void detect();
    Q_INVOKABLE void refreshLibrary();
    Q_INVOKABLE void acceptLicence(const QString &network);
    /// Entries that can go on this device (the engine's own `usable_now` view), for the network chooser.
    Q_INVOKABLE void planFlash(const QString &deviceId, const QString &entryId);
    Q_INVOKABLE void clearPlan();
    /// Runs the flash the plan described. The caller (the confirm step) passes what the person typed. This is the only
    /// path that can write to a board and it is never called in tests or screenshots with a real device.
    Q_INVOKABLE void runFlash(const QString &deviceId, const QString &entryId, const QString &typedName, bool ackUnsigned, bool allowNoBackup);
    /// The board's name as the person sees and types it (from the engine's board list).
    Q_INVOKABLE QString boardName(const QString &boardId) const;
    Q_INVOKABLE void clearResult();
    Q_INVOKABLE QString sizeText(double bytes) const;
    Q_INVOKABLE QString dateText(const QString &iso) const;
    Q_INVOKABLE void startService();
    /// Loads the firmware that fits a device (the engine's own view) into deviceFirmware.
    Q_INVOKABLE void loadDeviceFirmware(const QString &deviceId);

Q_SIGNALS:
    void changed();
    void planChanged();
    void resultChanged();

private:
    FirmwareClient *m_c;
    QString m_account;
    QString m_state = QStringLiteral("unknown"), m_error, m_refreshNote, m_planError;
    QVariantList m_library, m_pending, m_accepted, m_devices, m_devFw;
    QVariantMap m_boards, m_plan, m_result;
    bool m_busy = false;
};

} // namespace Taos::Survival
