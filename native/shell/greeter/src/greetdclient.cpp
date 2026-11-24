// SPDX-License-Identifier: AGPL-3.0-or-later
#include "greetdclient.h"

#include <QJsonArray>
#include <QJsonDocument>
#include <QtEndian>
#include <cstring>

GreetdClient::GreetdClient(QObject *parent) : QObject(parent) {
    m_replyTimeout.setSingleShot(true);
    m_replyTimeout.setInterval(20000);
    connect(&m_replyTimeout, &QTimer::timeout, this, &GreetdClient::onReplyTimeout);
    connect(&m_sock, &QLocalSocket::readyRead, this, &GreetdClient::onReadyRead);
    connect(&m_sock, &QLocalSocket::disconnected, this, &GreetdClient::onDisconnected);
}

bool GreetdClient::connectToGreetd(const QString &path) {
    m_dead = false;
    m_buf.clear();
    m_sock.connectToServer(path);
    if (!m_sock.waitForConnected(3000)) {
        failFatal(QStringLiteral("cannot connect to greetd: %1").arg(m_sock.errorString()));
        return false;
    }
    m_state = State::Idle;
    return true;
}

void GreetdClient::failFatal(const QString &why) {
    if (m_dead) return;
    m_dead = true;
    m_replyTimeout.stop();
    m_state = State::Disconnected;
    m_sock.abort();
    emit fatal(why);
}

void GreetdClient::sendRequest(const QJsonObject &o, State next) {
    if (m_dead || m_sock.state() != QLocalSocket::ConnectedState) {
        failFatal(QStringLiteral("not connected to greetd"));
        return;
    }
    const QByteArray payload = QJsonDocument(o).toJson(QJsonDocument::Compact);
    quint32 len = quint32(payload.size());
    QByteArray frame(4, 0);
    std::memcpy(frame.data(), &len, 4);  // native endian, as greetd expects
    frame += payload;
    m_sock.write(frame);
    m_state = next;
    // Only the requests greetd answers immediately are timed. create_session and
    // the answers to auth messages can legitimately block for a long time (PAM
    // waiting for a fingerprint, a touch key, an account lockout delay).
    if (next == State::Cancelling || next == State::Starting) m_replyTimeout.start();
    else m_replyTimeout.stop();
}

void GreetdClient::login(const QString &username) {
    if (m_dead) return;
    if (m_state == State::Cancelling || m_state == State::Starting || m_state == State::Creating || m_state == State::Answering) {
        // A request is in flight: cancel (once its reply is in) and then log in.
        m_queuedUser = username;
        m_haveQueued = true;
        if (m_state == State::Creating || m_state == State::Answering) m_failPending = false;
        return;
    }
    if (m_state == State::Prompting) {
        m_queuedUser = username;
        m_haveQueued = true;
        sendCancelThenMaybeLogin();
        return;
    }
    m_user = username;
    sendRequest({{"type", "create_session"}, {"username", username}}, State::Creating);
}

void GreetdClient::respond(const QString &text) {
    if (m_state != State::Prompting) return;
    sendRequest({{"type", "post_auth_message_response"}, {"response", text}}, State::Answering);
}

void GreetdClient::cancel() {
    if (m_dead) return;
    m_haveQueued = false;
    if (m_state == State::Prompting) {
        sendCancelThenMaybeLogin();
    } else if (m_state == State::Creating || m_state == State::Answering) {
        // The reply is in flight: cancel as soon as it lands, log nobody in.
        m_queuedUser.clear();
        m_haveQueued = true;
    }
}

void GreetdClient::sendCancelThenMaybeLogin() {
    sendRequest({{"type", "cancel_session"}}, State::Cancelling);
}

void GreetdClient::startSessionNow() {
    QJsonArray cmd, env;
    for (const QString &s : m_cmd) cmd.append(s);
    for (const QString &s : m_env) env.append(s);
    sendRequest({{"type", "start_session"}, {"cmd", cmd}, {"env", env}}, State::Starting);
}

