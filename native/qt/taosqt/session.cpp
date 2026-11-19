#include "taosqt/session.h"

#include <QFile>

using namespace Qt::StringLiterals;

namespace Taos {

// ---- token sources --------------------------------------------------------

void PasswordTokenSource::acquire(Session *, const Error &rejected)
{
    Q_EMIT inputRequired(rejected.isError() ? rejected.userMessage() : QString());
}

LocalTokenSource::LocalTokenSource(const QString &token, QObject *parent)
    : TokenSource(parent), m_token(token.trimmed())
{
}

LocalTokenSource *LocalTokenSource::fromFile(const QString &path, QObject *parent)
{
    auto *s = new LocalTokenSource(QString(), parent);
    s->m_path = path;
    return s;
}

void LocalTokenSource::acquire(Session *session, const Error &rejected)
{
    if (!m_tried) {
        m_tried = true;
        if (m_token.isEmpty() && !m_path.isEmpty()) {
            QFile f(m_path);
            if (f.open(QIODevice::ReadOnly | QIODevice::Text))
                m_token = QString::fromUtf8(f.readLine()).trimmed();
        }
        if (!m_token.isEmpty()) {
            session->useBearerToken(m_token);
            return;
        }
        Q_EMIT inputRequired(tr("The host token could not be read. Please sign in."));
        return;
    }
    Q_EMIT inputRequired(rejected.isError() ? rejected.userMessage() : tr("Please sign in."));
}

SavedSessionTokenSource::SavedSessionTokenSource(QByteArray saved, QObject *parent)
    : TokenSource(parent), m_saved(std::move(saved))
{
}

void SavedSessionTokenSource::acquire(Session *session, const Error &rejected)
{
    if (!m_tried && !m_saved.isEmpty()) {
        m_tried = true;
        session->useSavedSession(m_saved);
        return;
    }
    Q_EMIT inputRequired(rejected.isError() ? rejected.userMessage() : QString());
}

// ---- Session ---------------------------------------------------------------

Session::Session(const ClientOptions &options, QObject *parent)
    : QObject(parent), m_client(new Client(options, this))
{
    qRegisterMetaType<Taos::Session::State>();
    setTokenSource(new PasswordTokenSource);
    connect(m_client, &Client::unauthenticated, this, &Session::onUnauthenticated);
}

Session::~Session() = default;

void Session::setTokenSource(TokenSource *source)
{
    if (m_source == source)
        return;
    delete m_source;
    m_source = source;
    if (!m_source)
        return;
    m_source->setParent(this);
    connect(m_source, &TokenSource::inputRequired, this, [this](const QString &reason) {
        setState(SignedOut);
        Q_EMIT signInRequired(reason);
    });
}

void Session::setState(State s)
{
    if (m_state == s)
        return;
    m_state = s;
    Q_EMIT stateChanged(s);
}

void Session::setError(const Error &e)
{
    const QString t = e.userMessage();
    if (t != m_errorText) {
        m_errorText = t;
        Q_EMIT errorTextChanged();
    }
}

void Session::start()
{
    m_acquireBudget = 3;
    setState(SigningIn);
    setError(Error());
    acquire(Error());
}

void Session::acquire(const Error &rejected)
{
    if (!m_source || m_acquireBudget-- <= 0) {
        setState(SignedOut);
        Q_EMIT signInRequired(rejected.userMessage());
        return;
    }
    m_source->acquire(this, rejected);
}

void Session::useBearerToken(const QString &token)
{
    setState(SigningIn);
    m_client->setBearerToken(token);
    verify();
}

void Session::useSavedSession(const QByteArray &savedSession)
{
    setState(SigningIn);
    QString err;
    if (!m_client->restoreSession(savedSession, &err)) {
        const Error e(Error::Internal, err);
        setError(e);
        acquire(e);
        return;
    }
    verify();
}

void Session::verify()
{
    // `/auth/me` reads only the session cookie (the controller ignores a bearer
    // token there), so a cookie session is checked with it and a bearer-only
    // credential with a cheap authenticated route that does accept the token.
    const bool cookie = !m_client->exportSession().isEmpty();
    Reply *r = cookie ? m_client->call(u"auth.me"_s)
                      : m_client->http(u"GET"_s, u"/api/notifications"_s, {{u"unread_only"_s, u"true"_s}});
    r->then(this, [this, cookie](const Result &res) {
        if (res.ok()) {
            m_user = cookie ? res.value.toObject() : QJsonObject{{u"via"_s, u"token"_s}};
            m_acquireBudget = 3;
            setError(Error());
            Q_EMIT userChanged();
            setState(SignedIn);
            Q_EMIT signedIn(m_user);
        } else if (res.error.kind() == Error::Unauthenticated) {
            m_client->forgetCredentials();
            acquire(res.error); // the Client's own coalesced signal is not used for this check
        } else {
            setError(res.error);
            setState(SignedOut);
            Q_EMIT signInFailed(res.error);
        }
    });
}

void Session::signIn(const QString &username, const QString &password, bool remember)
{
    setState(SigningIn);
    setError(Error());
    QJsonObject args{{u"password"_s, password}, {u"remember"_s, remember}};
    if (!username.isEmpty())
        args.insert(u"username"_s, username);
    m_client->call(u"auth.login"_s, args)->then(this, [this, remember](const Result &r) {
        if (!r.ok()) {
            setError(r.error);
            setState(SignedOut);
            Q_EMIT signInFailed(r.error);
            return;
        }
        m_user = r.value.toObject().value(u"user"_s).toObject();
        m_acquireBudget = 3;
        if (remember && m_source)
            m_source->remember(m_client->exportSession());
        Q_EMIT userChanged();
        setState(SignedIn);
        Q_EMIT signedIn(m_user);
    });
}

void Session::signOut()
{
    // Tell the controller first (it needs the cookie), then forget locally
    // whatever the answer was.
    m_client->call(u"auth.logout"_s)->then(this, [this](const Result &) { m_client->forgetCredentials(); });
    if (m_source)
        m_source->forget();
    m_user = {};
    Q_EMIT userChanged();
    setState(SignedOut);
    Q_EMIT signedOut();
}

void Session::onUnauthenticated(const Error &e)
{
    if (m_state != SignedIn)
        return; // a sign-in or check in progress reports for itself
    setState(SignedOut);
    m_acquireBudget = 3;
    acquire(e);
}

} // namespace Taos
