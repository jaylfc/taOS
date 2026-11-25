// SPDX-License-Identifier: AGPL-3.0-or-later
#pragma once
#include <QByteArray>
#include <QObject>
#include <QString>
#include <QTimer>
#include <QUrl>

#include "taosqt/desktopsession.h"

namespace Taos {

/// Desktop single sign-on, client side. Asks the controller's local unix socket for the session of the
/// Linux account this process runs as (the controller reads our uid from the kernel; we say nothing about
/// who we are), stores it as the desktop session ($XDG_RUNTIME_DIR/taos/session, 0600), and renews it
/// before it expires. Never overwrites a session someone else put there (a password sign-in), never signs a
/// person back in after a sign-out (logout revokes it at the controller and removes the file). When the socket is absent or the account is not linked it does nothing: the shared
/// sign-in form is the fallback.
class TAOSQT_EXPORT DesktopSso : public QObject
{
    Q_OBJECT
public:
    enum Result { Issued, Unlinked, NoSocket, Failed, SignedOut, NotOurs };
    Q_ENUM(Result)
    static QString defaultSocketPath();

    DesktopSso(const QString &socketPath, const QUrl &baseUrl, DesktopSessionTokenSource *store, const QString &kind = QStringLiteral("desktop"),
               QObject *parent = nullptr);
    /// The controller keeps one live session per (uid, kind): the desktop service is "desktop", a by-hand
    /// `taos-session acquire` (an ssh login, say) is "cli", so one never revokes the other.
    /// One request now. Emits finished().
    void acquire();
    /// Acquire, then keep the session current until stop().
    void run();
    /// Stop renewing. Does not sign out: that is `taos-session logout`.
    void stop();
    int renewMs() const { return m_renewMs; }

Q_SIGNALS:
    void finished(Taos::DesktopSso::Result result);

private:
    void tick();
    QByteArray request(const QByteArray &line, bool *reached);

    QString m_socket;
    QUrl m_base;
    DesktopSessionTokenSource *m_store;
    QByteArray m_ours;
    QString m_kind;
    QTimer m_timer;
    int m_renewMs = 0;
    qint64 m_dueAt = 0;
};

} // namespace Taos
