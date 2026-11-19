#pragma once
#include <QJsonObject>
#include <QJsonValue>
#include <QObject>
#include <QPointer>
#include <QStringList>
#include <QUrl>
#include <memory>
#include <type_traits>

#include "taosqt/error.h"
#include "taosqt/global.h"

namespace Taos {

class Client;
class EventStream;

/// How to reach the controller. All durations are in milliseconds; 0 means the
/// library default (30 s request, 10 s connect, stream backoff 500 ms doubling
/// to 30 s, stream idle timeout 60 s). Tests shrink them.
struct TAOSQT_EXPORT ClientOptions
{
    QUrl baseUrl = QUrl(QStringLiteral("http://127.0.0.1:6969"));
    /// A session snapshot saved earlier (Client::exportSession()), or empty.
    QByteArray savedSession;
    /// A bearer token (the host-local token), or empty.
    QString bearerToken;
    int requestTimeoutMs = 0;
    int connectTimeoutMs = 0;
    int streamBackoffBaseMs = 0;
    int streamBackoffCapMs = 0;
    int streamIdleTimeoutMs = 0;
};

/// One in-flight request. Created by Client::call() / Client::http(); you do not
/// delete it: it deletes itself after `finished` has been delivered. Destroying
/// it earlier (or calling abort()) cancels the request in the transport and
/// guarantees that nothing is delivered afterwards.
///
///     client->call(u"notifications.list"_s)->then(this, [this](const Taos::Result &r) {
///         if (!r.ok()) { showError(r.error.userMessage()); return; }
///         fill(r.value.toArray());
///     });
///
/// `then()` ties the callback to a context object: if the context is destroyed
/// first, the callback is not called (and the request is cancelled).
class TAOSQT_EXPORT Reply : public QObject
{
    Q_OBJECT
public:
    ~Reply() override;

    bool isFinished() const { return m_finished; }
    Result result() const { return m_result; }

    /// Run `fn(const Taos::Result &)` on the context's thread when finished.
    template<typename Fn>
    void then(QObject *context, Fn &&fn)
    {
        static_assert(std::is_invocable_v<Fn, const Result &>, "fn must accept (const Taos::Result &)");
        QObject::connect(this, &Reply::finished, context, std::forward<Fn>(fn));
        QObject::connect(context, &QObject::destroyed, this, &Reply::abortSilently);
    }

public Q_SLOTS:
    /// Cancel. `finished` is emitted once with an Error::Cancelled result.
    void abort();

Q_SIGNALS:
    void finished(const Taos::Result &result);

private:
    friend class Client;
    explicit Reply(Client *client, quint64 id, QString op);
    void finish(const Result &r);
    void abortSilently();

    QPointer<Client> m_client;
    quint64 m_id;
    QString m_op;
    bool m_finished = false;
    Result m_result;
};

/// The connection to one controller: requests, streams, credentials and the
/// online/offline state. Owns the Rust transport (cookies, CSRF, the constant
/// User-Agent, error mapping). One Client per app is normal; it must be used
/// from the thread it lives in (the GUI thread), and destroyed there.
///
/// Credentials are changed through the Session (which asks a TokenSource); the
/// low-level calls below are what a TokenSource implementation uses.
class TAOSQT_EXPORT Client : public QObject
{
    Q_OBJECT
    Q_PROPERTY(ConnectionState connectionState READ connectionState NOTIFY connectionStateChanged)
    Q_PROPERTY(bool hasCredentials READ hasCredentials NOTIFY credentialsChanged)
public:
    enum ConnectionState {
        Unknown,      ///< nothing has been tried yet
        Online,       ///< the last request or stream frame got through
        Reconnecting  ///< the controller cannot be reached; streams are retrying with backoff
    };
    Q_ENUM(ConnectionState)

    explicit Client(const ClientOptions &options = {}, QObject *parent = nullptr);
    /// Cancels everything in flight. No callback or signal is delivered afterwards.
    ~Client() override;

