#include "control.h"

#include <QDir>
#include <QFile>
#include <QFileInfo>
#include <QLocalSocket>
#include <poll.h>
#include <sys/socket.h>
#include <sys/stat.h>
#include <sys/un.h>
#include <unistd.h>

QString Control::socketPath() {
    QString dir = qEnvironmentVariable("XDG_RUNTIME_DIR");
    if (dir.isEmpty() || !QDir(dir).exists()) dir = QStringLiteral("/tmp/taos-shell-%1").arg(getuid());
    else dir += QStringLiteral("/taos-shell");
    return dir + QStringLiteral("/overview.sock");
}

int Control::send(const QString &path, const QStringList &words, QString *reply, int timeoutMs) {
    const QByteArray p = path.toLocal8Bit();
    sockaddr_un a {};
    a.sun_family = AF_UNIX;
    if (p.size() >= int(sizeof a.sun_path)) return 2;
    memcpy(a.sun_path, p.constData(), size_t(p.size()));
    const int fd = ::socket(AF_UNIX, SOCK_STREAM | SOCK_CLOEXEC, 0);
    if (fd < 0) return 2;
    if (::connect(fd, reinterpret_cast<sockaddr *>(&a), sizeof a) != 0) { ::close(fd); return 2; }
    const QByteArray line = words.join(QLatin1Char(' ')).toUtf8() + '\n';
    if (::write(fd, line.constData(), size_t(line.size())) != line.size()) { ::close(fd); return 2; }
    QByteArray ans;
    pollfd pfd { fd, POLLIN, 0 };
    while (!ans.endsWith('\n')) {
        if (::poll(&pfd, 1, timeoutMs) <= 0) break;
        char buf[256];
        const ssize_t n = ::read(fd, buf, sizeof buf);
        if (n <= 0) break;
        ans.append(buf, int(n));
    }
    ::close(fd);
    const QString r = QString::fromUtf8(ans).trimmed();
    if (reply) *reply = r;
    if (r == QLatin1String("ok") || r.startsWith(QLatin1String("ok "))) return 0;
    return r.isEmpty() ? 2 : 1;
}

bool ControlServer::listen(const QString &path, QString *error) {
    QDir().mkpath(QFileInfo(path).absolutePath());
    ::chmod(QFileInfo(path).absolutePath().toLocal8Bit().constData(), 0700);
    QString r;
    if (Control::send(path, {QStringLiteral("ping")}, &r, 300) == 0) {
        if (error) *error = QStringLiteral("another taos-overview is already running");
        return false;
    }
    QLocalServer::removeServer(path);   // stale socket of a dead daemon
    m_server.setSocketOptions(QLocalServer::UserAccessOption);
    if (!m_server.listen(path)) {
        if (error) *error = m_server.errorString();
        return false;
    }
    ::chmod(path.toLocal8Bit().constData(), 0600);
    connect(&m_server, &QLocalServer::newConnection, this, [this] {
        while (QLocalSocket *s = m_server.nextPendingConnection()) {
            connect(s, &QLocalSocket::disconnected, s, &QObject::deleteLater);
            connect(s, &QLocalSocket::readyRead, s, [this, s] {
                if (!s->canReadLine()) return;
                const QString line = QString::fromUtf8(s->readLine()).trimmed();
                const QString ans = m_handler ? m_handler(line.split(QLatin1Char(' '), Qt::SkipEmptyParts))
                                              : QStringLiteral("error: no handler");
                s->write(ans.toUtf8() + '\n');
                s->flush();
                s->disconnectFromServer();
            });
        }
    });
    return true;
}
