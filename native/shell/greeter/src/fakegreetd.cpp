// SPDX-License-Identifier: AGPL-3.0-or-later
#include "fakegreetd.h"

#include <QJsonArray>
#include <QJsonDocument>
#include <QJsonObject>
#include <QTimer>
#include <cstring>

FakeGreetd::FakeGreetd(QObject *parent) : QObject(parent) {
    connect(&m_server, &QLocalServer::newConnection, this, &FakeGreetd::onConnection);
}

bool FakeGreetd::listen(const QString &path) {
    QLocalServer::removeServer(path);
    m_server.setSocketOptions(QLocalServer::UserAccessOption);
    return m_server.listen(path);
}

void FakeGreetd::onConnection() {
    m_sock = m_server.nextPendingConnection();
    connect(m_sock, &QLocalSocket::readyRead, this, &FakeGreetd::onData);
}

void FakeGreetd::onData() {
    m_buf += m_sock->readAll();
    while (m_buf.size() >= 4) {
        quint32 len;
        std::memcpy(&len, m_buf.constData(), 4);
        if (len > (1u << 20)) { m_sock->abort(); return; }
        if (quint32(m_buf.size()) < 4 + len) return;
        const QJsonDocument d = QJsonDocument::fromJson(m_buf.mid(4, int(len)));
        m_buf.remove(0, int(4 + len));
        if (d.isObject()) handle(d.object());
    }
}

void FakeGreetd::reply(const QJsonObject &o, int delayMs) {
    QPointer<QLocalSocket> s = m_sock;
    auto send = [s, o] {
        if (!s) return;
        const QByteArray p = QJsonDocument(o).toJson(QJsonDocument::Compact);
        quint32 len = quint32(p.size());
        QByteArray f(4, 0);
        std::memcpy(f.data(), &len, 4);
        s->write(f + p);
    };
    if (delayMs > 0) QTimer::singleShot(delayMs, this, send);
    else send();
}

void FakeGreetd::handle(const QJsonObject &o) {
    const QString type = o.value("type").toString();
    if (type == "create_session") {
        m_user = o.value("username").toString();
        m_step = 1;
        reply({{"type", "auth_message"}, {"auth_message_type", "secret"}, {"auth_message", "Password: "}});
    } else if (type == "post_auth_message_response") {
        const QString resp = o.value("response").toString();
        if (m_step == 1) {
            if (resp == "demo") {
                if (m_otp) {
                    m_step = 2;
                    reply({{"type", "auth_message"}, {"auth_message_type", "visible"}, {"auth_message", "Verification code: "}}, m_delay);
                } else {
                    m_step = 3;
                    reply({{"type", "success"}}, m_delay);
                }
            } else {
                m_step = 0;
                reply({{"type", "error"}, {"error_type", "auth_error"}, {"description", "pam_authenticate: AUTH_ERR"}}, m_delay);
            }
        } else if (m_step == 2) {
            if (resp == "123456") {
                m_step = 3;
                reply({{"type", "success"}}, m_delay / 2);
            } else {
                m_step = 0;
                reply({{"type", "error"}, {"error_type", "auth_error"}, {"description", "pam_authenticate: AUTH_ERR"}}, m_delay);
            }
        }
    } else if (type == "cancel_session") {
        m_step = 0;
        reply({{"type", "success"}});
    } else if (type == "start_session") {
        if (m_step != 3) {
            reply({{"type", "error"}, {"error_type", "error"}, {"description", "no authenticated session"}});
            return;
        }
        m_cmd.clear();
        for (const QJsonValue &v : o.value("cmd").toArray()) m_cmd << v.toString();
        reply({{"type", "success"}});
        emit sessionStartRequested(m_user, m_cmd);
    }
}
