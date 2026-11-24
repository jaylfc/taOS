#include "taosqt/client.h"

#include <QJsonArray>
#include <QJsonDocument>
#include <QMetaObject>
#include <QThread>

#include "taos_client.h"
#include "taosqt/eventstream.h"

using namespace Qt::StringLiterals;

namespace Taos {

namespace {
struct CbCtx {
    Client *client;
    quint64 generation;
};

QJsonValue parseValue(const QByteArray &json)
{
    // QJsonDocument only accepts objects and arrays at the top level, but the
    // transport also sends scalars (null, numbers). Wrap and unwrap.
    const QJsonDocument doc = QJsonDocument::fromJson("[" + json + "]");
    return doc.array().isEmpty() ? QJsonValue() : doc.array().first();
}

QByteArray toJson(const QJsonObject &o)
{
    return QJsonDocument(o).toJson(QJsonDocument::Compact);
}
} // namespace

struct Client::Private
{
    TaosClient *handle = nullptr;
    CbCtx *ctx = nullptr;
    quint64 generation = 0;
    quint64 nextId = 1;
    QHash<quint64, Reply *> replies;
    QHash<quint64, EventStream *> streams; // by transport id, while open
    QList<EventStream *> allStreams;       // every stream that exists

    // Runs on a transport thread: copy, then hop to the Client's thread.
    static void onNative(void *user, uint64_t id, int kind, const char *json, size_t)
    {
        const auto *ctx = static_cast<const CbCtx *>(user);
        Client *c = ctx->client;
        const quint64 gen = ctx->generation;
        const QByteArray data(json);
        QMetaObject::invokeMethod(
            c, [c, gen, id, kind, data] { c->dispatch(gen, id, kind, data); }, Qt::QueuedConnection);
    }