void GreetdClient::onReadyRead() {
    m_buf += m_sock.readAll();
    while (!m_dead) {
        if (m_buf.size() < 4) return;
        quint32 len;
        std::memcpy(&len, m_buf.constData(), 4);
        if (len == 0 || len > kMaxFrame) {
            failFatal(QStringLiteral("greetd sent an invalid frame length (%1)").arg(len));
            return;
        }
        if (quint32(m_buf.size()) < 4 + len) return;
        const QByteArray payload = m_buf.mid(4, int(len));
        m_buf.remove(0, int(4 + len));
        QJsonParseError pe;
        const QJsonDocument d = QJsonDocument::fromJson(payload, &pe);
        if (pe.error != QJsonParseError::NoError || !d.isObject()) {
            failFatal(QStringLiteral("greetd sent a malformed reply"));
            return;
        }
        handleReply(d.object());
    }
}

void GreetdClient::onDisconnected() {
    if (m_dead || m_finished) return;
    failFatal(QStringLiteral("the connection to greetd was closed"));
}

void GreetdClient::onReplyTimeout() {
    failFatal(QStringLiteral("greetd did not answer in time"));
}

void GreetdClient::handleReply(const QJsonObject &o) {
    m_replyTimeout.stop();
    const QString type = o.value(QStringLiteral("type")).toString();
    const bool awaiting = m_state == State::Creating || m_state == State::Answering ||
                          m_state == State::Starting || m_state == State::Cancelling;
    if (!awaiting) {
        failFatal(QStringLiteral("greetd sent an unexpected reply"));
        return;
    }
    if (type != QLatin1String("success") && type != QLatin1String("error") && type != QLatin1String("auth_message")) {
        failFatal(QStringLiteral("greetd sent an unknown reply type"));
        return;
    }

    if (m_state == State::Cancelling) {
        // The reply to cancel_session. Whatever it says, the session is gone.
        m_state = State::Idle;
        if (m_failPending) {
            m_failPending = false;
            emit authFailed(m_failMessage, m_failCredentials);
        }
        if (m_haveQueued && !m_dead) {
            m_haveQueued = false;
            if (!m_queuedUser.isEmpty()) login(m_queuedUser);
        }
        return;
    }

    if (type == QLatin1String("auth_message")) {
        if (m_state == State::Starting) {
            failFatal(QStringLiteral("greetd asked a question while starting the session"));
            return;
        }
        const QString kind = o.value(QStringLiteral("auth_message_type")).toString();
        const QString text = o.value(QStringLiteral("auth_message")).toString();
        if (kind != QLatin1String("visible") && kind != QLatin1String("secret") && kind != QLatin1String("info") &&
            kind != QLatin1String("error")) {
            failFatal(QStringLiteral("greetd sent an unknown auth message type"));
            return;
        }
        if (m_haveQueued) {
            // The caller moved on to another user: drop this conversation.
            sendCancelThenMaybeLogin();
            return;
        }
        if (kind == QLatin1String("info") || kind == QLatin1String("error")) {
            emit promptRequested(kind, text);
            // Informative: acknowledge with no response so PAM carries on.
            sendRequest({{"type", "post_auth_message_response"}, {"response", QJsonValue::Null}}, State::Answering);
        } else {
            m_state = State::Prompting;
            emit promptRequested(kind, text);
        }
        return;
    }

    if (type == QLatin1String("success")) {
        if (m_state == State::Starting) {
            m_state = State::Idle;
            m_finished = true;  // greetd may close the socket now: that is not an error
            emit sessionStarting();
            return;
        }
        if (m_haveQueued) {
            // Authenticated, but the caller changed their mind.
            sendCancelThenMaybeLogin();
            return;
        }
        if (m_cmd.isEmpty()) {
            failFatal(QStringLiteral("no session command configured"));
            return;
        }
        startSessionNow();
        return;
    }

    // type == error
    const QString et = o.value(QStringLiteral("error_type")).toString();
    const QString desc = o.value(QStringLiteral("description")).toString();
    const bool wasStarting = m_state == State::Starting;
    m_failMessage = desc;
    m_failCredentials = (et == QLatin1String("auth_error")) && !wasStarting;
    m_failPending = !m_haveQueued;
    sendCancelThenMaybeLogin();
}
