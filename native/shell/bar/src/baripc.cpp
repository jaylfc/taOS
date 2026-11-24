// SPDX-License-Identifier: AGPL-3.0-or-later
#include "baripc.h"

#include <QDir>
#include <QLocalSocket>

#include "audio.h"

QString BarIpc::defaultPath() {
    const QString env = qEnvironmentVariable("TAOS_BAR_SOCKET");
    if (!env.isEmpty()) return env;
    const QString rt = qEnvironmentVariable("XDG_RUNTIME_DIR", QDir::tempPath());
    return rt + QStringLiteral("/taos-bar.sock");
}

BarIpc::BarIpc(Audio *audio, QObject *parent) : QObject(parent), m_audio(audio) {
    m_server.setSocketOptions(QLocalServer::UserAccessOption);
    connect(&m_server, &QLocalServer::newConnection, this, [this] {
        while (QLocalSocket *s = m_server.nextPendingConnection()) {
            connect(s, &QLocalSocket::readyRead, s, [this, s] {
                while (s->canReadLine()) {
                    const QString line = QString::fromUtf8(s->readLine()).trimmed();
                    s->write((run(line) + QLatin1Char('\n')).toUtf8());
                    s->flush();
                }
            });
            connect(s, &QLocalSocket::disconnected, s, &QObject::deleteLater);
        }
    });
}

bool BarIpc::listen(const QString &path) {
    const QString p = path.isEmpty() ? defaultPath() : path;
    QLocalServer::removeServer(p);  // a stale socket of a bar that is gone
    return m_server.listen(p);
}

QString BarIpc::run(const QString &line) {
    const QStringList a = line.split(QLatin1Char(' '), Qt::SkipEmptyParts);
    QString cmd = a.value(0) == QLatin1String("volume") ? a.value(1) : a.value(0);
    if (cmd == QLatin1String("up")) m_audio->stepVolume(Audio::kStep);
    else if (cmd == QLatin1String("down")) m_audio->stepVolume(-Audio::kStep);
    else if (cmd == QLatin1String("mute")) m_audio->toggleMute();
    else if (cmd == QLatin1String("mic-mute")) m_audio->toggleMicMute();
    else return QStringLiteral("error: unknown command '%1' (use volume up|down|mute|mic-mute)").arg(line);
    return QStringLiteral("ok");
}

int BarIpc::send(const QStringList &args, const QString &path, QString *message) {
    QLocalSocket s;
    s.connectToServer(path.isEmpty() ? defaultPath() : path);
    if (!s.waitForConnected(1000)) {
        if (message) *message = QStringLiteral("taos-bar is not running");
        return 1;
    }
    s.write((args.join(QLatin1Char(' ')) + QLatin1Char('\n')).toUtf8());
    s.flush();
    if (!s.waitForReadyRead(2000)) {
        if (message) *message = QStringLiteral("taos-bar did not answer");
        return 1;
    }
    const QString reply = QString::fromUtf8(s.readLine()).trimmed();
    if (message) *message = reply;
    return reply == QLatin1String("ok") ? 0 : 1;
}
