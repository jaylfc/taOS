// SPDX-License-Identifier: AGPL-3.0-or-later
#pragma once
// Moving one radio board from one mesh network to another, end to end, as one auditable sequence:
//   save its settings for the old network -> let go of its port in the mesh engine -> flash through the firmware manager
//   (typed board name, owner nonce, both checked by the manager) -> reconnect and let the mesh engine detect the new
//   network -> put back the settings saved earlier for that network, if there are any.
// Every step is read back before the next starts, and a failure stops the sequence with the step named and what the
// person can do next. Nothing here writes to a board except the firmware manager's flash call, and that call is made
// only after start() was given the board name the person typed. No UI: faces and the CLI drive the same object.
#include <QElapsedTimer>
#include <QObject>
#include <QPointer>
#include <QSet>
#include <QTimer>
#include <QVariantMap>
#include <functional>

#include "firmwareclient.h"
#include "meshinterface.h"
#include "radioprofile.h"

namespace Taos::Survival {

struct SwitchRequest
{
    QString port;          // the serial path the mesh engine has open (empty: the engine has nothing open on this board)
    QString radioId;       // the engine's id for the open radio (empty when none)
    QString deviceId;      // the firmware manager's id for the board
    QString entryId;       // the firmware to write
    QString fromNetwork;   // meshtastic | meshcore | "" when unknown
    QString toNetwork;
    QString typedName;     // what the person typed to confirm
    bool ackUnsigned = false;
    bool allowNoBackup = false;
    QString previousEntryId;   // the firmware that ran before, for the way back (may be empty)
};

class RadioSwitch : public QObject
{
    Q_OBJECT
public:
    enum class Step { Idle, Saving, Releasing, Flashing, Reconnecting, Restoring, Done, Failed };
    Q_ENUM(Step)
    RadioSwitch(MeshBackend *mesh, FirmwareClient *fw, RadioProfiles *profiles, QObject *parent = nullptr);
    void start(const SwitchRequest &req);
    Step step() const { return m_step; }
    static QString stepName(Step s);
    bool running() const { return m_step != Step::Idle && m_step != Step::Done && m_step != Step::Failed; }
    /// The step that failed (valid in Failed), and what went wrong in words.
    Step failedAt() const { return m_failedAt; }
    QString error() const { return m_error; }
    /// What each finished step did, in words, oldest first.
    QStringList notes() const { return m_notes; }
    QVariantMap flashResult() const { return m_flash; }
    /// The board's own recovery words after a failed flash, else general advice. Empty when not failed.
    QString recovery() const { return m_recovery; }
    bool profileSaved() const { return m_profileSaved; }
    int restored() const { return m_restored; }
    qint64 elapsedMs() const { return m_clock.isValid() ? m_clock.elapsed() : 0; }
    const SwitchRequest &request() const { return m_req; }
    /// Tests shorten these.
    void setTimeouts(int configMs, int reconnectMs) { m_configMs = configMs; m_reconnectMs = reconnectMs; }

Q_SIGNALS:
    void changed();

private:
    void go(Step s);
    void fail(const QString &why);
    void note(const QString &text);
    void waitFor(std::function<bool()> cond, int timeoutMs, std::function<void(bool)> then);
    void save();
    void release();
    void flash();
    void reconnect();
    void restore();
    void finish();
    MeshRadio radioOnPort() const;
    MeshBackend *m_b;
    FirmwareClient *m_c;
    RadioProfiles *m_p;
    SwitchRequest m_req;
    Step m_step = Step::Idle, m_failedAt = Step::Idle;
    QString m_error, m_recovery, m_newRadio;
    QStringList m_notes;
    QVariantMap m_flash;
    QSet<QString> m_knownPorts;
    bool m_profileSaved = false;
    int m_restored = 0, m_configMs = 12000, m_reconnectMs = 120000;
    quint64 m_run = 0;   // a new start() makes older callbacks harmless
    QElapsedTimer m_clock;
};

} // namespace Taos::Survival
