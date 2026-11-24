#include "dockipc.h"

#include <QDir>
#include <QFile>
#include <QLocalSocket>
#include <sys/stat.h>
#include <unistd.h>

QString DockIpc::socketPath() {
    const QString run = qEnvironmentVariable("XDG_RUNTIME_DIR");
    const QString dir = run.isEmpty() ? QStringLiteral("/tmp/taos-shell-%1").arg(getuid()) : run + QStringLiteral("/taos-shell");
    return dir + QStringLiteral("/dock.sock");
}

bool DockIpc::isCommand(const QString &w) {
    return w == QLatin1String("reveal") || w == QLatin1String("hide") || w == QLatin1String("toggle") || w == QLatin1String("focus");
}

DockIpc::DockIpc(QObject *parent) : QObject(parent) {
    connect(&m_server, &QLocalServer::newConnection, this, [this] {
        while (QLocalSocket *s = m_server.nextPendingConnection()) {
            connect(s, &QLocalSocket::readyRead, this, [this, s] {
                const QString w = QString::fromUtf8(s->readAll()).trimmed();
                if (isCommand(w)) emit command(w);
                s->write("ok\n");
                s->disconnectFromServer();
            });
            connect(s, &QLocalSocket::disconnected, s, &QObject::deleteLater);
        }
    });
}

bool DockIpc::listen(const QString &p) {
    const QString path = p.isEmpty() ? socketPath() : p;
    QDir().mkpath(QFileInfo(path).absolutePath());
    chmod(QFileInfo(path).absolutePath().toLocal8Bit().constData(), 0700);
    QLocalServer::removeServer(path);
    m_server.setSocketOptions(QLocalServer::UserAccessOption);
    return m_server.listen(path);
}

bool DockIpc::send(const QString &cmd, QString *error, const QString &p) {
    QLocalSocket s;
    s.connectToServer(p.isEmpty() ? socketPath() : p);
    if (!s.waitForConnected(1000)) { if (error) *error = QStringLiteral("no taos-dock is running"); return false; }
    s.write(cmd.toUtf8());
    s.flush();
    s.waitForReadyRead(1000);
    return true;
}
