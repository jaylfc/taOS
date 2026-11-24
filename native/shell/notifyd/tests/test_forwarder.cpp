// SPDX-License-Identifier: AGPL-3.0-or-later
#include <QDir>
#include <QFile>
#include <QJsonDocument>
#include <QSet>
#include <QTemporaryDir>
#include <QtTest>

#include "buffer.h"
#include "forwarder.h"
#include "httpstub.h"

using namespace Taos::Notifyd;
using namespace Qt::StringLiterals;

namespace {
const QByteArray kUa = "taOS-native/test";

void writeSession(const QString &runtime, const QString &base, const QString &token = u"tok1"_s, const QString &csrf = u"csrf1"_s)
{
    QDir().mkpath(runtime + u"/taos"_s);
    QFile::setPermissions(runtime + u"/taos"_s, QFileDevice::ReadOwner | QFileDevice::WriteOwner | QFileDevice::ExeOwner);
    QFile f(runtime + u"/taos/session"_s);
    QVERIFY(f.open(QIODevice::WriteOnly | QIODevice::Truncate));
    f.write(QJsonDocument(QJsonObject{{u"v"_s, 1}, {u"base_url"_s, base}, {u"user_agent"_s, QString::fromLatin1(kUa)},
                                      {u"session"_s, token}, {u"csrf"_s, csrf}}).toJson());
    f.close();
    f.setPermissions(QFileDevice::ReadOwner | QFileDevice::WriteOwner);
}
QJsonObject rec(const QString &id, const QString &cat = QString())
{
    return {{u"id"_s, id}, {u"app_name"_s, u"app"_s}, {u"summary"_s, u"s"_s}, {u"category"_s, cat}};
}
}

// The controller as the tests need it: idempotent on the posted id, like the real route.
struct Controller
{
    HttpStub stub;
    QSet<QString> rows;
    QStringList order;
    int next = 1;
    std::function<StubReply(const StubRequest &)> before; // scenario hook, runs first
    Controller()
    {
        stub.handler = [this](const StubRequest &r) -> StubReply {
            if (before) {
                const StubReply s = before(r);
                if (s.status != 0)
                    return s;
            }
            const QString id = QJsonDocument::fromJson(r.body).object()[u"id"_s].toString();
            const bool dup = rows.contains(id);
            if (!dup) { rows.insert(id); order << id; }
            StubReply ok;
            ok.body = QJsonDocument(QJsonObject{{u"id"_s, 100 + next++}, {u"duplicate"_s, dup}}).toJson(QJsonDocument::Compact);
            return ok;
        };
    }
};

