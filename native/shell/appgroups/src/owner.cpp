// SPDX-License-Identifier: AGPL-3.0-or-later
#include "owner.h"

#include <QByteArray>
#include <QDir>
#include <QFileInfo>
#include <QJsonDocument>
#include <QJsonObject>
#include <QRandomGenerator>
#include <QStringList>
#include <QDateTime>
#include <QFile>

#include <cstring>

#include <algorithm>
#include <atomic>
#include <mutex>
#include <thread>
#include <vector>

#include <fcntl.h>
#include <poll.h>
#include <pwd.h>
#include <sys/socket.h>
#include <sys/stat.h>
#include <sys/un.h>
#include <unistd.h>

using namespace Qt::StringLiterals;

namespace TaosAppGroups {

namespace {

constexpr qint64 kLifetimeSecs = 120;

QString accountName()
{
    if (const passwd *pw = ::getpwuid(::geteuid()))
        return QString::fromLocal8Bit(pw->pw_name);
    return u"uid %1"_s.arg(::geteuid());
}

bool ctEq(const QByteArray &a, const QByteArray &b)
{
    if (a.size() != b.size())
        return false;
    unsigned char r = 0;
    for (int i = 0; i < a.size(); ++i)
        r |= static_cast<unsigned char>(a[i]) ^ static_cast<unsigned char>(b[i]);
    return r == 0;
}

bool peerIsMe(int fd)
{
    ucred c{};
    socklen_t n = sizeof c;
    return ::getsockopt(fd, SOL_SOCKET, SO_PEERCRED, &c, &n) == 0 && c.uid == ::geteuid();
}

void setTimeout(int fd, int secs)
{
    timeval tv{secs, 0};
    ::setsockopt(fd, SOL_SOCKET, SO_RCVTIMEO, &tv, sizeof tv);
    ::setsockopt(fd, SOL_SOCKET, SO_SNDTIMEO, &tv, sizeof tv);
}

bool sockAddr(const QString &path, sockaddr_un *a)
{
    const QByteArray p = QFile::encodeName(path);
    if (p.isEmpty() || p.size() >= int(sizeof a->sun_path))
        return false;
    *a = {};
    a->sun_family = AF_UNIX;
    ::memcpy(a->sun_path, p.constData(), size_t(p.size()));
    return true;
}

QByteArray readLine(int fd)
{
    QByteArray buf;
    char c;
    while (buf.size() < 4096) {
        const ssize_t n = ::read(fd, &c, 1);
        if (n <= 0 || c == '\n')
            break;
        buf.append(c);
    }
    return buf;
}

void writeAll(int fd, QByteArray b)
{
    b.append('\n');
    ssize_t off = 0;
    while (off < b.size()) {
        const ssize_t n = ::write(fd, b.constData() + off, size_t(b.size() - off));
        if (n <= 0)
            return;
        off += n;
    }
}

QByteArray json(const QJsonObject &o) { return QJsonDocument(o).toJson(QJsonDocument::Compact); }

} // namespace

QString ownerDir()
{
    const QString o = qEnvironmentVariable("TAOS_APPGROUPS_OWNER_DIR");
    if (!o.isEmpty())
        return o;
    const QString x = qEnvironmentVariable("XDG_RUNTIME_DIR");
    return x.isEmpty() ? QString() : x + u"/taos"_s;
}

QString ownerSocketPath()
{
    const QString d = ownerDir();
    return d.isEmpty() ? QString() : d + u"/appgroups-owner.sock"_s;
}

OwnerProof consumeOwnerNonce(const QString &nonce, const QString &action, const QString &subject)
{
    OwnerProof r;
    if (nonce.isEmpty()) {
        r.error = u"no confirmation from the person was given"_s;
        return r;
    }
    sockaddr_un addr;
    if (!sockAddr(ownerSocketPath(), &addr)) {
        r.error = u"the Store's confirmation socket is not available"_s;
        return r;
    }
    struct stat st{};
    if (::lstat(addr.sun_path, &st) != 0 || !S_ISSOCK(st.st_mode) || st.st_uid != ::geteuid() || (st.st_mode & 077) != 0) {
        r.error = u"the Store is not running, so there is nobody to confirm"_s;
        return r;
    }
    const int fd = ::socket(AF_UNIX, SOCK_STREAM | SOCK_CLOEXEC, 0);
    if (fd < 0) {
        r.error = u"cannot open a socket"_s;
        return r;
    }
    setTimeout(fd, 5);
    if (::connect(fd, reinterpret_cast<sockaddr *>(&addr), sizeof addr) != 0 || !peerIsMe(fd)) {
        ::close(fd);
        r.error = u"the Store is not running, so there is nobody to confirm"_s;
        return r;
    }
    writeAll(fd, json({{u"op"_s, u"consume"_s}, {u"nonce"_s, nonce}, {u"action"_s, action}, {u"subject"_s, subject}}));
    const QJsonObject a = QJsonDocument::fromJson(readLine(fd)).object();
    ::close(fd);
    if (!a.value(u"ok"_s).toBool()) {
        r.error = a.value(u"error"_s).toString(u"the confirmation was not accepted"_s);
        return r;
    }
    r.ok = true;
    r.by = a.value(u"by"_s).toString();
    return r;
}

struct OwnerGate::Impl {
    struct Nonce {
        QByteArray value;
        QString action, subject;
        qint64 expires;
    };
    std::mutex mu;
    std::vector<Nonce> nonces;
    int lfd = -1;
    int wake[2] = {-1, -1};
    std::thread th;
    std::atomic<bool> running{false};
    QString path;

