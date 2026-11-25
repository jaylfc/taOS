// SPDX-License-Identifier: AGPL-3.0-or-later
#pragma once
#include <QJsonObject>
#include <QObject>

#include "taosqt/client.h"
#include "taosqt/eventstream.h"

namespace Taos {

class Session;

/// Where a Session gets its credentials. REPLACEABLE: the Session does not
/// care how a credential is obtained, so the sign-in mechanism can change
/// without touching any app.
///
/// Today: a username and password typed by the user (PasswordTokenSource), the
/// host-local token where the controller permits it (LocalTokenSource), or a
/// session saved in the keyring (SavedSessionTokenSource). Later: the
/// per-person session from the accounts design (files-and-accounts.md section 4;
/// app-model.md section 3: an app holds a scoped app token, never the user's
/// session): write a TokenSource whose acquire() asks the account service and
/// calls Session::useBearerToken() with what it returns.
///
/// Contract: acquire() is called on the GUI thread when the Session needs
/// credentials, at start() and each time the controller rejected the current
/// ones. It must either call one of the Session::use...() methods (now or
/// later, asynchronously) or emit inputRequired() to ask the user. It must not
/// offer a credential that was already rejected again (the Session gives up
/// after a few rounds so a bad source cannot loop forever).
class TAOSQT_EXPORT TokenSource : public QObject
{
    Q_OBJECT
public:
    using QObject::QObject;
    virtual QString name() const = 0;
    virtual void acquire(Session *session, const Error &rejected) = 0;
    /// Called after a successful password sign-in with the new session snapshot
    /// (contains the token) so the source can store it (keyring). Default: nothing.
    virtual void remember(const QByteArray &sessionJson) { Q_UNUSED(sessionJson); }
    /// Called on sign-out: drop anything stored.
    virtual void forget() {}

Q_SIGNALS:
    /// The source cannot proceed without the user (show a sign-in form).
    void inputRequired(const QString &reason);
};

/// Asks the user: acquire() just raises inputRequired(). The app answers by
/// calling Session::signIn(). Stores nothing.
class TAOSQT_EXPORT PasswordTokenSource : public TokenSource
{
    Q_OBJECT
public:
    using TokenSource::TokenSource;
    QString name() const override { return QStringLiteral("password"); }
    void acquire(Session *session, const Error &rejected) override;
};

/// The host-local admin token (`Authorization: Bearer ...`), accepted by the
/// controller only for processes on the same host. Reads the token from a
/// file (or takes it directly); falls back to asking the user when the token
/// is refused or unreadable.
class TAOSQT_EXPORT LocalTokenSource : public TokenSource
{
    Q_OBJECT
public:
    explicit LocalTokenSource(const QString &token, QObject *parent = nullptr);
    /// Reads the first line of `path` when acquire() runs.
    static LocalTokenSource *fromFile(const QString &path, QObject *parent = nullptr);
    QString name() const override { return QStringLiteral("local-token"); }
    void acquire(Session *session, const Error &rejected) override;

private:
    QString m_token;
    QString m_path;
    bool m_tried = false;
};

/// A session snapshot read from the keyring by the app. Used once; if the
/// controller rejects it, asks the user. remember()/forget() are forwarded to
/// optional callbacks so the app can write the keyring.
class TAOSQT_EXPORT SavedSessionTokenSource : public TokenSource
{
    Q_OBJECT
public:
    SavedSessionTokenSource(QByteArray saved, QObject *parent = nullptr);
    QString name() const override { return QStringLiteral("saved-session"); }
    void acquire(Session *session, const Error &rejected) override;
    void remember(const QByteArray &sessionJson) override { Q_EMIT sessionToStore(sessionJson); }
    void forget() override { Q_EMIT sessionForgotten(); }

Q_SIGNALS:
    void sessionToStore(const QByteArray &sessionJson);
    void sessionForgotten();

private:
    QByteArray m_saved;
    bool m_tried = false;
};

/// Who is signed in, and the one place an app learns it must ask the user to
/// sign in. Owns the Client.
///
///     Taos::Session session(options);          // PasswordTokenSource by default
///     connect(&session, &Taos::Session::signInRequired, this, showSignIn);
///     session.start();
///     ...
///     session.signIn(user, password);          // from the form
///
/// After any 401 anywhere (a request or a live stream) the Session goes to
/// SignedOut, asks its TokenSource for new credentials and, if that needs the
/// user, emits signInRequired once. Streams that stopped for lack of a session
/// restart by themselves after sign-in.
class TAOSQT_EXPORT Session : public QObject
{
    Q_OBJECT
    Q_PROPERTY(State state READ state NOTIFY stateChanged)
    Q_PROPERTY(QString userName READ userName NOTIFY userChanged)
    Q_PROPERTY(QJsonObject user READ user NOTIFY userChanged)
    Q_PROPERTY(QString errorText READ errorText NOTIFY errorTextChanged)
    /// The last attempt failed because the controller could not be reached (as
    /// opposed to refusing the credentials). Apps show "cannot reach the
    /// controller" instead of a sign-in prompt, because signing in again
    /// would not help.
    Q_PROPERTY(bool unreachable READ unreachable NOTIFY errorTextChanged)
    Q_PROPERTY(Taos::Client *client READ client CONSTANT)
public:
    enum State { SignedOut, SigningIn, SignedIn };
    Q_ENUM(State)

    explicit Session(const ClientOptions &options = {}, QObject *parent = nullptr);
    ~Session() override;

    Client *client() const { return m_client; }
    State state() const { return m_state; }
    QJsonObject user() const { return m_user; }
    QString userName() const { return m_user.value(QStringLiteral("username")).toString(); }
    QString errorText() const { return m_errorText; }
    bool unreachable() const { return m_unreachable; }

    /// Replace the credential source. The Session takes ownership. Takes effect
    /// at the next acquire(); call start() to use it now.
    void setTokenSource(TokenSource *source);
    TokenSource *tokenSource() const { return m_source; }

    /// Ask the token source for credentials and verify them.
    Q_INVOKABLE void start();
    /// Sign in with a password (the sign-in form). `remember` asks the
    /// controller for a long-lived session and lets the source store it.
    Q_INVOKABLE void signIn(const QString &username, const QString &password, bool remember = true);
    Q_INVOKABLE void signOut();

    // ---- for TokenSource implementations ----
    void useBearerToken(const QString &token);
    void useSavedSession(const QByteArray &savedSession);

Q_SIGNALS:
    void stateChanged(Taos::Session::State state);
    void userChanged();
    void errorTextChanged();
    void signedIn(const QJsonObject &user);
    void signedOut();
    /// The app must show its sign-in prompt. Emitted once per need; `reason` is
    /// a sentence for the prompt (empty on a first start).
    void signInRequired(const QString &reason);
    /// A sign-in or credential check failed (wrong password, controller
    /// unreachable, ...). `error.userMessage()` is the text to show.
    void signInFailed(const Taos::Error &error);

private:
    void setState(State s);
    void setError(const Error &e);
    void verify();
    void acquire(const Error &rejected);
    void onUnauthenticated(const Error &e);

    Client *m_client;
    TokenSource *m_source = nullptr;
    State m_state = SignedOut;
    QJsonObject m_user;
    QString m_errorText;
    bool m_unreachable = false;
    int m_acquireBudget = 3;
};

} // namespace Taos

Q_DECLARE_METATYPE(Taos::Session::State)
