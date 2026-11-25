// SPDX-License-Identifier: AGPL-3.0-or-later
#pragma once
#include <QFileSystemWatcher>
#include <QPointer>

#include "taosqt/session.h"

namespace Taos {

/// ONE sign-in per desktop login: the session snapshot lives in
/// `$XDG_RUNTIME_DIR/taos/session` (directory 0700, file 0600, owner only,
/// written atomically) and every first-party app reads it. This is the default
/// TokenSource of the generated app entry point.
///
///  * acquire(): offers the stored session once; if the controller rejects it
///    the stale file is removed (only if it still holds that same session) and
///    the user is asked.
///  * remember(): after a password sign-in, writes the file; the other running
///    apps notice (file watcher) and sign in without asking.
///  * forget() (sign out): deletes the file; the other apps' next request fails
///    with 401 and they ask.
///
/// A file that is not owned by the user, or readable by group/other, is ignored
/// (never trusted). Without `$XDG_RUNTIME_DIR` nothing is stored and the source
/// behaves like PasswordTokenSource. The runtime directory is tmpfs, so the
/// session does not survive logout or reboot. The session cookie is bound to the
/// User-Agent the first app signed in with; it is stored in the snapshot, so
/// every app that restores it keeps sending that exact string.
class TAOSQT_EXPORT DesktopSessionTokenSource : public TokenSource
{
    Q_OBJECT
public:
    /// `runtimeDir` overrides `$XDG_RUNTIME_DIR` (tests).
    explicit DesktopSessionTokenSource(const QString &runtimeDir = QString(), QObject *parent = nullptr);
    QString name() const override { return QStringLiteral("desktop-session"); }
    void acquire(Session *session, const Error &rejected) override;
    void remember(const QByteArray &sessionJson) override;
    void forget() override;

    /// `<runtime dir>/taos/session`, or empty when there is no runtime dir.
    QString path() const { return m_path; }
    /// The stored snapshot when it passes the owner-only checks, else empty.
    QByteArray stored() const { return read(); }
    /// `<runtime dir>/taos/signed-out`: present after a sign-out (forget()) until the next sign-in
    /// (remember() or clearSignedOut()). The desktop single sign-on service does not sign a person
    /// back in while it exists, so signing out stays signed out.
    bool signedOut() const;
    void clearSignedOut();

private:
    QByteArray read() const;
    void changed();

    QString m_path;
    QPointer<Session> m_session;
    QFileSystemWatcher m_watcher;
    QByteArray m_offered;  // the snapshot we last gave the session
    QByteArray m_rejected; // a snapshot the controller refused
    QByteArray m_written;  // what we wrote ourselves (ignore our own change)
};

} // namespace Taos
