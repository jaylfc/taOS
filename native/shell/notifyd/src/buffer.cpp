// SPDX-License-Identifier: AGPL-3.0-or-later
#include "buffer.h"

#include <QDateTime>
#include <QFile>
#include <QJsonArray>
#include <QJsonDocument>
#include <QFileInfo>

#include <fcntl.h>
#include <unistd.h>

using namespace Qt::StringLiterals;

namespace Taos::Notifyd {

namespace {
constexpr quint64 kSeqBlock = 100;
}

WriteAheadBuffer::WriteAheadBuffer(const Options &o) : m_o(o) {}

bool WriteAheadBuffer::syncDir(const QString &dir) const
{
    const int fd = ::open(QFile::encodeName(dir).constData(), O_RDONLY | O_DIRECTORY);
    if (fd < 0)
        return false;
    const bool ok = ::fsync(fd) == 0;
    ::close(fd);
    return ok;
}

bool WriteAheadBuffer::writeFileDurably(const QString &path, const QByteArray &bytes, QString *error) const
{
    const QString tmp = path + u".tmp"_s;
    const int fd = ::open(QFile::encodeName(tmp).constData(), O_WRONLY | O_CREAT | O_TRUNC | O_CLOEXEC, 0600);
    if (fd < 0) {
        if (error) *error = u"cannot create %1: %2"_s.arg(tmp, QString::fromLocal8Bit(strerror(errno)));
        return false;
    }
    qsizetype off = 0;
    bool ok = true;
    while (off < bytes.size()) {
        const ssize_t w = ::write(fd, bytes.constData() + off, size_t(bytes.size() - off));
        if (w < 0) { ok = false; break; }
        off += w;
    }
    ok = ok && ::fsync(fd) == 0;
    ::close(fd);
    if (ok)
        ok = ::rename(QFile::encodeName(tmp).constData(), QFile::encodeName(path).constData()) == 0;
    if (ok)
        ok = syncDir(QFileInfo(path).absolutePath());
    if (!ok) {
        if (error) *error = u"cannot write %1: %2"_s.arg(path, QString::fromLocal8Bit(strerror(errno)));
        QFile::remove(tmp);
    }
    return ok;
}

bool WriteAheadBuffer::open(QString *error)
{
    QDir d(m_o.dir);
    if (!d.mkpath(u"."_s) || !d.mkpath(u"rejected"_s)) {
        if (error) *error = u"cannot create %1"_s.arg(m_o.dir);
        return false;
    }
    QFile::setPermissions(m_o.dir, QFileDevice::ReadOwner | QFileDevice::WriteOwner | QFileDevice::ExeOwner);
    QFile::setPermissions(d.filePath(u"rejected"_s), QFileDevice::ReadOwner | QFileDevice::WriteOwner | QFileDevice::ExeOwner);
    // A crash during a write leaves only a temp file; the record was never accepted.
    for (const QString &f : d.entryList({u"*.tmp"_s}, QDir::Files))
        QFile::remove(d.filePath(f));
    quint64 hw = 0;
    QFile sf(d.filePath(u"seq"_s));
    if (sf.open(QIODevice::ReadOnly))
        hw = sf.readAll().trimmed().toULongLong();
    // The counter file may lag the directory after a crash: never go below what is on disk.
    quint64 top = hw;
    const QStringList dirs{m_o.dir, d.filePath(u"rejected"_s)};
    for (const QString &dir : dirs)
        for (const QString &f : QDir(dir).entryList({u"*.json"_s}, QDir::Files))
            top = qMax(top, f.left(20).toULongLong());
    m_next = top + 1;
    m_highWater = hw;
    return true;
}

quint64 WriteAheadBuffer::nextSeq()
{
    if (m_next > m_highWater) {
        const quint64 newHw = m_next + kSeqBlock;
        QString err;
        if (writeFileDurably(QDir(m_o.dir).filePath(u"seq"_s), QByteArray::number(newHw), &err))
            m_highWater = newHw;
        // If the counter file cannot be written the id is still unique within this run,
        // and open() restarts above every record on disk.
    }
    return m_next++;
}

QString WriteAheadBuffer::nameFor(quint64 seq, bool critical) const
{
    return u"%1-%2.json"_s.arg(seq, 20, 10, QChar(u'0')).arg(critical ? u'c' : u'n');
}

QList<WriteAheadBuffer::Entry> WriteAheadBuffer::scan() const
{
    QList<Entry> out;
    const QDir d(m_o.dir);
    for (const QString &f : d.entryList({u"*.json"_s}, QDir::Files, QDir::Name)) {
        Entry e;
        e.seq = f.left(20).toULongLong();
        e.critical = f.mid(21, 1) == u"c"_s;
        e.path = d.filePath(f);
        QFile file(e.path);
        if (!file.open(QIODevice::ReadOnly))
            continue;
        const QJsonDocument doc = QJsonDocument::fromJson(file.readAll());
        if (!doc.isObject())
            continue; // unreadable: left in place, never deleted
        e.record = doc.object();
        out.append(e);
    }
    return out;
}

QList<WriteAheadBuffer::Entry> WriteAheadBuffer::pending() const { return scan(); }

int WriteAheadBuffer::count() const { return int(QDir(m_o.dir).entryList({u"*.json"_s}, QDir::Files).size()); }

WriteAheadBuffer::AppendResult WriteAheadBuffer::append(quint64 seq, bool critical, const QJsonObject &record)
{
    AppendResult r;
    const QList<Entry> now = scan();
    if (now.size() >= m_o.hardMax) {
        r.error = u"notification buffer is full (%1 records)"_s.arg(now.size());
        return r;
    }
    int nonCritical = 0;
    for (const Entry &e : now)
        if (!e.critical)
            ++nonCritical;
    if (!critical && nonCritical >= m_o.maxNonCritical) {
        // Overflow: drop the oldest non-critical batch, warning record first.
        QList<Entry> victims;
        for (const Entry &e : now)
            if (!e.critical && victims.size() < m_o.dropBatch)
                victims.append(e);
        QString err;
        // 1. the visible warning record (critical, so it is never itself dropped)
        const quint64 wseq = nextSeq();
        QJsonObject w;
        w[u"id"_s] = u"n%1"_s.arg(wseq);
        w[u"app_name"_s] = u"taOS"_s;
        w[u"desktop_entry"_s] = u"taos-notifyd"_s;
        w[u"summary"_s] = u"Some notifications were dropped"_s;
        w[u"body"_s] = u"%1 older notifications were dropped because the taOS controller could not be reached "
                       u"and the local buffer was full. A full copy of each was kept in %2."_s
                           .arg(victims.size()).arg(QDir(m_o.dir).filePath(u"dropped.jsonl"_s));
        w[u"urgency"_s] = u"critical"_s;
        w[u"category"_s] = u"taos.notifyd.dropped"_s;
        w[u"icon"_s] = QString();
        w[u"actions"_s] = QJsonArray();
        w[u"expire_timeout_ms"_s] = -1;
        w[u"transient"_s] = false;
        w[u"posted_at"_s] = QDateTime::currentSecsSinceEpoch();
        if (!writeFileDurably(QDir(m_o.dir).filePath(nameFor(wseq, true)),
                              QJsonDocument(w).toJson(QJsonDocument::Compact), &err)) {
            r.error = u"buffer full and the warning record could not be written: %1"_s.arg(err);
            return r; // nothing dropped
        }
        // 2. a full copy of every victim, appended and synced
        QByteArray lines;
        for (const Entry &v : victims)
            lines += QJsonDocument(v.record).toJson(QJsonDocument::Compact) + '\n';
        const int fd = ::open(QFile::encodeName(QDir(m_o.dir).filePath(u"dropped.jsonl"_s)).constData(),
                              O_WRONLY | O_CREAT | O_APPEND | O_CLOEXEC, 0600);
        bool logged = fd >= 0;
        if (logged) {
            logged = ::write(fd, lines.constData(), size_t(lines.size())) == lines.size() && ::fsync(fd) == 0;
            ::close(fd);
        }
        if (!logged) {
            r.error = u"buffer full and dropped.jsonl could not be written"_s;
            return r; // the warning stays; nothing was dropped
        }
        // 3. only now delete
        for (const Entry &v : victims)
            if (QFile::remove(v.path))
                ++r.dropped;
    }
    QString err;
    if (!writeFileDurably(QDir(m_o.dir).filePath(nameFor(seq, critical)),
                          QJsonDocument(record).toJson(QJsonDocument::Compact), &err)) {
        r.error = err;
        return r;
    }
    r.ok = true;
    return r;
}

bool WriteAheadBuffer::confirm(quint64 seq)
{
    const QDir d(m_o.dir);
    bool ok = true;
    for (const bool c : {false, true}) {
        const QString p = d.filePath(nameFor(seq, c));
        if (QFile::exists(p))
            ok = QFile::remove(p) && ok;
    }
    return ok;
}

bool WriteAheadBuffer::quarantine(quint64 seq)
{
    const QDir d(m_o.dir);
    for (const bool c : {false, true}) {
        const QString p = d.filePath(nameFor(seq, c));
        if (QFile::exists(p))
            return QFile::rename(p, d.filePath(u"rejected/"_s + nameFor(seq, c)));
    }
    return false;
}

} // namespace Taos::Notifyd
