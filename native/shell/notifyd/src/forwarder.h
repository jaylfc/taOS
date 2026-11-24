// SPDX-License-Identifier: AGPL-3.0-or-later
#pragma once
// Sends buffered records to the controller, oldest first, one in flight, and
// deletes a record only when the controller has answered 200 with a JSON object
// holding "id". Credentials are the shared desktop session file the native apps
// use ($XDG_RUNTIME_DIR/taos/session), read afresh for every attempt, so a
// sign-in that happens after the shell started is picked up and a stale one is
// replaced by the next sign-in.
#include <QJsonObject>
#include <QNetworkAccessManager>
#include <QObject>
#include <QTimer>

#include "buffer.h"

namespace Taos::Notifyd {

/// What the session file holds (the Rust client's exported Session): the controller, the exact
/// User-Agent the session is bound to, the session token and the CSRF token.
struct Credentials
{
    QString baseUrl, userAgent, session, csrf;
    bool valid() const { return !baseUrl.isEmpty() && !session.isEmpty() && !userAgent.isEmpty(); }
};
/// Reads and checks the file (owner only, mode 0600, not a symlink). Empty/invalid when absent or untrusted.
Credentials readCredentials(const QString &runtimeDir);

/// What to do with the controller's answer. Pure, so it is tested without a network.
enum class Verdict { Confirm, Quarantine, RetryAfter, RetryBackoff };
Verdict classify(int httpStatus, const QByteArray &body);

class Forwarder : public QObject
{
    Q_OBJECT
public:
    struct Options
    {
        QString runtimeDir;
        int backoffMinMs = 1000;
        int backoffMaxMs = 60000;
        int requestTimeoutMs = 15000;
    };
    Forwarder(WriteAheadBuffer *buffer, const Options &o, QObject *parent = nullptr);
    /// New record in the buffer (or session may have changed): start sending if idle.
    void kick();
    int delivered() const { return m_delivered; }
    int quarantined() const { return m_quarantined; }
    QString status() const { return m_status; }
    /// Refused records whose visible warning could not be written yet (the id counter failed). They are
    /// safe in rejected/; the warning is raised as soon as the counter works again.
    int unwarned() const { return int(m_unwarned.size()); }
    QString warningError() const { return m_warnError; }

Q_SIGNALS:
    void idle(); ///< the buffer is empty and nothing is in flight
    void healthChanged(); ///< quarantined / unwarned counts changed (the status file reports them)

private:
    void pump();
    void send(const WriteAheadBuffer::Entry &e, const Credentials &c, bool csrfReplay);
    void retryLater(int ms, const QString &why);
    bool quarantine(const WriteAheadBuffer::Entry &e, int status);
    bool raiseRejectedWarning(const QString &app, int status); ///< false when it could not be numbered or stored
    void retryWarnings();

    struct Unwarned
    {
        QString app;
        int status;
    };

    WriteAheadBuffer *m_buf;
    Options m_o;
    QNetworkAccessManager m_net;
    QTimer m_timer;
    bool m_inflight = false;
    int m_backoff;
    int m_delivered = 0, m_quarantined = 0;
    QString m_csrf;   ///< a CSRF token the controller handed back, newer than the file's
    QString m_status;
    QList<Unwarned> m_unwarned;
    QString m_warnError;
};

} // namespace Taos::Notifyd
