// SPDX-License-Identifier: AGPL-3.0-or-later
#include "taosqt/app.h"

#include <QCoreApplication>
#include <QCryptographicHash>
#include <QDir>
#include <QFile>
#include <QGuiApplication>
#include <QJsonArray>
#include <QJsonDocument>
#include <QLocalServer>
#include <QLocalSocket>
#include <QStandardPaths>

#include <sys/stat.h>
#include <unistd.h>

using namespace Qt::StringLiterals;

namespace Taos {

void App::configure(const AppInfo &info)
{
    QCoreApplication::setApplicationName(info.appId);
    QCoreApplication::setOrganizationName(u"taOS"_s);
    QCoreApplication::setOrganizationDomain(u"taos.my"_s);
    if (!info.version.isEmpty())
        QCoreApplication::setApplicationVersion(info.version);
    QGuiApplication::setApplicationDisplayName(info.displayName);
    QGuiApplication::setDesktopFileName(info.appId); // Wayland app_id
}

SingleInstance::SingleInstance(const QString &appId, QObject *parent) : QObject(parent)
{
    // Per user: the uid keeps two users on one host from activating each other.
    const QString name = u"taos-%1-%2"_s.arg(appId).arg(quint32(::getuid()));
    // The socket lives in the user's private runtime directory, never in the shared /tmp. Without one (no login session)
    // there is no single-instance socket at all: the app runs, just not single (m_name stays empty, acquire says so once).
    const QString rt = qEnvironmentVariable("XDG_RUNTIME_DIR");
    if (!rt.isEmpty()) {
        const QString dir = rt + u"/taos"_s;
        QDir().mkpath(dir);
        ::chmod(QFile::encodeName(dir).constData(), 0700);
        m_name = dir + u"/"_s + name + u".sock"_s;
        // a unix socket path holds about 107 bytes: a long runtime directory or app id gets a short, stable file name
        if (QFile::encodeName(m_name).size() > 100)
            m_name = dir + u"/s-"_s + QString::fromLatin1(QCryptographicHash::hash(name.toUtf8(), QCryptographicHash::Sha1).toHex().left(16)) + u".sock"_s;
    }
}

SingleInstance::~SingleInstance()
{
    if (m_server)
        m_server->close();
}

bool SingleInstance::acquire(const QStringList &args)
{
    if (m_name.isEmpty()) {
        qWarning("taosqt: no XDG_RUNTIME_DIR: running without single-instance (no socket is made in a shared directory)");
        return true;
    }
    {
        QLocalSocket probe;
        probe.connectToServer(m_name);
        if (probe.waitForConnected(300)) {
            const QByteArray msg = QJsonDocument(QJsonArray::fromStringList(args)).toJson(QJsonDocument::Compact) + '\n';
            probe.write(msg);
            probe.waitForBytesWritten(1000);
            probe.disconnectFromServer();
            return false;
        }
    }
    QLocalServer::removeServer(m_name); // a stale socket from a crashed instance
    m_server = new QLocalServer(this);
    m_server->setSocketOptions(QLocalServer::UserAccessOption);
    if (!m_server->listen(m_name)) {
        // Cannot listen (read-only runtime dir...): run anyway, just not single.
        qWarning("taosqt: single-instance socket unavailable: %s", qUtf8Printable(m_server->errorString()));
        return true;
    }
    connect(m_server, &QLocalServer::newConnection, this, [this] {
        while (QLocalSocket *s = m_server->nextPendingConnection()) {
            connect(s, &QLocalSocket::disconnected, s, &QObject::deleteLater);
            connect(s, &QLocalSocket::readyRead, s, [this, s] {
                while (s->canReadLine()) {
                    const QJsonArray a = QJsonDocument::fromJson(s->readLine()).array();
                    QStringList args;
                    for (const QJsonValue &v : a)
                        args << v.toString();
                    Q_EMIT activationRequested(args);
                }
            });
        }
    });
    return true;
}

} // namespace Taos
