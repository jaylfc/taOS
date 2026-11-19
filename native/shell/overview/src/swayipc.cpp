#include "swayipc.h"

#include <QJsonArray>
#include <QJsonDocument>
#include <QtEndian>
#include <cstring>

namespace SwayWire {
static const char kMagic[] = "i3-ipc";

QByteArray encode(quint32 type, const QByteArray &payload) {
    QByteArray b;
    b.append(kMagic, 6);
    quint32 len = quint32(payload.size());
    b.append(reinterpret_cast<const char *>(&len), 4);   // native endian, as sway does
    b.append(reinterpret_cast<const char *>(&type), 4);
    b.append(payload);
    return b;
}

void Parser::feed(const QByteArray &data) { m_buf.append(data); }

bool Parser::next(Message *m) {
    if (m_buf.size() < 14) return false;
    if (std::memcmp(m_buf.constData(), kMagic, 6) != 0) { m_broken = true; return false; }
    quint32 len, type;
    std::memcpy(&len, m_buf.constData() + 6, 4);
    std::memcpy(&type, m_buf.constData() + 10, 4);
    if (len > (64u << 20)) { m_broken = true; return false; }
    if (quint32(m_buf.size()) < 14 + len) return false;
    m->type = type;
    m->payload = m_buf.mid(14, int(len));
    m_buf.remove(0, 14 + int(len));
    return true;
}
}

SwayIpc::SwayIpc(QObject *parent) : QObject(parent) {
    connect(&m_sock, &QLocalSocket::readyRead, this, &SwayIpc::onReadyRead);
    connect(&m_sock, &QLocalSocket::disconnected, this, [this] {
        while (!m_pending.empty()) {
            auto cb = std::move(m_pending.front());
            m_pending.pop();
            if (cb) cb(false, QByteArray());
        }
        emit disconnected();
    });
}

QString SwayIpc::socketPath() {
    QString p = qEnvironmentVariable("SWAYSOCK");
    if (p.isEmpty()) p = qEnvironmentVariable("I3SOCK");
    return p;
}

bool SwayIpc::connectToSway(const QString &path) {
    const QString p = path.isEmpty() ? socketPath() : path;
    if (p.isEmpty()) return false;
    m_sock.connectToServer(p);
    return m_sock.waitForConnected(1000);
}

bool SwayIpc::isConnected() const { return m_sock.state() == QLocalSocket::ConnectedState; }

void SwayIpc::request(quint32 type, const QByteArray &payload, std::function<void(bool, const QByteArray &)> cb) {
    if (!isConnected()) { if (cb) cb(false, QByteArray()); return; }
    m_pending.push(std::move(cb));
    m_sock.write(SwayWire::encode(type, payload));
}

void SwayIpc::subscribe(const QStringList &events) {
    QJsonArray a;
    for (const QString &e : events) a.append(e);
    request(SwayWire::Subscribe, QJsonDocument(a).toJson(QJsonDocument::Compact));
}

void SwayIpc::onReadyRead() {
    m_parser.feed(m_sock.readAll());
    SwayWire::Message m;
    while (m_parser.next(&m)) {
        if (m.type & 0x80000000u) {
            emit event(m.type, m.payload);
        } else if (!m_pending.empty()) {
            auto cb = std::move(m_pending.front());
            m_pending.pop();
            if (cb) cb(true, m.payload);
        }
    }
    if (m_parser.broken()) m_sock.abort();
}
