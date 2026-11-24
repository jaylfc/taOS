// SPDX-License-Identifier: AGPL-3.0-or-later
#include <QDir>
#include <QFile>
#include <QJsonArray>
#include <QJsonDocument>
#include <QTemporaryDir>
#include <QtTest>

#include "buffer.h"

using namespace Taos::Notifyd;
using namespace Qt::StringLiterals;

static QJsonObject rec(const QString &id, const QString &summary = u"s"_s)
{
    return {{u"id"_s, id}, {u"summary"_s, summary}, {u"app_name"_s, u"app"_s}};
}

static quint64 seq(WriteAheadBuffer &b)
{
    quint64 s = 0;
    QString err;
    if (!b.nextSeq(&s, &err))
        qWarning("nextSeq failed: %s", qPrintable(err));
    return s;
}

class TestBuffer : public QObject
{
    Q_OBJECT
    static WriteAheadBuffer::Options opts(const QString &dir, int maxNc = 500, int batch = 50, int hard = 5000)
    {
        WriteAheadBuffer::Options o;
        o.dir = dir + u"/buf"_s;
        o.maxNonCritical = maxNc;
        o.dropBatch = batch;
        o.hardMax = hard;
        return o;
    }
private Q_SLOTS:
    void crashBetweenAcceptAndSend()
    {
        QTemporaryDir t;
        {
            WriteAheadBuffer b(opts(t.path()));
            QVERIFY(b.open(nullptr));
            QVERIFY(b.append(seq(b), false, rec(u"n1"_s)).ok);
            QVERIFY(b.append(seq(b), true, rec(u"n2"_s)).ok);
            // the process dies here: no confirm, no clean shutdown, no destructor work to rely on
        }
        // a half-written record from a crash mid-write is invisible and cleaned up
        QFile tmp(t.path() + u"/buf/00000000000000000099-n.json.tmp"_s);
        QVERIFY(tmp.open(QIODevice::WriteOnly));
        tmp.write("{\"id\":\"half"); tmp.close();
        WriteAheadBuffer b2(opts(t.path()));
        QVERIFY(b2.open(nullptr));
        const auto p = b2.pending();
        QCOMPARE(p.size(), 2);
        QCOMPARE(p[0].record[u"id"_s].toString(), u"n1"_s);
        QVERIFY(p[1].critical);
        QVERIFY(!QFile::exists(tmp.fileName()));
    }
    void filesAreOwnerOnly()
    {
        QTemporaryDir t;
        WriteAheadBuffer b(opts(t.path()));
        QVERIFY(b.open(nullptr));
        QVERIFY(b.append(seq(b), false, rec(u"n1"_s)).ok);
        QCOMPARE(QFileInfo(t.path() + u"/buf"_s).permissions() & 0x77, QFileDevice::Permissions());
        const auto p = b.pending();
        QCOMPARE(QFileInfo(p[0].path).permissions() & 0x77, QFileDevice::Permissions());
    }
    void idsNeverRepeatAfterRestart()
    {
        QTemporaryDir t;
        quint64 last = 0;
        for (int run = 0; run < 3; ++run) {
            WriteAheadBuffer b(opts(t.path()));
            QVERIFY(b.open(nullptr));
            for (int i = 0; i < 7; ++i) {
                const quint64 s = seq(b);
                QVERIFY2(s > last, "id went backwards");
                last = s;
            }
        }
        // the counter file lost (crash before it was written): ids still stay above what is on disk
        WriteAheadBuffer b(opts(t.path()));
        QVERIFY(b.open(nullptr));
        QVERIFY(b.append(last + 50, false, rec(u"x"_s)).ok);
        QFile::remove(t.path() + u"/buf/seq"_s);
        WriteAheadBuffer b2(opts(t.path()));
        QVERIFY(b2.open(nullptr));
        QVERIFY(seq(b2) > last + 50);
    }
    void duplicateConfirmIsHarmless()
    {
        QTemporaryDir t;
        WriteAheadBuffer b(opts(t.path()));
        QVERIFY(b.open(nullptr));
        const quint64 s = seq(b);
        QVERIFY(b.append(s, false, rec(u"n"_s)).ok);
        QVERIFY(b.confirm(s));
        QVERIFY(b.confirm(s)); // the second confirm (a duplicate send answered twice) finds nothing: still fine
        QCOMPARE(b.count(), 0);
    }
    void fullBufferDropsOldestNonCriticalAfterAWarning()
    {
        QTemporaryDir t;
        WriteAheadBuffer b(opts(t.path(), 5, 2));
        QVERIFY(b.open(nullptr));
        QVERIFY(b.append(seq(b), true, rec(u"crit"_s)).ok);
        for (int i = 1; i <= 5; ++i)
            QVERIFY(b.append(seq(b), false, rec(u"m%1"_s.arg(i))).ok);
        const auto r = b.append(seq(b), false, rec(u"m6"_s));
        QVERIFY(r.ok);
        QCOMPARE(r.dropped, 2);
        QStringList ids;
        bool warning = false;
        for (const auto &e : b.pending()) {
            ids << e.record[u"id"_s].toString();
            if (e.record[u"category"_s].toString() == u"taos.notifyd.dropped"_s) {
                warning = true;
                QVERIFY(e.critical);
            }
        }
        QVERIFY2(warning, "the visible warning record must exist");
        QVERIFY(ids.contains(u"crit"_s));
        QVERIFY(!ids.contains(u"m1"_s) && !ids.contains(u"m2"_s));       // the two oldest non-critical
        QVERIFY(ids.contains(u"m3"_s) && ids.contains(u"m6"_s));
        // the dropped ones are still readable in full
        QFile f(t.path() + u"/buf/dropped.jsonl"_s);
        QVERIFY(f.open(QIODevice::ReadOnly));
        const QList<QByteArray> lines = f.readAll().trimmed().split('\n');
        QCOMPARE(lines.size(), 2);
        QCOMPARE(QJsonDocument::fromJson(lines[0]).object()[u"id"_s].toString(), u"m1"_s);
    }
    void nothingIsDroppedWhenTheWarningCannotBeKept()
    {
        QTemporaryDir t;
        WriteAheadBuffer b(opts(t.path(), 3, 2));
        QVERIFY(b.open(nullptr));
        for (int i = 1; i <= 3; ++i)
            QVERIFY(b.append(seq(b), false, rec(u"m%1"_s.arg(i))).ok);
        // dropped.jsonl cannot be created: a directory sits where the file should be
        QVERIFY(QDir().mkpath(t.path() + u"/buf/dropped.jsonl"_s));
        const auto r = b.append(seq(b), false, rec(u"m4"_s));
        QVERIFY(!r.ok);
        QVERIFY(!r.error.isEmpty());
        QStringList ids;
        for (const auto &e : b.pending())
            ids << e.record[u"id"_s].toString();
        QVERIFY(ids.contains(u"m1"_s) && ids.contains(u"m2"_s) && ids.contains(u"m3"_s));
    }
    void criticalNeverDroppedAndHardCapRefusesLoudly()
    {
        QTemporaryDir t;
        WriteAheadBuffer b(opts(t.path(), 2, 1, 10));
        QVERIFY(b.open(nullptr));
        for (int i = 1; i <= 6; ++i)
            QVERIFY(b.append(seq(b), true, rec(u"c%1"_s.arg(i))).ok); // more critical than the non-critical cap
        for (int i = 1; i <= 4; ++i)
            b.append(seq(b), false, rec(u"n%1"_s.arg(i)));
        int crit = 0;
        for (const auto &e : b.pending())
            if (e.critical && e.record[u"id"_s].toString().startsWith(u"c"_s))
                ++crit;
        QCOMPARE(crit, 6);
        // at the hard cap an append is refused with an error, never accepted and dropped
        while (b.count() < 10)
            QVERIFY(b.append(seq(b), true, rec(u"f"_s)).ok);
        const auto r = b.append(seq(b), true, rec(u"over"_s));
        QVERIFY(!r.ok);
        QVERIFY(r.error.contains(u"full"_s));
    }
    void idsAreUniqueAcrossAWipedStateDirectory()
    {
        QTemporaryDir t;
        QString first, firstInstall;
        {
            WriteAheadBuffer b(opts(t.path()));
            QVERIFY(b.open(nullptr));
            first = b.clientId(seq(b));
            firstInstall = b.installId();
            QCOMPARE(firstInstall.size(), 16);
        }
        {   // same directory, next run: same install, so the counter goes on
            WriteAheadBuffer b(opts(t.path()));
            QVERIFY(b.open(nullptr));
            QCOMPARE(b.installId(), firstInstall);
            QVERIFY(b.clientId(seq(b)) != first);
        }
        // the directory is wiped: the counter restarts at 1 but the id must not repeat
        QVERIFY(QDir(t.path() + u"/buf"_s).removeRecursively());
        WriteAheadBuffer b(opts(t.path()));
        QVERIFY(b.open(nullptr));
        QCOMPARE(seq(b), quint64(1));
        QVERIFY(b.installId() != firstInstall);
        QVERIFY(!b.clientId(1).contains(firstInstall));
    }
    void aFailedCounterWriteFailsInsteadOfRepeating()
    {
        QTemporaryDir t;
        WriteAheadBuffer b(opts(t.path()));
        QVERIFY(b.open(nullptr));
        QVERIFY(QDir().mkpath(t.path() + u"/buf/seq"_s)); // the counter file cannot be replaced
        quint64 s = 77;
        QString err;
        QVERIFY(!b.nextSeq(&s, &err));
        QVERIFY(!err.isEmpty());
        QCOMPARE(s, quint64(77)); // no number was handed out
    }
    void pendingComesFromMemoryAfterStartup()
    {
        QTemporaryDir t;
        WriteAheadBuffer b(opts(t.path()));
        QVERIFY(b.open(nullptr));
        const quint64 a = seq(b), c = seq(b);
        QVERIFY(b.append(a, false, rec(u"a"_s)).ok);
        QVERIFY(b.append(c, false, rec(u"c"_s)).ok);
        QCOMPARE(b.pending().size(), 2);
        QVERIFY(b.confirm(a));
        QCOMPARE(b.pending().size(), 1);
        QCOMPARE(b.pending().first().record[u"id"_s].toString(), u"c"_s);
        // and a restart sees the same
        WriteAheadBuffer b2(opts(t.path()));
        QVERIFY(b2.open(nullptr));
        QCOMPARE(b2.count(), 1);
    }
    void quarantineKeepsTheRecord()
    {
        QTemporaryDir t;
        WriteAheadBuffer b(opts(t.path()));
        QVERIFY(b.open(nullptr));
        const quint64 s = seq(b);
        QVERIFY(b.append(s, false, rec(u"bad"_s)).ok);
        QVERIFY(b.quarantine(s));
        QCOMPARE(b.count(), 0);
        QCOMPARE(QDir(t.path() + u"/buf/rejected"_s).entryList({u"*.json"_s}).size(), 1);
    }
};

QTEST_MAIN(TestBuffer)
#include "test_buffer.moc"
