// SPDX-License-Identifier: AGPL-3.0-or-later
#pragma once
#include <QByteArray>
#include <QLocalSocket>
#include <QObject>
#include <functional>
#include <queue>

// The i3/sway IPC wire format: "i3-ipc", payload length (u32), message type (u32), payload.
namespace SwayWire {
enum Type : quint32 {
    RunCommand = 0, GetWorkspaces = 1, Subscribe = 2, GetOutputs = 3, GetTree = 4, GetVersion = 7,
    EventWorkspace = 0x80000000u, EventOutput = 0x80000001u, EventMode = 0x80000002u,
    EventWindow = 0x80000003u, EventBarconfig = 0x80000004u, EventBinding = 0x80000005u,
    EventShutdown = 0x80000006u, EventTick = 0x80000007u, EventInput = 0x80000015u,
};
QByteArray encode(quint32 type, const QByteArray &payload);
struct Message { quint32 type = 0; QByteArray payload; };
// Incremental decoder: feed bytes as they arrive, take whole messages out.
class Parser {
public:
    void feed(const QByteArray &data);
    bool next(Message *m);
    bool broken() const { return m_broken; }
private:
    QByteArray m_buf;
    bool m_broken = false;
};
}

// A connection to sway. One instance is used for requests (replies come back in order) and a second
// one for events, because event messages are interleaved with replies on a subscribed socket.
class SwayIpc : public QObject {
    Q_OBJECT
public:
    explicit SwayIpc(QObject *parent = nullptr);
    static QString socketPath();   // $SWAYSOCK, else I3SOCK, else empty
    bool connectToSway(const QString &path = QString());
    bool isConnected() const;
    // Queues a request; `cb` runs with the reply payload (empty and ok=false when the socket died).
    void request(quint32 type, const QByteArray &payload, std::function<void(bool ok, const QByteArray &)> cb = {});
    void command(const QString &cmd, std::function<void(bool ok, const QByteArray &)> cb = {}) {
        request(SwayWire::RunCommand, cmd.toUtf8(), std::move(cb));
    }
    void subscribe(const QStringList &events);   // makes this connection an event stream
signals:
    void event(quint32 type, const QByteArray &payload);
    void disconnected();
private:
    void onReadyRead();
    QLocalSocket m_sock;
    SwayWire::Parser m_parser;
    std::queue<std::function<void(bool, const QByteArray &)>> m_pending;
};