    bool consume(const QByteArray &value, const QString &action, const QString &subject)
    {
        const qint64 now = QDateTime::currentSecsSinceEpoch();
        std::lock_guard<std::mutex> g(mu);
        nonces.erase(std::remove_if(nonces.begin(), nonces.end(), [now](const Nonce &n) { return n.expires <= now; }), nonces.end());
        for (size_t i = 0; i < nonces.size(); ++i) {
            if (!ctEq(nonces[i].value, value))
                continue;
            const Nonce n = nonces[i];
            nonces.erase(nonces.begin() + long(i));   // used up whether or not the scope matches
            return n.action == action && n.subject == subject;
        }
        return false;
    }

    void handle(int fd)
    {
        if (!peerIsMe(fd)) {
            writeAll(fd, json({{u"ok"_s, false}, {u"error"_s, u"not the person who runs the Store"_s}}));
            return;
        }
        setTimeout(fd, 5);
        const QJsonObject q = QJsonDocument::fromJson(readLine(fd)).object();
        if (q.value(u"op"_s).toString() != u"consume"_s) {
            writeAll(fd, json({{u"ok"_s, false}, {u"error"_s, u"unknown request"_s}}));
            return;
        }
        if (consume(q.value(u"nonce"_s).toString().toLatin1(), q.value(u"action"_s).toString(), q.value(u"subject"_s).toString()))
            writeAll(fd, json({{u"ok"_s, true}, {u"by"_s, accountName() + u" (confirmed in the Store)"_s}}));
        else
            writeAll(fd, json({{u"ok"_s, false}, {u"error"_s, u"that confirmation was not given by the person in the Store, was already used, has expired, or is for something else"_s}}));
    }

    void loop()
    {
        while (running) {
            pollfd p[2] = {{lfd, POLLIN, 0}, {wake[0], POLLIN, 0}};
            if (::poll(p, 2, -1) < 0)
                continue;
            if (p[1].revents)
                break;
            if (p[0].revents & POLLIN) {
                const int c = ::accept4(lfd, nullptr, nullptr, SOCK_CLOEXEC);
                if (c >= 0) {
                    handle(c);
                    ::close(c);
                }
            }
        }
    }
};

OwnerGate::OwnerGate() : d(std::make_unique<Impl>()) {}

OwnerGate::~OwnerGate()
{
    if (!d->running)
        return;
    d->running = false;
    if (::write(d->wake[1], "x", 1) < 0) {}
    d->th.join();
    ::close(d->lfd);
    ::close(d->wake[0]);
    ::close(d->wake[1]);
    ::unlink(QFile::encodeName(d->path).constData());
}

bool OwnerGate::serving() const { return d->running; }

bool OwnerGate::start(QString *error)
{
    auto fail = [&](const QString &m) { if (error) *error = m; return false; };
    const QString dir = ownerDir();
    d->path = ownerSocketPath();
    sockaddr_un addr;
    if (dir.isEmpty() || !sockAddr(d->path, &addr))
        return fail(u"there is no per-user runtime directory for the confirmation socket"_s);
    QDir().mkpath(dir);
    ::chmod(QFile::encodeName(dir).constData(), 0700);
    struct stat st{};
    if (::lstat(QFile::encodeName(dir).constData(), &st) != 0 || !S_ISDIR(st.st_mode) || st.st_uid != ::geteuid() || (st.st_mode & 077) != 0)
        return fail(u"the confirmation directory is not private to you"_s);
    // a live server already owns the socket: do not take it over
    if (::lstat(addr.sun_path, &st) == 0) {
        const int t = ::socket(AF_UNIX, SOCK_STREAM | SOCK_CLOEXEC, 0);
        const bool live = t >= 0 && ::connect(t, reinterpret_cast<sockaddr *>(&addr), sizeof addr) == 0;
        if (t >= 0)
            ::close(t);
        if (live)
            return fail(u"another Store already serves confirmations"_s);
        ::unlink(addr.sun_path);
    }
    d->lfd = ::socket(AF_UNIX, SOCK_STREAM | SOCK_CLOEXEC, 0);
    if (d->lfd < 0)
        return fail(u"cannot open a socket"_s);
    const mode_t old = ::umask(077);
    const int br = ::bind(d->lfd, reinterpret_cast<sockaddr *>(&addr), sizeof addr);
    ::umask(old);
    if (br != 0 || ::chmod(addr.sun_path, 0600) != 0 || ::listen(d->lfd, 8) != 0 || ::pipe2(d->wake, O_CLOEXEC) != 0) {
        ::close(d->lfd);
        return fail(u"cannot serve the confirmation socket"_s);
    }
    d->running = true;
    d->th = std::thread([this] { d->loop(); });
    return true;
}

QString OwnerGate::mint(const QString &action, const QString &subject)
{
    if (!d->running || subject.isEmpty() || subject.size() > 300)
        return {};
    QByteArray v;
    for (int i = 0; i < 6; ++i) {
        const quint32 r = QRandomGenerator::system()->generate();
        v += QByteArray::number(r, 16).rightJustified(8, '0');
    }
    std::lock_guard<std::mutex> g(d->mu);
    d->nonces.push_back({v, action, subject, QDateTime::currentSecsSinceEpoch() + kLifetimeSecs});
    return QString::fromLatin1(v);
}

} // namespace TaosAppGroups
