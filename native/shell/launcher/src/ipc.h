// SPDX-License-Identifier: AGPL-3.0-or-later
#pragma once
#include <QLocalServer>
#include <QObject>
#include <QString>
#include <QStringList>
#include <functional>

namespace taos::launcher {

// One line in, one line out over a unix socket in $XDG_RUNTIME_DIR/taos/. The reply starts with
// "ok" or "error:". The daemon is single instance: listen() refuses while another one answers ping.
QString socketPath();
// 0 ok, 1 the daemon answered with an error, 2 nobody answered.
int sendCommand(const QString &path, const QStringList &words, QString *reply, int timeoutMs = 1000);

class Server : public QObject {
    Q_OBJECT
public:
    using Handler = std::function<QString(const QStringList &)>;
    void setHandler(Handler h) { m_handler = std::move(h); }
    bool listen(const QString &path, QString *error);
private:
    QLocalServer m_server;
    Handler m_handler;
};

}  // namespace taos::launcher
