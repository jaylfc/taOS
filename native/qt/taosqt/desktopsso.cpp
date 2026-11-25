// SPDX-License-Identifier: AGPL-3.0-or-later
#include "taosqt/desktopsso.h"

#include <QDateTime>
#include <QEventLoop>
#include <QFile>
#include <QJsonDocument>
#include <QJsonObject>
#include <QLocalSocket>

using namespace Qt::StringLiterals;

namespace Taos {

QString DesktopSso::defaultSocketPath()
{
    const QString env = qEnvironmentVariable("TAOS_DESKTOP_SSO_SOCKET");
    return env.isEmpty() ? u"/run/taos/desktop-sso.sock"_s : env;
}

DesktopSso::DesktopSso(const QString &socketPath, const QUrl &baseUrl, DesktopSessionTokenSource *store, QObject *parent)
    : QObject(parent), m_socket(socketPath), m_base(baseUrl), m_store(store)
{
    m_timer.setInterval(5000);
    connect(&m_timer, &QTimer::timeout, this, &DesktopSso::tick);
}

// One line out, one line back. A nested event loop keeps the caller's own servers (tests) alive.
QByteArray DesktopSso::request(const QByteArray &line, bool *reached)
{
    *reached = false;
    QLocalSocket s;
    QEventLoop loop;
    QTimer guard;
    guard.setSingleShot(true);
    QByteArray buf;
    connect(&guard, &QTimer::timeout, &loop, &QEventLoop::quit);
    connect(&s, &QLocalSocket::connected, &s, [&] { s.write(line + "\n"); });
    connect(&s, &QLocalSocket::readyRead, &s, [&] {
        buf += s.readAll();
        if (buf.contains('\n') || buf.size() > 8192)
            loop.quit();
    });
    connect(&s, &QLocalSocket::disconnected, &loop, &QEventLoop::quit);
    connect(&s, &QLocalSocket::errorOccurred, &loop, &QEventLoop::quit);
    guard.start(4000);
    s.connectToServer(m_socket);
    loop.exec();
    const int nl = buf.indexOf('\n');
    if (nl < 0)
        return {};
    *reached = true;
    return buf.left(nl);
}

void DesktopSso::acquire()
{
    connect(this, &DesktopSso::finished, this, [this](Result r) {
        if (r == Unlinked) m_dueAt = QDateTime::currentMSecsSinceEpoch() + 600000;
        else if (r != Issued) m_dueAt = QDateTime::currentMSecsSinceEpoch() + 30000;
    }, Qt::SingleShotConnection);
    if (m_store->signedOut()) {
        Q_EMIT finished(SignedOut);
        return;
    }
    const QByteArray cur = m_store->stored();
    if (!cur.isEmpty() && cur != m_ours) {
        Q_EMIT finished(NotOurs); // a password sign-in (or another source) owns the file: leave it
        return;
    }
    if (!QFile::exists(m_socket)) {
        Q_EMIT finished(NoSocket);
        return;
    }
    bool reached = false;
    const QJsonObject r = QJsonDocument::fromJson(request(R"({"op":"session"})", &reached)).object();
    if (!reached) {
        Q_EMIT finished(NoSocket);
        return;
    }
    if (r.value(u"error"_s).toString() == u"unlinked"_s) {
        if (!m_ours.isEmpty() && m_store->stored() == m_ours)
            QFile::remove(m_store->path());
        m_ours.clear();
        Q_EMIT finished(Unlinked);
        return;
    }
    const QString token = r.value(u"session"_s).toString();
    if (token.isEmpty()) {
        Q_EMIT finished(Failed);
        return;
    }
    const QJsonObject snap{{u"v"_s, 1}, {u"base_url"_s, m_base.toString()}, {u"user_agent"_s, r.value(u"user_agent"_s)},
                           {u"session"_s, token}, {u"csrf"_s, r.contains(u"csrf"_s) ? r.value(u"csrf"_s) : QJsonValue(QJsonValue::Null)}};
    const QByteArray json = QJsonDocument(snap).toJson(QJsonDocument::Compact);
    m_store->remember(json);
    if (m_store->stored() != json) {
        Q_EMIT finished(Failed); // could not be written owner-only: say so, do not pretend
        return;
    }
    m_ours = json;
    const int expires = qMax(120, r.value(u"expires_in"_s).toInt(86400));
    m_renewMs = qMin(expires / 2, 12 * 3600) * 1000;
    m_dueAt = QDateTime::currentMSecsSinceEpoch() + m_renewMs;
    Q_EMIT finished(Issued);
}

void DesktopSso::tick()
{
    if (m_store->signedOut())
        return;
    const QByteArray cur = m_store->stored();
    if (!cur.isEmpty() && cur != m_ours)
        return; // someone else's session: leave it
    const bool vanished = cur.isEmpty() && !m_ours.isEmpty(); // the controller refused it and an app removed it
    if (!vanished && QDateTime::currentMSecsSinceEpoch() < m_dueAt)
        return;
    acquire();
}

void DesktopSso::run()
{
    m_timer.start();
    acquire();
}

void DesktopSso::stop()
{
    // Stopping the service is not a sign-out: restarting the shell pieces must not sign anyone out. Logging out
    // (`taos-session logout`, the launcher's Log out) revokes at the controller and removes the file.
    m_timer.stop();
}

} // namespace Taos