    void freeHandle()
    {
        if (handle) {
            // Blocks until no callback is running; none runs afterwards.
            taos_client_free(handle);
            handle = nullptr;
        }
        delete ctx;
        ctx = nullptr;
    }
};

// ---- Reply ---------------------------------------------------------------

Reply::Reply(Client *client, quint64 id, QString op)
    : QObject(client), m_client(client), m_id(id), m_op(std::move(op))
{
}

Reply::~Reply()
{
    if (!m_finished && m_client)
        m_client->cancelId(m_id);
}

void Reply::abort()
{
    if (m_finished)
        return;
    if (m_client)
        m_client->cancelId(m_id);
    finish(Result{QJsonValue(), Error(Error::Cancelled, QString())});
}

void Reply::abortSilently()
{
    if (m_finished)
        return;
    if (m_client)
        m_client->cancelId(m_id);
    m_finished = true;
    deleteLater();
}

void Reply::finish(const Result &r)
{
    if (m_finished)
        return;
    m_finished = true;
    m_result = r;
    emit finished(r);
    deleteLater();
}

// ---- Client --------------------------------------------------------------

Client::Client(const ClientOptions &options, QObject *parent)
    : QObject(parent), m_options(options), d(new Private)
{
    qRegisterMetaType<Taos::Result>();
    qRegisterMetaType<Taos::Error>();
    qRegisterMetaType<Taos::Client::ConnectionState>();
    qRegisterMetaType<Taos::EventStream::State>();
    QString err;
    if (!createHandle(&err))
        qWarning("taosqt: cannot create the controller client: %s", qUtf8Printable(err));
}

Client::~Client()
{
    d->freeHandle(); // first: after this no callback can reach us
    // Detach what we own so their destructors do not call back into us.
    d->replies.clear();
    d->streams.clear();
}

bool Client::createHandle(QString *errorText)
{
    const QByteArray url = m_options.baseUrl.toString().toUtf8();
    const QByteArray bearer = m_options.bearerToken.toUtf8();
    TaosConfig cfg{};
    cfg.base_url = url.constData();
    cfg.session_json = m_options.savedSession.isEmpty() ? nullptr : m_options.savedSession.constData();
    cfg.bearer_token = bearer.isEmpty() ? nullptr : bearer.constData();
    cfg.request_timeout_ms = quint32(m_options.requestTimeoutMs);
    cfg.connect_timeout_ms = quint32(m_options.connectTimeoutMs);
    cfg.sse_backoff_base_ms = quint32(m_options.streamBackoffBaseMs);
    cfg.sse_backoff_cap_ms = quint32(m_options.streamBackoffCapMs);
    cfg.sse_idle_timeout_ms = quint32(m_options.streamIdleTimeoutMs);

    d->ctx = new CbCtx{this, ++d->generation};
    char *err = nullptr;
    d->handle = taos_client_new(&cfg, &Private::onNative, d->ctx, &err);
    if (!d->handle) {
        if (errorText && err)
            *errorText = Error::fromJson(QJsonDocument::fromJson(err).object()).message();
        if (err)
            taos_string_free(err);
        delete d->ctx;
        d->ctx = nullptr;
        return false;
    }
    return true;
}

bool Client::hasCredentials() const
{
    return d->handle && taos_has_credentials(d->handle) != 0;
}

Reply *Client::call(const QString &op, const QJsonObject &args)
{
    Q_ASSERT(QThread::currentThread() == thread());
    const quint64 id = d->nextId++;
    auto *r = new Reply(this, id, op);
    d->replies.insert(id, r);
    const QByteArray o = op.toUtf8();
    const QByteArray a = toJson(args);
    const int rc = d->handle ? taos_call(d->handle, id, o.constData(), a.constData()) : TAOS_ERR_CLOSED;
    if (rc != TAOS_OK) {
        d->replies.remove(id);
        QMetaObject::invokeMethod(
            r,
            [r, rc] { r->finish(Result{QJsonValue(), Error(Error::Internal, tr("The request could not be started (%1).").arg(rc))}); },
            Qt::QueuedConnection);
    }
    return r;
}

Reply *Client::http(const QString &method, const QString &path, const QJsonObject &query, const QJsonValue &body)
{
    QJsonObject args{{u"method"_s, method}, {u"path"_s, path}};
    if (!query.isEmpty())
        args.insert(u"query"_s, query);
    if (!body.isNull())
        args.insert(u"body"_s, body);
    return call(u"http"_s, args);
}

void Client::cancelId(quint64 id)
{
    d->replies.remove(id);
    d->streams.remove(id);
    if (d->handle)
        taos_cancel(d->handle, id);
}

void Client::registerStream(EventStream *s)
{
    d->allStreams.append(s);
}

void Client::unregisterStream(EventStream *s)
{
    d->allStreams.removeAll(s);
    for (auto it = d->streams.begin(); it != d->streams.end();) {
        if (it.value() == s) {
            if (d->handle)
                taos_cancel(d->handle, it.key());
            it = d->streams.erase(it);
        } else {
            ++it;
        }
    }
}

quint64 Client::startStream(EventStream *s, const QString &path, const QJsonObject &query)
{
    if (!d->handle)
        return 0;
    const quint64 id = d->nextId++;
    const QByteArray p = path.toUtf8();
    const QByteArray q = toJson(query);
    d->streams.insert(id, s);
    if (taos_stream_open(d->handle, id, p.constData(), query.isEmpty() ? nullptr : q.constData()) != TAOS_OK) {
        d->streams.remove(id);
        return 0;
    }
    return id;
}

EventStream *Client::openStream(const QString &path, const QJsonObject &query, QObject *parent)
{
    return new EventStream(this, path, query, parent ? parent : this);
}

EventStream *Client::osEvents(const QStringList &kinds, QObject *parent)
{
    QJsonObject q;
    if (!kinds.isEmpty())
        q.insert(u"kinds"_s, kinds.join(u',')); // the route takes a comma list
    return openStream(u"/api/os/events"_s, q, parent);
}

EventStream *Client::sharedOsEvents()
{
    if (!m_osShared)
        m_osShared = osEvents({}, this);
    return m_osShared;
}

void Client::retainOsEvents()
{
    if (m_osRetain++ == 0)
        sharedOsEvents()->start();
}

void Client::releaseOsEvents()
{
    if (m_osRetain > 0 && --m_osRetain == 0 && m_osShared)
        m_osShared->stop();
}

static QPointer<Client> g_defaultClient;
Client *defaultClient() { return g_defaultClient; }
void setDefaultClient(Client *c) { g_defaultClient = c; }

void Client::dispatch(quint64 generation, quint64 id, int kind, const QByteArray &json)
{
    if (generation != d->generation)
        return; // from a transport that has been replaced
    switch (kind) {
    case TAOS_EV_RESULT:
    case TAOS_EV_FAILED: {
        Reply *r = d->replies.take(id);
        if (!r)
            return; // cancelled or deleted meanwhile
        Result res;
        if (kind == TAOS_EV_RESULT) {
            res.value = parseValue(json);
            noteResult(Error(), r->m_op);
        } else {
            res.error = Error::fromJson(QJsonDocument::fromJson(json).object());
            noteResult(res.error, r->m_op);
        }
        const bool signIn = r->m_op == u"auth.login"_s || r->m_op == u"auth.pin_login"_s;
        const bool signOut = r->m_op == u"auth.logout"_s;
        r->finish(res);
        if (res.ok() && (signIn || signOut))
            onCredentialsChanged();
        break;
    }
    default: {
        EventStream *s = d->streams.value(id);
        if (!s)
            return;
        if (kind == TAOS_EV_STREAM_ENDED)
            d->streams.remove(id);
        if (kind == TAOS_EV_STREAM_EVENT || kind == TAOS_EV_STREAM_CONNECTED)
            setConnectionState(Online);
        else if (kind == TAOS_EV_STREAM_RETRYING)
            setConnectionState(Reconnecting);
        s->handle(kind, json);
        break;
    }
    }
}

void Client::noteResult(const Error &e, const QString &op)
{
    if (!e.isError()) {
        setConnectionState(Online);
        return;
    }
    switch (e.kind()) {
    case Error::Network:
        setConnectionState(Reconnecting);
        break;
    case Error::Cancelled:
    case Error::Internal:
        break;
    case Error::Unauthenticated:
        setConnectionState(Online);
        if (op != u"auth.login"_s && op != u"auth.pin_login"_s && !m_unauthNotified) {
            m_unauthNotified = true;
            emit unauthenticated(e);
        }
        break;
    default:
        setConnectionState(Online); // the controller answered
        break;
    }
}

void Client::setConnectionState(ConnectionState s)
{
    if (m_state == s)
        return;
    m_state = s;
    emit connectionStateChanged(s);
}

void Client::onCredentialsChanged()
{
    m_unauthNotified = false;
    emit credentialsChanged();
    for (EventStream *s : std::as_const(d->allStreams))
        s->restartAfterCredentials();
}

QByteArray Client::exportSession() const
{
    if (!d->handle)
        return {};
    char *s = taos_session_export(d->handle);
    if (!s)
        return {};
    const QByteArray out(s);
    taos_string_free(s);
    return out;
}

void Client::setBearerToken(const QString &token)
{
    m_options.bearerToken = token;
    if (d->handle) {
        const QByteArray t = token.toUtf8();
        taos_set_bearer(d->handle, token.isEmpty() ? nullptr : t.constData());
    }
    onCredentialsChanged();
}

void Client::forgetCredentials()
{
    m_options.bearerToken.clear();
    m_options.savedSession.clear();
    if (d->handle) {
        taos_session_forget(d->handle);
        taos_set_bearer(d->handle, nullptr);
    }
    onCredentialsChanged();
}

bool Client::restoreSession(const QByteArray &savedSession, QString *errorText)
{
    // The transport fixes the User-Agent a session was created with when it is
    // built, so a restore builds a new transport. Pending requests are
    // cancelled; streams are re-opened on the new one.
    const ClientOptions previous = m_options;
    m_options.savedSession = savedSession;
    m_options.bearerToken.clear();

    const QList<EventStream *> streams = d->allStreams;
    QList<Reply *> pending = d->replies.values();
    d->replies.clear();
    d->streams.clear();
    d->freeHandle();
    if (!createHandle(errorText)) {
        m_options = previous;
        QString ignored;
        createHandle(&ignored); // keep a working transport
        for (EventStream *s : streams)
            s->restartAfterCredentials();
        for (Reply *r : std::as_const(pending))
            r->finish(Result{QJsonValue(), Error(Error::Cancelled, QString())});
        return false;
    }
    for (Reply *r : std::as_const(pending))
        r->finish(Result{QJsonValue(), Error(Error::Cancelled, QString())});
    m_unauthNotified = false;
    emit credentialsChanged();
    for (EventStream *s : streams)
        s->reopen();
    return true;
}

} // namespace Taos