class TestForwarder : public QObject
{
    Q_OBJECT
    struct Rig
    {
        QTemporaryDir dir;
        std::unique_ptr<WriteAheadBuffer> buf;
        std::unique_ptr<Forwarder> fwd;
        QString runtime;
        Rig(int backoffMin = 20, int backoffMax = 80)
        {
            runtime = dir.path() + u"/run"_s;
            QDir().mkpath(runtime);
            WriteAheadBuffer::Options bo;
            bo.dir = dir.path() + u"/buf"_s;
            buf = std::make_unique<WriteAheadBuffer>(bo);
            buf->open(nullptr);
            Forwarder::Options fo;
            fo.runtimeDir = runtime;
            fo.backoffMinMs = backoffMin;
            fo.backoffMaxMs = backoffMax;
            fo.requestTimeoutMs = 400;
            fwd = std::make_unique<Forwarder>(buf.get(), fo);
        }
        void add(const QString &id, const QString &cat = QString())
        {
            QVERIFY(buf->append(buf->nextSeq(), false, rec(id, cat)).ok);
        }
    };

private Q_SLOTS:
    void classifyTable()
    {
        QCOMPARE(classify(200, R"({"id":5,"duplicate":false})"), Verdict::Confirm);
        QCOMPARE(classify(200, R"({"id":null,"stored":false})"), Verdict::Confirm); // transient or muted
        QCOMPARE(classify(200, "<html>captive portal</html>"), Verdict::RetryBackoff);
        QCOMPARE(classify(200, R"({"ok":true})"), Verdict::RetryBackoff);
        for (int s : {400, 413, 422})
            QCOMPARE(classify(s, "{}"), Verdict::Quarantine);
        QCOMPARE(classify(429, "{}"), Verdict::RetryAfter);
        for (int s : {401, 403, 404, 500, 502, 503})
            QCOMPARE(classify(s, "{}"), Verdict::RetryBackoff);
    }
    void controllerDownThenUp()
    {
        Controller c;
        QVERIFY(c.stub.listen());
        const quint16 port = c.stub.port();
        c.stub.close(); // down
        Rig r;
        writeSession(r.runtime, u"http://127.0.0.1:%1"_s.arg(port));
        r.add(u"n1"_s);
        r.fwd->kick();
        QTest::qWait(250);
        QCOMPARE(r.buf->count(), 1); // kept while the controller is down, never dropped
        QVERIFY(r.fwd->status().contains(u"unreachable"_s));
        QVERIFY(c.stub.listen(port));
        QTRY_COMPARE_WITH_TIMEOUT(r.buf->count(), 0, 3000);
        QCOMPARE(c.order, QStringList{u"n1"_s});
    }
    void lostResponseThenRetryStoresOnce()
    {
        Controller c;
        QVERIFY(c.stub.listen());
        int calls = 0;
        c.before = [&](const StubRequest &r) {
            if (++calls == 1) { // the controller stores it, then the answer is lost
                const QString id = QJsonDocument::fromJson(r.body).object()[u"id"_s].toString();
                c.rows.insert(id); c.order << id;
                StubReply d; d.drop = true; return d;
            }
            StubReply pass; pass.status = 0; return pass;
        };
        Rig r;
        writeSession(r.runtime, c.stub.url());
        r.add(u"n1"_s);
        r.fwd->kick();
        QTRY_COMPARE_WITH_TIMEOUT(r.buf->count(), 0, 3000);
        QVERIFY(c.stub.requests.size() >= 2);   // it was sent twice
        QCOMPARE(c.order.size(), 1);            // and stored once
    }
    void sentInOrderAndCarriesTheSession()
    {
        Controller c;
        QVERIFY(c.stub.listen());
        Rig r;
        writeSession(r.runtime, c.stub.url());
        for (const char *id : {"n1", "n2", "n3"})
            r.add(QString::fromLatin1(id));
        r.fwd->kick();
        QTRY_COMPARE_WITH_TIMEOUT(r.buf->count(), 0, 3000);
        QCOMPARE(c.order, (QStringList{u"n1"_s, u"n2"_s, u"n3"_s}));
        const StubRequest q = c.stub.requests.first();
        QCOMPARE(q.method, u"POST"_s);
        QCOMPARE(q.path, u"/api/notifications/os"_s);
        QCOMPARE(q.headers["user-agent"], kUa);
        QVERIFY(q.headers["cookie"].contains("taos_session=tok1"));
        QVERIFY(q.headers["cookie"].contains("csrf_token=csrf1"));
        QCOMPARE(q.headers["x-csrf-token"], QByteArray("csrf1"));
    }
    void waitsForTheSignInThenSends()
    {
        Controller c;
        QVERIFY(c.stub.listen());
        Rig r;
        r.add(u"n1"_s);
        r.fwd->kick();
        QTest::qWait(150);
        QCOMPARE(r.buf->count(), 1);
        QVERIFY(r.fwd->status().contains(u"sign-in"_s));
        QVERIFY(c.stub.requests.isEmpty());
        writeSession(r.runtime, c.stub.url()); // the person signs in later
        QTRY_COMPARE_WITH_TIMEOUT(r.buf->count(), 0, 3000);
    }
    void aSessionFileOthersCanReadIsNotTrusted()
    {
        Controller c;
        QVERIFY(c.stub.listen());
        Rig r;
        writeSession(r.runtime, c.stub.url());
        QFile::setPermissions(r.runtime + u"/taos/session"_s, QFileDevice::ReadOwner | QFileDevice::WriteOwner | QFileDevice::ReadOther);
        QVERIFY(!readCredentials(r.runtime).valid());
        QFile::setPermissions(r.runtime + u"/taos/session"_s, QFileDevice::ReadOwner | QFileDevice::WriteOwner);
        QVERIFY(readCredentials(r.runtime).valid());
    }
    void csrfRefusalIsReplayedOnceWithTheFreshToken()
    {
        Controller c;
        QVERIFY(c.stub.listen());
        c.before = [](const StubRequest &r) {
            if (r.headers.value("x-csrf-token") != "fresh") {
                StubReply d; d.status = 403; d.body = R"({"detail":"CSRF"})";
                d.headers << qMakePair(QByteArray("Set-Cookie"), QByteArray("csrf_token=fresh; Path=/"));
                return d;
            }
            StubReply pass; pass.status = 0; return pass;
        };
        Rig r;
        writeSession(r.runtime, c.stub.url());
        r.add(u"n1"_s);
        r.fwd->kick();
        QTRY_COMPARE_WITH_TIMEOUT(r.buf->count(), 0, 3000);
        QCOMPARE(c.order, QStringList{u"n1"_s});
    }
    void refusedRecordIsKeptWarnedAndDoesNotBlockTheQueue()
    {
        Controller c;
        QVERIFY(c.stub.listen());
        c.before = [](const StubRequest &r) {
            if (QJsonDocument::fromJson(r.body).object()[u"id"_s].toString() == u"bad"_s) {
                StubReply d; d.status = 422; d.body = "{}"; return d;
            }
            StubReply pass; pass.status = 0; return pass;
        };
        Rig r;
        writeSession(r.runtime, c.stub.url());
        r.add(u"bad"_s);
        r.add(u"good"_s);
        r.fwd->kick();
        QTRY_COMPARE_WITH_TIMEOUT(r.buf->count(), 0, 3000);
        QCOMPARE(r.fwd->quarantined(), 1);
        QCOMPARE(QDir(r.dir.path() + u"/buf/rejected"_s).entryList({u"*.json"_s}).size(), 1);
        QVERIFY(c.rows.contains(u"good"_s));
        bool warned = false;
        for (const QString &id : c.rows) // the warning record went through the buffer to the controller
            warned = warned || id != u"good"_s;
        QVERIFY2(warned, "a visible warning record must reach the controller");
    }
    void aRefusedWarningDoesNotRaiseAnotherWarning()
    {
        Controller c;
        QVERIFY(c.stub.listen());
        c.before = [](const StubRequest &) { StubReply d; d.status = 413; d.body = "{}"; return d; };
        Rig r;
        writeSession(r.runtime, c.stub.url());
        r.add(u"w"_s, u"taos.notifyd.dropped"_s);
        r.fwd->kick();
        QTRY_COMPARE_WITH_TIMEOUT(r.buf->count(), 0, 3000);
        QCOMPARE(r.fwd->quarantined(), 1);
        QCOMPARE(c.stub.requests.size(), 1);
    }
    void rateLimitHonoursRetryAfter()
    {
        Controller c;
        QVERIFY(c.stub.listen());
        int calls = 0;
        QElapsedTimer t;
        c.before = [&](const StubRequest &) {
            if (++calls == 1) {
                t.start();
                StubReply d; d.status = 429; d.body = "{}";
                d.headers << qMakePair(QByteArray("Retry-After"), QByteArray("1"));
                return d;
            }
            StubReply pass; pass.status = 0; return pass;
        };
        Rig r;
        writeSession(r.runtime, c.stub.url());
        r.add(u"n1"_s);
        r.fwd->kick();
        QTRY_COMPARE_WITH_TIMEOUT(r.buf->count(), 0, 4000);
        QVERIFY2(t.elapsed() >= 950, "the retry came before Retry-After");
    }
    void aWrongSessionKeepsTheRecordUntilANewSignIn()
    {
        Controller c;
        QVERIFY(c.stub.listen());
        c.before = [](const StubRequest &r) {
            if (!r.headers.value("cookie").contains("taos_session=good")) {
                StubReply d; d.status = 401; d.body = "{}"; return d;
            }
            StubReply pass; pass.status = 0; return pass;
        };
        Rig r;
        writeSession(r.runtime, c.stub.url(), u"stale"_s);
        r.add(u"n1"_s);
        r.fwd->kick();
        QTest::qWait(200);
        QCOMPARE(r.buf->count(), 1);
        writeSession(r.runtime, c.stub.url(), u"good"_s);
        QTRY_COMPARE_WITH_TIMEOUT(r.buf->count(), 0, 3000);
    }
    void aTimeoutKeepsTheRecord()
    {
        Controller c;
        QVERIFY(c.stub.listen());
        bool hang = true;
        c.before = [&](const StubRequest &) {
            StubReply d;
            if (hang) { d.hang = true; return d; }
            d.status = 0; return d;
        };
        Rig r;
        writeSession(r.runtime, c.stub.url());
        r.add(u"n1"_s);
        r.fwd->kick();
        QTest::qWait(700);
        QCOMPARE(r.buf->count(), 1);
        hang = false;
        QTRY_COMPARE_WITH_TIMEOUT(r.buf->count(), 0, 4000);
    }
};

QTEST_MAIN(TestForwarder)
#include "test_forwarder.moc"
