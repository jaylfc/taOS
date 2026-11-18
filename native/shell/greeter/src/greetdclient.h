#pragma once
#include <QJsonObject>
#include <QLocalSocket>
#include <QObject>
#include <QStringList>
#include <QTimer>

// GreetdClient: the greetd IPC state machine (see greetd-ipc(7)).
//
// Frames are a 4 byte native-endian length followed by a UTF-8 JSON payload.
// One request is in flight at a time; every request gets exactly one reply.
//
//   login(user) -> create_session
//       auth_message(visible|secret) -> promptRequested(), wait for respond()
//       auth_message(info|error)     -> promptRequested() and acknowledged
//                                       automatically (no answer is needed)
//       error(auth_error|error)      -> cancel_session, then authFailed()
//       success                      -> start_session(cmd), then sessionStarting()
//
// Protocol violations (oversized or malformed frames, an unknown reply type, a
// reply nobody asked for), a closed socket and a reply timeout are all fatal().
class GreetdClient : public QObject {
    Q_OBJECT
public:
    static constexpr quint32 kMaxFrame = 1u << 20;  // 1 MiB: greetd replies are tiny

    enum class State { Disconnected, Idle, Creating, Answering, Prompting, Starting, Cancelling, Failed };
    Q_ENUM(State)

    explicit GreetdClient(QObject *parent = nullptr);

    void setSessionCommand(const QStringList &cmd) { m_cmd = cmd; }
    void setSessionEnv(const QStringList &env) { m_env = env; }
    // Time to wait for greetd to answer cancel_session or start_session. Waiting
    // for the user, and for PAM (which may block on hardware), is not timed.
    // Default 20 s.
    void setReplyTimeoutMs(int ms) { m_replyTimeout.setInterval(ms); }

    bool connectToGreetd(const QString &socketPath);
    State state() const { return m_state; }
    bool isConnected() const { return m_state != State::Disconnected; }
    QString username() const { return m_user; }

public slots:
    // Start (or restart: an open session is cancelled first) a login for user.
    void login(const QString &username);
    // Answer the prompt that promptRequested() announced.
    void respond(const QString &text);
    // Abandon the current session (back to Idle).
    void cancel();

signals:
    // kind: "visible" or "secret" (a question: call respond()), "info" or
    // "error" (a message: already acknowledged, nothing to answer).
    void promptRequested(const QString &kind, const QString &text);
    // Authentication failed (or the session could not start). greetd has been
    // sent cancel_session; login() may be called again straight away.
    // credentials is true for greetd's auth_error (wrong secret).
    void authFailed(const QString &message, bool credentials);
    void sessionStarting();
    void fatal(const QString &message);

private slots:
    void onReadyRead();
    void onDisconnected();
    void onReplyTimeout();

private:
    void sendRequest(const QJsonObject &o, State next);
    void handleReply(const QJsonObject &o);
    void failFatal(const QString &why);
    void startSessionNow();
    void sendCancelThenMaybeLogin();

    QLocalSocket m_sock;
    QTimer m_replyTimeout;
    QByteArray m_buf;
    State m_state = State::Disconnected;
    QStringList m_cmd;
    QStringList m_env;
    QString m_user;
    QString m_queuedUser;       // login() asked for while a cancel was in flight
    bool m_haveQueued = false;
    QString m_failMessage;      // reported once the cancel reply is in
    bool m_failCredentials = false;
    bool m_failPending = false;
    bool m_dead = false;
    bool m_finished = false;  // start_session succeeded
};
