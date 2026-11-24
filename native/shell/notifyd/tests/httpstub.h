// SPDX-License-Identifier: AGPL-3.0-or-later
#pragma once
// A tiny HTTP stub for the tests: one handler per request, answers as told. No sockets leave 127.0.0.1.
#include <QByteArray>
#include <QList>
#include <QMap>
#include <QObject>
#include <QTcpServer>
#include <QTcpSocket>
#include <functional>

struct StubRequest
{
    QString method, path;
    QMap<QByteArray, QByteArray> headers; // lower-case names
    QByteArray body;
};
struct StubReply
{
    int status = 200;
    QByteArray body;
    QList<QPair<QByteArray, QByteArray>> headers;
    bool hang = false;  ///< never answer (timeout test)
    bool drop = false;  ///< close without answering (lost response)
};

class HttpStub : public QObject
{
public:
    std::function<StubReply(const StubRequest &)> handler;
    QList<StubRequest> requests;

    explicit HttpStub(QObject *parent = nullptr) : QObject(parent)
    {
        connect(&m_srv, &QTcpServer::newConnection, this, [this] {
            while (QTcpSocket *s = m_srv.nextPendingConnection()) {
                auto *buf = new QByteArray;
                connect(s, &QTcpSocket::readyRead, this, [this, s, buf] { onData(s, buf); });
                connect(s, &QTcpSocket::disconnected, s, &QObject::deleteLater);
                connect(s, &QObject::destroyed, this, [buf] { delete buf; });
            }
        });
    }
    bool listen(quint16 port = 0) { return m_srv.listen(QHostAddress::LocalHost, port); }
    void close() { m_srv.close(); }
    quint16 port() const { return m_srv.serverPort(); }
    QString url() const { return QStringLiteral("http://127.0.0.1:%1").arg(port()); }

private:
    void onData(QTcpSocket *s, QByteArray *buf)
    {
        buf->append(s->readAll());
        const int he = buf->indexOf("\r\n\r\n");
        if (he < 0)
            return;
        const QList<QByteArray> lines = buf->left(he).split('\n');
        StubRequest r;
        const QList<QByteArray> rl = lines.first().trimmed().split(' ');
        r.method = QString::fromLatin1(rl.value(0));
        r.path = QString::fromLatin1(rl.value(1));
        int clen = 0;
        for (int i = 1; i < lines.size(); ++i) {
            const int c = lines[i].indexOf(':');
            if (c > 0)
                r.headers[lines[i].left(c).trimmed().toLower()] = lines[i].mid(c + 1).trimmed();
        }
        clen = r.headers.value("content-length", "0").toInt();
        if (buf->size() < he + 4 + clen)
            return;
        r.body = buf->mid(he + 4, clen);
        buf->clear();
        requests.append(r);
        const StubReply rep = handler ? handler(r) : StubReply{};
        if (rep.hang)
            return;
        if (rep.drop) {
            s->disconnectFromHost();
            return;
        }
        QByteArray out = "HTTP/1.1 " + QByteArray::number(rep.status) + " X\r\nContent-Type: application/json\r\nContent-Length: "
                         + QByteArray::number(rep.body.size()) + "\r\nConnection: close\r\n";
        for (const auto &h : rep.headers)
            out += h.first + ": " + h.second + "\r\n";
        out += "\r\n" + rep.body;
        s->write(out);
        s->disconnectFromHost();
    }
    QTcpServer m_srv;
};
