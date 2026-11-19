#pragma once
#include <QJsonValue>
#include <QObject>

#include "taosqt/client.h"

namespace Taos {

/// A live server-sent-event stream with automatic reconnect.
///
/// Reconnect and backoff happen in the transport (500 ms doubling to 30 s with
/// jitter by default); this object only reports what it is doing:
///
///     Idle -> Connecting -> Live <-> Reconnecting
///                      \-> AuthRequired   (HTTP 401: restarts by itself after the
///                                          Client's credentials change)
///                      \-> Ended          (a fatal error that signing in will not fix)
///
/// "Live" means a frame (including the controller's 10 s heartbeat) arrived; a
/// stream that connected but has been quiet for less than the heartbeat period
/// still shows Connecting.
///
/// Destroying the object closes the connection (verified by tests against the
/// fake controller's open-connection count) and nothing is delivered after the
/// destructor returns.
class TAOSQT_EXPORT EventStream : public QObject
{
    Q_OBJECT
    Q_PROPERTY(State state READ state NOTIFY stateChanged)
    Q_PROPERTY(QString path READ path CONSTANT)
public:
    enum State { Idle, Connecting, Live, Reconnecting, AuthRequired, Ended };
    Q_ENUM(State)

    ~EventStream() override;

    State state() const { return m_state; }
    QString path() const { return m_path; }
    /// Last error that moved the stream out of Live (Network while reconnecting,
    /// Unauthenticated, ...), for display next to the "reconnecting" state.
    Error lastError() const { return m_lastError; }

    /// Controller heartbeats (`events.heartbeat`) keep the stream Live but are
    /// not delivered through eventReceived() unless this is turned off.
    void setDeliverHeartbeats(bool on) { m_deliverHeartbeats = on; }

public Q_SLOTS:
    void start();
    void stop();

Q_SIGNALS:
    /// One frame. `data` is the parsed JSON of the frame's data lines, or the
    /// raw string when it was not JSON. For `/api/os/events` it is
    /// `{"kind": "notification.added", "id", "ts"}`: refetch what the kind names.
    void eventReceived(const QString &event, const QString &id, const QJsonValue &data);
    void stateChanged(Taos::EventStream::State state);
    void errorOccurred(const Taos::Error &error);

private:
    friend class Client;
    EventStream(Client *client, QString path, QJsonObject query, QObject *parent);
    void handle(int kind, const QByteArray &json);
    void setState(State s);
    void restartAfterCredentials();
    void reopen();

    QPointer<Client> m_client;
    QString m_path;
    QJsonObject m_query;
    State m_state = Idle;
    quint64 m_id = 0;
    bool m_deliverHeartbeats = false;
    Error m_lastError;
};

} // namespace Taos

Q_DECLARE_METATYPE(Taos::EventStream::State)
