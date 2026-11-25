// SPDX-License-Identifier: AGPL-3.0-or-later
#include "ownernonce.h"

#include <QDir>
#include <QFile>
#include <QFileInfo>
#include <QJsonDocument>
#include <QJsonObject>
#include <QLocalSocket>
#include <QUuid>

#include <sys/socket.h>
#include <unistd.h>

namespace Taos::Survival {

void OwnerNonceStore::prune()
{
    const qint64 now = QDateTime::currentMSecsSinceEpoch();
    for (auto it = m_armed.begin(); it != m_armed.end();) { if (it->until < now) it = m_armed.erase(it); else ++it; }
    for (auto it = m_codes.begin(); it != m_codes.end();) { if (it->until < now) it = m_codes.erase(it); else ++it; }
}
void OwnerNonceStore::arm(const QString &action, const QString &subject, int ttlSec)
{
    prune();
    m_armed.insert(action + QLatin1Char('|') + subject, {action, subject, QDateTime::currentMSecsSinceEpoch() + qint64(ttlSec) * 1000});
}
QString OwnerNonceStore::mint(const QString &action, const QString &subject)
{
    prune();
    const QString key = action + QLatin1Char('|') + subject;
    if (!m_armed.contains(key))
        return {};
    m_armed.remove(key);
    const QString nonce = QUuid::createUuid().toString(QUuid::Id128);
    m_codes.insert(nonce, {action, subject, QDateTime::currentMSecsSinceEpoch() + 5 * 60 * 1000});
    return nonce;
}
bool OwnerNonceStore::consume(const QString &nonce, const QString &action, const QString &subject)
{
    prune();
    auto it = m_codes.find(nonce);
    if (it == m_codes.end() || nonce.isEmpty())
        return false;
    const bool ok = it->action == action && it->subject == subject;
    m_codes.erase(it);   // used up whether or not it matched: a wrong guess burns the code
    return ok;
}

QString ownerSocketPath()
{
    const QString rt = qEnvironmentVariable("XDG_RUNTIME_DIR");
    return (rt.isEmpty() ? QStringLiteral("/tmp/taos-survival-") + QString::number(getuid()) : rt) + QStringLiteral("/taos-survival/owner.sock");
}

bool OwnerNonceServer::start(const QString &socketPath, QString *error)
{
    const QString dir = QFileInfo(socketPath).absolutePath();
    QDir().mkpath(dir);
    QFile::setPermissions(dir, QFileDevice::ReadOwner | QFileDevice::WriteOwner | QFileDevice::ExeOwner);   // 0700
    QLocalServer::removeServer(socketPath);
    m_server.setSocketOptions(QLocalServer::UserAccessOption);   // the socket file is the owner's only
    if (!m_server.listen(socketPath)) {
        if (error) *error = m_server.errorString();
        return false;
    }
    connect(&m_server, &QLocalServer::newConnection, this, [this] {
        while (QLocalSocket *c = m_server.nextPendingConnection()) {
            ucred cred{};
            socklen_t len = sizeof cred;
            const bool ours = getsockopt(int(c->socketDescriptor()), SOL_SOCKET, SO_PEERCRED, &cred, &len) == 0 && cred.uid == getuid();
            connect(c, &QLocalSocket::readyRead, c, [this, c, ours] {
                const QByteArray line = c->readLine();
                bool ok = false;
                if (ours) {
                    const QJsonObject o = QJsonDocument::fromJson(line).object();
                    if (o.value(QStringLiteral("op")).toString() == QStringLiteral("consume"))
                        ok = m_store->consume(o.value(QStringLiteral("nonce")).toString(), o.value(QStringLiteral("action")).toString(), o.value(QStringLiteral("subject")).toString());
                }
                c->write(QJsonDocument(QJsonObject{{QStringLiteral("ok"), ok}}).toJson(QJsonDocument::Compact) + "\n");
                c->flush();
                c->disconnectFromServer();
            });
            connect(c, &QLocalSocket::disconnected, c, &QObject::deleteLater);
        }
    });
    return true;
}

bool ownerNonceConsume(const QString &nonce, const QString &action, const QString &subject, QString *error, const QString &socketPath)
{
    QLocalSocket s;
    s.connectToServer(socketPath);
    if (!s.waitForConnected(2000)) {
        if (error) *error = QStringLiteral("The Survival Studio app is not running, so there is no way to confirm the person pressed Approve.");
        return false;
    }
    s.write(QJsonDocument(QJsonObject{{QStringLiteral("op"), QStringLiteral("consume")}, {QStringLiteral("nonce"), nonce}, {QStringLiteral("action"), action},
                                      {QStringLiteral("subject"), subject}}).toJson(QJsonDocument::Compact) + "\n");
    s.waitForBytesWritten(2000);
    if (!s.waitForReadyRead(3000)) {
        if (error) *error = QStringLiteral("The app did not answer.");
        return false;
    }
    const bool ok = QJsonDocument::fromJson(s.readLine()).object().value(QStringLiteral("ok")).toBool();
    if (!ok && error) *error = QStringLiteral("That code is not valid. Press Approve in the app to get a new one-time code.");
    return ok;
}

} // namespace Taos::Survival
