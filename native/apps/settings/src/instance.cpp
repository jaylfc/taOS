#include "instance.h"

#include <QDir>
#include <QFile>
#include <QJsonArray>
#include <QJsonDocument>
#include <QLocalServer>
#include <QLocalSocket>

#include <sys/stat.h>
#include <unistd.h>

using namespace Qt::StringLiterals;

namespace TaosSettings {

QString InstanceGuard::defaultSocketPath()
{
    QString base = qEnvironmentVariable("XDG_RUNTIME_DIR");
    if (base.isEmpty())
        base = QDir::tempPath() + u"/taos-run-"_s + QString::number(::getuid());
    const QString dir = base + u"/taos"_s;
    QDir().mkpath(dir);
    ::chmod(QFile::encodeName(dir).constData(), 0700);
    return dir + u"/settings.sock"_s;
}

InstanceGuard::InstanceGuard(QObject *parent) : QObject(parent), m_path(defaultSocketPath()) {}

InstanceGuard::~InstanceGuard()
{
    if (m_server)
        m_server->close();
}

bool InstanceGuard::acquire(const QStringList &args)
{
    {
        QLocalSocket probe;
        probe.connectToServer(m_path);
        if (probe.waitForConnected(300)) {
            probe.write(QJsonDocument(QJsonArray::fromStringList(args)).toJson(QJsonDocument::Compact) + '\n');
            probe.waitForBytesWritten(1000);
            probe.disconnectFromServer();
            return false;
        }
    }
    QLocalServer::removeServer(m_path); // a stale socket from a crashed instance
    m_server = new QLocalServer(this);
    m_server->setSocketOptions(QLocalServer::UserAccessOption);
    if (!m_server->listen(m_path)) {
        qWarning("taos-settings: single-instance socket unavailable: %s", qUtf8Printable(m_server->errorString()));
        return true; // run anyway, just not single
    }
    connect(m_server, &QLocalServer::newConnection, this, [this] {
        while (QLocalSocket *s = m_server->nextPendingConnection()) {
            connect(s, &QLocalSocket::disconnected, s, &QObject::deleteLater);
            connect(s, &QLocalSocket::readyRead, s, [this, s] {
                while (s->canReadLine()) {
                    QStringList a;
                    for (const QJsonValue &v : QJsonDocument::fromJson(s->readLine()).array())
                        a << v.toString();
                    emit activated(a);
                }
            });
        }
    });
    return true;
}

} // namespace TaosSettings
