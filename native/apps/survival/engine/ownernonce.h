// SPDX-License-Identifier: AGPL-3.0-or-later
#pragma once
// The person's own press. Something that carries the person's authority (approving a shopping list an agent drafted)
// needs a single-use code that is made only when the person presses the button in the app: the press ARMS one action
// for one subject for a minute, and only then can a code be made for exactly that. A caller (the CLI, an agent) proves
// it holds such a code by presenting it over a unix socket that only the account's own uid may use (the kernel
// reports the peer's uid, SO_PEERCRED). Limit, stated plainly: another process of the same account can connect to the
// socket, but it cannot make a code unless the person has just pressed the button.
#include <QDateTime>
#include <QHash>
#include <QLocalServer>
#include <QObject>
#include <QString>

namespace Taos::Survival {

class OwnerNonceStore
{
public:
    /// The person pressed: this action on this subject may get a code in the next `ttlSec` seconds.
    void arm(const QString &action, const QString &subject, int ttlSec = 60);
    /// A code for what is armed (and only that); empty when nothing is armed. The arm is used up.
    QString mint(const QString &action, const QString &subject);
    /// True once per code, within 5 minutes, for the same action and subject.
    bool consume(const QString &nonce, const QString &action, const QString &subject);
private:
    struct Grant { QString action, subject; qint64 until; };
    QHash<QString, Grant> m_armed;      // key action|subject
    QHash<QString, Grant> m_codes;      // nonce
    void prune();
};

/// $XDG_RUNTIME_DIR/taos-survival/owner.sock
QString ownerSocketPath();

class OwnerNonceServer : public QObject
{
    Q_OBJECT
public:
    OwnerNonceServer(OwnerNonceStore *store, QObject *parent = nullptr) : QObject(parent), m_store(store) {}
    bool start(const QString &socketPath = ownerSocketPath(), QString *error = nullptr);
    bool listening() const { return m_server.isListening(); }
private:
    OwnerNonceStore *m_store;
    QLocalServer m_server;
};

/// Asks the running app whether `nonce` is a live code for this action and subject (and uses it up).
bool ownerNonceConsume(const QString &nonce, const QString &action, const QString &subject, QString *error = nullptr, const QString &socketPath = ownerSocketPath());

} // namespace Taos::Survival
