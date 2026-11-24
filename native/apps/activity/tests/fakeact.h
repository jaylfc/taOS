// SPDX-License-Identifier: AGPL-3.0-or-later
#pragma once
// Runs tests/fake_activity.py (the foundation's fake controller plus the
// Activity routes) and talks to its control endpoints.
#include <QJsonDocument>
#include <QJsonObject>
#include <QProcess>
#include <QProcessEnvironment>
#include <QTcpSocket>
#include <QUrl>

#include "taosqt/client.h"

class FakeAct
{
public:
    FakeAct()
    {
        QProcessEnvironment env = QProcessEnvironment::systemEnvironment();
        env.insert(QStringLiteral("FAKE_BASE_PY"), QStringLiteral(FAKE_BASE_PY));
        m_proc.setProcessEnvironment(env);
        m_proc.setProgram(QStringLiteral("python3"));
        m_proc.setArguments({QStringLiteral(FAKE_ACT_PY), QStringLiteral(FAKE_BASE_PY)});
        m_proc.start();
        if (m_proc.waitForReadyRead(10000)) {
            const QByteArray line = m_proc.readLine();
            if (line.startsWith("LISTENING "))
                m_port = line.mid(10).trimmed().toUShort();
        }
    }
    ~FakeAct()
    {
        m_proc.kill();
        m_proc.waitForFinished(3000);
    }
    bool ok() const { return m_port != 0; }
    QUrl url() const { return QUrl(QStringLiteral("http://127.0.0.1:%1").arg(m_port)); }

    void act(const QJsonObject &o) { request("POST", "/__fake/act", QJsonDocument(o).toJson(QJsonDocument::Compact)); }
    void base(const QJsonObject &o) { request("POST", "/__fake/config", QJsonDocument(o).toJson(QJsonDocument::Compact)); }
    void reset() { act({{QStringLiteral("reset"), true}}); base({{QStringLiteral("reset"), true}}); }
    QJsonObject stats() { return QJsonDocument::fromJson(request("GET", "/__fake/act/stats", {})).object(); }
    int stat(const char *k) { return stats().value(QLatin1StringView(k)).toInt(); }

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

inline Taos::ClientOptions actOptions(const QUrl &url)
{
    Taos::ClientOptions o;
    o.baseUrl = url;
    o.requestTimeoutMs = 5000;
    o.connectTimeoutMs = 2000;
    o.streamBackoffBaseMs = 20;
    o.streamBackoffCapMs = 100;
    return o;
}
