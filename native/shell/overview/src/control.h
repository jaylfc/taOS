// SPDX-License-Identifier: AGPL-3.0-or-later
#pragma once
#include <QLocalServer>
#include <functional>
#include <QObject>
#include <QString>
#include <QStringList>

// The control socket: $XDG_RUNTIME_DIR/taos-shell/overview.sock, mode 600. One request per
// connection: a line of words, answered by one line ("ok" or "error: ...").
namespace Control {
QString socketPath();
// Plain POSIX client, usable before any Qt object exists. Returns 0 on "ok", 1 when the daemon
// answered with an error, 2 when it could not be reached. `reply` gets the answer line.
int send(const QString &path, const QStringList &words, QString *reply, int timeoutMs = 2000);
}

class ControlServer : public QObject {
    Q_OBJECT
public:
    explicit ControlServer(QObject *parent = nullptr) : QObject(parent) {}
    // Fails (with `error`) when another live daemon already owns the socket.
    bool listen(const QString &path, QString *error);
    // The handler returns the reply line (without newline).
    void setHandler(std::function<QString(const QStringList &)> h) { m_handler = std::move(h); }
private:
    QLocalServer m_server;
    std::function<QString(const QStringList &)> m_handler;
};
