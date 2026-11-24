#include "taosqt/app.h"

#include <QCoreApplication>
#include <QDir>
#include <QGuiApplication>
#include <QJsonArray>
#include <QJsonDocument>
#include <QLocalServer>
#include <QLocalSocket>
#include <QStandardPaths>

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
    m_name = u"taos-%1-%2"_s.arg(appId).arg(quint32(::getuid()));
}

SingleInstance::~SingleInstance()
{
    if (m_server)
        m_server->close();
}

bool SingleInstance::acquire(const QStringList &args)
{
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
