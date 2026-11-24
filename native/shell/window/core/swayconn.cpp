#include "swayconn.h"

#include <cerrno>
#include <cstring>
#include <poll.h>
#include <sys/socket.h>
#include <sys/un.h>
#include <unistd.h>

namespace TaosWin {

namespace Ipc {
static const char kMagic[] = "i3-ipc";
QByteArray encode(quint32 type, const QByteArray &payload) {
    QByteArray out(kMagic, 6);
    const quint32 len = quint32(payload.size());
    out.append(reinterpret_cast<const char *>(&len), 4);
    out.append(reinterpret_cast<const char *>(&type), 4);
    out.append(payload);
    return out;
}
bool take(QByteArray *buf, quint32 *type, QByteArray *payload) {
    if (buf->size() < 14) return false;
    if (std::memcmp(buf->constData(), kMagic, 6) != 0) { buf->clear(); return false; }
    quint32 len = 0, t = 0;
    std::memcpy(&len, buf->constData() + 6, 4);
    std::memcpy(&t, buf->constData() + 10, 4);
    if (quint64(buf->size()) < 14u + len) return false;
    *type = t;
    *payload = buf->mid(14, int(len));
    buf->remove(0, 14 + int(len));
    return true;
}
}  // namespace Ipc

QString SwayConnection::socketPath() {
    QString p = qEnvironmentVariable("SWAYSOCK");
    if (p.isEmpty()) p = qEnvironmentVariable("I3SOCK");
    return p;
}

bool SwayConnection::open(const QString &pathIn, QString *error) {
    close();
    const QString path = pathIn.isEmpty() ? socketPath() : pathIn;
    if (path.isEmpty()) {
        if (error) *error = QStringLiteral("not running under sway (SWAYSOCK is not set)");
        return false;
    }
    const QByteArray p = path.toLocal8Bit();
    sockaddr_un addr;
    std::memset(&addr, 0, sizeof addr);
    addr.sun_family = AF_UNIX;
    if (size_t(p.size()) >= sizeof addr.sun_path) {
        if (error) *error = QStringLiteral("the sway socket path is too long");
        return false;
    }
    std::memcpy(addr.sun_path, p.constData(), size_t(p.size()));
    const int fd = ::socket(AF_UNIX, SOCK_STREAM | SOCK_CLOEXEC, 0);
    if (fd < 0 || ::connect(fd, reinterpret_cast<sockaddr *>(&addr), sizeof addr) != 0) {
        if (error) *error = QStringLiteral("cannot connect to sway at %1: %2").arg(path, QString::fromLocal8Bit(std::strerror(errno)));
        if (fd >= 0) ::close(fd);
        return false;
    }
    m_fd = fd;
    m_buf.clear();
    return true;
}

void SwayConnection::close() {
    if (m_fd >= 0) ::close(m_fd);
    m_fd = -1;
    m_buf.clear();
}

bool SwayConnection::fill(int timeoutMs, bool *closed) {
    pollfd pf{m_fd, POLLIN, 0};
    int r;
    do { r = ::poll(&pf, 1, timeoutMs); } while (r < 0 && errno == EINTR);
    if (r <= 0) return false;
    char tmp[16384];
    const ssize_t n = ::read(m_fd, tmp, sizeof tmp);
    if (n <= 0) {
        if (n < 0 && (errno == EINTR || errno == EAGAIN)) return false;
        if (closed) *closed = true;
        close();
        return false;
    }
    m_buf.append(tmp, int(n));
    return true;
}

bool SwayConnection::next(quint32 *type, QByteArray *payload, int timeoutMs, bool *closed) {
    if (closed) *closed = false;
    while (true) {
        if (Ipc::take(&m_buf, type, payload)) return true;
        if (m_fd < 0) { if (closed) *closed = true; return false; }
        if (!fill(timeoutMs, closed)) return false;
    }
}

bool SwayConnection::request(quint32 type, const QByteArray &payload, QByteArray *reply, QString *error, int timeoutMs) {
    if (m_fd < 0) {
        if (error) *error = QStringLiteral("not connected to sway");
        return false;
    }
    const QByteArray msg = Ipc::encode(type, payload);
    qsizetype off = 0;
    while (off < msg.size()) {
        const ssize_t n = ::send(m_fd, msg.constData() + off, size_t(msg.size() - off), MSG_NOSIGNAL);
        if (n < 0 && errno == EINTR) continue;
        if (n <= 0) {
            if (error) *error = QStringLiteral("the connection to sway was lost");
            close();
            return false;
        }
        off += n;
    }
    while (true) {
        quint32 t = 0;
        QByteArray p;
        bool closed = false;
        if (!next(&t, &p, timeoutMs, &closed)) {
            if (error) *error = closed ? QStringLiteral("the connection to sway was lost") : QStringLiteral("sway did not answer in time");
            return false;
        }
        if (t & 0x80000000u) continue;  // an event on a subscribed socket
        *reply = p;
        return true;
    }
}

}  // namespace TaosWin
