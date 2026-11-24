// SPDX-License-Identifier: AGPL-3.0-or-later
#pragma once
// Test helper: runs native/testing/fake_controller.py and talks to its
// control endpoints with plain blocking sockets.
#include <QJsonDocument>
#include <QJsonObject>
#include <QProcess>
#include <QTcpSocket>
#include <QUrl>

class FakeController
{
public:
    FakeController()
    {
        m_proc.setProgram(QStringLiteral("python3"));
        m_proc.setArguments({QStringLiteral(FAKE_CONTROLLER_PY)});
        m_proc.start();
        if (m_proc.waitForReadyRead(10000)) {
            const QByteArray line = m_proc.readLine();
            if (line.startsWith("LISTENING "))
                m_port = line.mid(10).trimmed().toUShort();
        }
    }
    ~FakeController()
    {
        m_proc.kill();
        m_proc.waitForFinished(3000);
    }
    bool ok() const { return m_port != 0; }
    quint16 port() const { return m_port; }
    QUrl url() const { return QUrl(QStringLiteral("http://127.0.0.1:%1").arg(m_port)); }

    void config(const QJsonObject &o) { request("POST", "/__fake/config", QJsonDocument(o).toJson(QJsonDocument::Compact)); }
    void reset() { config({{QStringLiteral("reset"), true}}); }
    int stat(const char *key) const
    {
        const QByteArray body = const_cast<FakeController *>(this)->request("GET", "/__fake/stats", {});
        return QJsonDocument::fromJson(body).object().value(QLatin1StringView(key)).toInt();
    }

private:
    QByteArray request(const char *method, const char *path, const QByteArray &body)
    {
        QTcpSocket s;
        s.connectToHost(QStringLiteral("127.0.0.1"), m_port);
        if (!s.waitForConnected(3000))
            return {};
        s.write(QByteArray(method) + " " + path + " HTTP/1.1\r\nHost: x\r\nConnection: close\r\nContent-Length: "
                + QByteArray::number(body.size()) + "\r\n\r\n" + body);
        s.waitForBytesWritten(3000);
        QByteArray all;
        while (s.waitForReadyRead(3000))
            all += s.readAll();
        all += s.readAll();
        return all.mid(all.indexOf("\r\n\r\n") + 4);
    }

    QProcess m_proc;
    quint16 m_port = 0;
};

#include "taosqt/client.h"

inline Taos::ClientOptions fastOptions(const QUrl &url)
{
    Taos::ClientOptions o;
    o.baseUrl = url;
    o.requestTimeoutMs = 5000;
    o.connectTimeoutMs = 2000;
    o.streamBackoffBaseMs = 20;
    o.streamBackoffCapMs = 100;
    return o;
}

#define FAKE_PASSWORD QStringLiteral("hunter2-secret-pw")