    ClientOptions options() const { return m_options; }
    ConnectionState connectionState() const { return m_state; }
    bool hasCredentials() const;

    /// Run a named operation (see taos_client.h for the list), for example
    /// `call("notifications.archive", {{"id", 12}})`. Typed domain wrappers are in
    /// api.h; this is the building block.
    Reply *call(const QString &op, const QJsonObject &args = {});

    /// A raw request for routes that have no operation yet. `query` values and
    /// `body` are JSON. Same cookie, CSRF and error handling as call().
    Reply *http(const QString &method, const QString &path, const QJsonObject &query = {},
                const QJsonValue &body = QJsonValue::Null);

    /// A reconnecting event stream on `path` (see EventStream). The stream is
    /// owned by `parent` (default: this) and is not started.
    EventStream *openStream(const QString &path, const QJsonObject &query = {}, QObject *parent = nullptr);
    /// `GET /api/os/events?kinds=...`: change notifications (no payload). An
    /// empty list subscribes to every kind. Prefer sharedOsEvents(): one
    /// connection per app is enough.
    EventStream *osEvents(const QStringList &kinds = {}, QObject *parent = nullptr);

    /// THE `/api/os/events` stream of this Client, shared by every LiveQuery:
    /// one connection however many lists an app shows. Reference counted: it
    /// connects on the first retainOsEvents() and closes on the last
    /// releaseOsEvents(). Consumers filter by `data.kind` themselves and refetch
    /// on every `connected()` (events sent while disconnected are not replayed).
    EventStream *sharedOsEvents();
    void retainOsEvents();
    void releaseOsEvents();

    // ---- credentials (used by Session and TokenSource implementations) ----
    /// Opaque, serialisable session for the keyring; empty when there is no
    /// cookie session. Contains the live token: treat it like a password.
    QByteArray exportSession() const;
    /// Use this bearer token from now on (empty removes it). Streams pick it up
    /// on their next reconnect; streams that ended with Unauthenticated restart.
    void setBearerToken(const QString &token);
    /// Replace the whole credential state with a saved session. Requests in
    /// flight finish with Cancelled; open streams are re-opened.
    bool restoreSession(const QByteArray &savedSession, QString *errorText = nullptr);
    /// Forget the cookie session and bearer token locally (no server call).
    void forgetCredentials();

Q_SIGNALS:
    /// The controller rejected our credentials (HTTP 401) on a call or stream.
    /// Emitted once per rejection, however many requests fail together; it
    /// fires again only after credentials changed. Failed sign-in attempts
    /// (`auth.login`) do not emit it: they report through their own Reply.
    void unauthenticated(const Taos::Error &error);
    void connectionStateChanged(Taos::Client::ConnectionState state);
    /// A sign-in, token change, restore or forget happened.
    void credentialsChanged();

private:
    friend class Reply;
    friend class EventStream;
    struct Private;

    void registerStream(EventStream *s);
    void unregisterStream(EventStream *s);
    quint64 startStream(EventStream *s, const QString &path, const QJsonObject &query);
    void cancelId(quint64 id);
    void dispatch(quint64 generation, quint64 id, int kind, const QByteArray &json);
    void noteResult(const Error &e, const QString &op);
    void setConnectionState(ConnectionState s);
    void onCredentialsChanged();
    bool createHandle(QString *errorText);

    ClientOptions m_options;
    ConnectionState m_state = Unknown;
    bool m_unauthNotified = false;
    int m_osRetain = 0;
    QPointer<EventStream> m_osShared;
    std::unique_ptr<Private> d;
};

/// The Client that QML-created objects (LiveQuery, Taos.call) use when they are
/// not given one. The generated app entry point sets it; plain C++ apps may too.
TAOSQT_EXPORT Client *defaultClient();
TAOSQT_EXPORT void setDefaultClient(Client *client);

} // namespace Taos

Q_DECLARE_METATYPE(Taos::Client::ConnectionState)
