// SPDX-License-Identifier: AGPL-3.0-or-later
#include "callbackbroker.h"

#include <QDeadlineTimer>
#include <QFile>
#include <QUrl>

#include <cstring>

#include <arpa/inet.h>
#include <fcntl.h>
#include <netinet/in.h>
#include <poll.h>
#include <sys/socket.h>
#include <sys/un.h>
#include <unistd.h>

using namespace Qt::StringLiterals;

namespace TaosAppGroups {

namespace {

bool waitReadable(int fd, int ms)
{
    pollfd p{fd, POLLIN, 0};
    return ::poll(&p, 1, ms) > 0 && (p.revents & (POLLIN | POLLHUP));
}

// Reads until the blank line that ends the request head (at most 8 KiB, at most `ms` in all).
QByteArray readHead(int fd, int ms)
{
    QByteArray buf;
    QDeadlineTimer dl(ms);
    while (buf.size() < 8192 && !buf.contains("\r\n\r\n") && !buf.contains("\n\n")) {
        const qint64 left = dl.remainingTime();
        if (left <= 0 || !waitReadable(fd, int(left)))
            break;
        char tmp[1024];
        const ssize_t n = ::read(fd, tmp, sizeof tmp);
        if (n <= 0)
            break;
        buf.append(tmp, int(n));
    }
    return buf;
}

void writeAll(int fd, const QByteArray &b)
{
    ssize_t off = 0;
    while (off < b.size()) {
        const ssize_t n = ::send(fd, b.constData() + off, size_t(b.size() - off), MSG_NOSIGNAL);
        if (n <= 0)
            return;
        off += n;
    }
}

int connectUnix(const QString &path)
{
    const QByteArray p = QFile::encodeName(path);
    sockaddr_un a{};
    if (p.isEmpty() || p.size() >= int(sizeof a.sun_path))
        return -1;
    a.sun_family = AF_UNIX;
    ::memcpy(a.sun_path, p.constData(), size_t(p.size()));
    const int fd = ::socket(AF_UNIX, SOCK_STREAM | SOCK_CLOEXEC, 0);
    if (fd < 0)
        return -1;
    if (::connect(fd, reinterpret_cast<sockaddr *>(&a), sizeof a) != 0) {
        ::close(fd);
        return -1;
    }
    return fd;
}

// Passes bytes both ways until both sides are done or nothing moves for `idleMs`.
void relay(int a, int b, int idleMs)
{
    bool aOpen = true, bOpen = true;
    while (aOpen || bOpen) {
        pollfd p[2] = {{aOpen ? a : -1, POLLIN, 0}, {bOpen ? b : -1, POLLIN, 0}};
        if (::poll(p, 2, idleMs) <= 0)
            return;
        for (int i = 0; i < 2; ++i) {
            if (!(p[i].revents & (POLLIN | POLLHUP)))
                continue;
            char tmp[4096];
            const ssize_t n = ::read(p[i].fd, tmp, sizeof tmp);
            bool &open = i == 0 ? aOpen : bOpen;
            if (n <= 0) {
                open = false;
                ::shutdown(i == 0 ? b : a, SHUT_WR);
                continue;
            }
            writeAll(i == 0 ? b : a, QByteArray(tmp, int(n)));
        }
    }
}

} // namespace

bool callbackRequestMatches(const QByteArray &head, const QString &expectedPath)
{
    const int eol = head.indexOf('\n');
    const QByteArray line = (eol < 0 ? head : head.left(eol)).trimmed();
    const QList<QByteArray> parts = line.split(' ');
    if (parts.size() != 3 || parts[0] != "GET" || !parts[2].startsWith("HTTP/1."))
        return false;
    const QByteArray target = parts[1];
    if (!target.startsWith('/') || target.startsWith("//"))
        return false;
    QByteArray raw = target;
    for (char stop : {'?', '#'})
        if (const int i = raw.indexOf(stop); i >= 0)
            raw.truncate(i);
    const QString path = QUrl::fromPercentEncoding(raw);
    if (path.contains(u'\0') || path.contains(u"/../"_s) || path.contains(u"/./"_s) || path.endsWith(u"/.."_s) || path.endsWith(u"/."_s))
        return false;
    return path == (expectedPath.isEmpty() ? u"/"_s : expectedPath);
}

int listenCallbackPort(int port, QString *error)
{
    const int fd = ::socket(AF_INET, SOCK_STREAM | SOCK_CLOEXEC, 0);
    if (fd < 0) {
        *error = u"cannot open a socket"_s;
        return -1;
    }
    sockaddr_in a{};
    a.sin_family = AF_INET;
    a.sin_port = htons(quint16(port));
    a.sin_addr.s_addr = htonl(INADDR_LOOPBACK);
    if (::bind(fd, reinterpret_cast<sockaddr *>(&a), sizeof a) != 0 || ::listen(fd, 4) != 0) {
        ::close(fd);
        *error = u"port %1 on this computer is already in use"_s.arg(port);
        return -1;
    }
    return fd;
}

bool serveCallback(int lfd, const QString &expectedPath, const QString &upstream, int seconds)
{
    QDeadlineTimer dl(qint64(seconds) * 1000);
    while (!dl.hasExpired()) {
        if (!waitReadable(lfd, int(qMin<qint64>(250, dl.remainingTime()))))
            continue;
        const int c = ::accept4(lfd, nullptr, nullptr, SOCK_CLOEXEC);
        if (c < 0)
            continue;
        const QByteArray head = readHead(c, 3000);
        if (!callbackRequestMatches(head, expectedPath)) {
            writeAll(c, "HTTP/1.1 404 Not Found\r\nConnection: close\r\nContent-Length: 0\r\n\r\n");
            ::close(c);
            continue;
        }
        const int u = connectUnix(upstream);
        if (u < 0) {
            writeAll(c, "HTTP/1.1 502 Bad Gateway\r\nConnection: close\r\nContent-Length: 0\r\n\r\n");
            ::close(c);
            continue;
        }
        writeAll(u, head);
        relay(c, u, 15000);
        ::close(u);
        ::close(c);
        return true;
    }
    return false;
}

} // namespace TaosAppGroups
