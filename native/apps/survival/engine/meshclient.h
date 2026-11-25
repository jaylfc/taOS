// SPDX-License-Identifier: AGPL-3.0-or-later
#pragma once
// The app's end of the taos-mesh socket: newline-delimited JSON over a unix socket (see native/mesh/docs/API.md).
// Everything is asynchronous: a request returns at once and its reply comes to a callback on the caller's thread.
// Two connections: one for requests and one for the engine's record stream (`subscribe` takes its connection over).
// When the socket file is missing or the engine goes away the client says so and tries again with a back-off;
// it never starts or restarts the engine and never sends anything but the requests it is asked to send.
#include <QHash>
#include <QJsonObject>
#include <QJsonValue>
#include <QLocalSocket>
#include <QObject>
#include <QPointer>
#include <QTimer>
#include <functional>

namespace Taos::Survival {

struct MeshReply
{
    bool ok = false;
    QJsonValue result;
    QString code;       // the engine's error code, or "transport" when the engine was not reached
    QString message;    // plain words
};

class MeshClient : public QObject
{
    Q_OBJECT
public:
    enum class State { Absent, Connecting, Connected };
    Q_ENUM(State)
    explicit MeshClient(QString socketPath, QObject *parent = nullptr);
    ~MeshClient() override;
    /// $TAOS_MESH_SOCKET (a seam for tests and pictures), else $XDG_RUNTIME_DIR/taos-mesh.sock; empty without a runtime directory.
    static QString defaultSocketPath();
    QString socketPath() const { return m_path; }
    State state() const { return m_state; }
    /// Starts looking for the engine and keeps looking until stop().
    void start();
    void stop();
    using Callback = std::function<void(const MeshReply &)>;
    /// Sends one request as the owner. `ctx` limits the callback's life: it is dropped if `ctx` is destroyed.
    /// With no connection the callback gets a transport error at once (never queued: a send must not wait for a
    /// reconnect and go out later).
    void request(const QString &method, const QJsonObject &params, QObject *ctx, Callback cb);
    int pending() const { return int(m_pending.size()); }

Q_SIGNALS:
    void stateChanged();
    /// A record was stored by the engine (from the stream).
    void recordStored(const QJsonObject &record);

private:
    struct Pending { QPointer<QObject> ctx; Callback cb; QTimer *timeout = nullptr; };
    void tryConnect();
    void scheduleRetry();
    void onReadyRead();
    void onCommandDown();
    void failAll(const QString &why);
    void openStream();
    void onStreamRead();
    void setState(State s);
    QString m_path;
    State m_state = State::Absent;
    bool m_running = false;
    QLocalSocket m_cmd, m_stream;
    QByteArray m_cmdBuf, m_streamBuf;
    QHash<qint64, Pending> m_pending;
    qint64 m_nextId = 1;
    QTimer m_retry;
    int m_backoffMs = 1000;
};

} // namespace Taos::Survival
