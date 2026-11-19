#include <QCoreApplication>
#include <QDir>
#include <QSignalSpy>
#include <QThread>
#include <QtTest>

#include "fakecontroller.h"
#include "taosqt/client.h"
#include "taosqt/eventstream.h"

using namespace Qt::StringLiterals;
using namespace Taos;

namespace {
bool signIn(Client &c)
{
    QSignalSpy spy(c.call(u"auth.login"_s, {{u"username"_s, u"jay"_s}, {u"password"_s, FAKE_PASSWORD}}), &Reply::finished);
    return spy.wait(8000) && qvariant_cast<Result>(spy.first().first()).ok();
}
} // namespace

class TstStream : public QObject
{
    Q_OBJECT
    FakeController fake;

    void fastHeartbeat() { fake.config({{u"heartbeat_ms"_s, 40}}); }

private Q_SLOTS:
    void initTestCase() { QVERIFY2(fake.ok(), "python3 fake controller did not start"); }
    void init()
    {
        fake.reset();
        fastHeartbeat();
    }

    void eventsAndHeartbeats()
    {
        Client c(fastOptions(fake.url()));
        QVERIFY(signIn(c));
        EventStream *s = c.osEvents({u"notification.added"_s});
        QCOMPARE(s->state(), EventStream::Idle);
        QSignalSpy ev(s, &EventStream::eventReceived);
        s->start();
        QCOMPARE(s->state(), EventStream::Connecting);
        QTRY_COMPARE(s->state(), EventStream::Live);
        QCOMPARE(c.connectionState(), Client::Online);
        QCOMPARE(fake.stat("sse_open"), 1);
        QTest::qWait(150); // several heartbeats go by
        QCOMPARE(ev.size(), 0); // and are not delivered as events

        fake.config({{u"push"_s, QJsonObject{{u"kind"_s, u"notification.added"_s}, {u"id"_s, u"n1"_s}}}});
        QTRY_COMPARE(ev.size(), 1);
        const QJsonObject d = ev.first().at(2).toJsonValue().toObject();
        QCOMPARE(d.value(u"kind"_s).toString(), u"notification.added"_s);
        QCOMPARE(d.value(u"id"_s).toString(), u"n1"_s);
    }

    void reconnectsAfterTheServerDropsTheConnection()
    {
        Client c(fastOptions(fake.url()));
        QVERIFY(signIn(c));
        EventStream *s = c.osEvents();
        QSignalSpy states(s, &EventStream::stateChanged);
        QSignalSpy conn(&c, &Client::connectionStateChanged);
        QSignalSpy ev(s, &EventStream::eventReceived);
        s->start();
        QTRY_COMPARE(s->state(), EventStream::Live);

        fake.config({{u"drop_sse"_s, true}});
        QTRY_VERIFY(states.contains(QList<QVariant>{QVariant::fromValue(EventStream::Reconnecting)}));
        QCOMPARE(s->lastError().kind(), Error::Network);
        QTRY_COMPARE(s->state(), EventStream::Live); // backoff 20 ms, so back quickly
        QTRY_COMPARE(fake.stat("sse_total"), 2);
        QCOMPARE(fake.stat("sse_open"), 1);
        QVERIFY(conn.size() >= 2); // Online -> Reconnecting -> Online
        QCOMPARE(c.connectionState(), Client::Online);

        // Events flow again on the new connection.
        fake.config({{u"push"_s, QJsonObject{{u"kind"_s, u"decision.answered"_s}}}});
        QTRY_COMPARE(ev.size(), 1);

        // Repeated drops keep recovering.
        for (int i = 0; i < 3; ++i) {
            fake.config({{u"drop_sse"_s, true}});
            QTRY_COMPARE_WITH_TIMEOUT(s->state(), EventStream::Reconnecting, 3000);
            QTRY_COMPARE_WITH_TIMEOUT(s->state(), EventStream::Live, 3000);
        }
    }

    void serverDownThenUpAgain()
    {
        // Point at a port that is closed: the stream reports Reconnecting and keeps trying.
        Client c(fastOptions(QUrl(u"http://127.0.0.1:9"_s)));
        EventStream *s = c.osEvents();
        s->start();
        QTRY_COMPARE(s->state(), EventStream::Reconnecting);
        QCOMPARE(c.connectionState(), Client::Reconnecting);
        QTest::qWait(300);
        QCOMPARE(s->state(), EventStream::Reconnecting); // still retrying, not given up
    }

    void destroyingTheStreamClosesTheConnectionAndSilencesIt()
    {
        Client c(fastOptions(fake.url()));
        QVERIFY(signIn(c));
        auto *s = c.osEvents();
        auto *probe = new QObject;
        int received = 0;
        connect(s, &EventStream::eventReceived, probe, [&] { ++received; });
        s->start();
        QTRY_COMPARE(s->state(), EventStream::Live);
        QCOMPARE(fake.stat("sse_open"), 1);

        delete s;
        // A filter alone would leave the socket open: the server must see it close.
        QTRY_COMPARE_WITH_TIMEOUT(fake.stat("sse_open"), 0, 5000);
        const int before = received;
        fake.config({{u"push"_s, QJsonObject{{u"kind"_s, u"x"_s}}}, {u"burst"_s, 20}});
        QTest::qWait(300);
        QCOMPARE(received, before);
        delete probe;
    }

    void destroyingTheClientClosesItsStreams()
    {
        {
            Client c(fastOptions(fake.url()));
            QVERIFY(signIn(c));
            for (int i = 0; i < 3; ++i)
                c.osEvents()->start();
            QTRY_COMPARE(fake.stat("sse_open"), 3);
        }
        QTRY_COMPARE_WITH_TIMEOUT(fake.stat("sse_open"), 0, 5000);
    }

    void stopAndStartAgain()
    {
        Client c(fastOptions(fake.url()));
        QVERIFY(signIn(c));
        auto *s = c.osEvents();
        s->start();
        QTRY_COMPARE(s->state(), EventStream::Live);
        s->stop();
        QCOMPARE(s->state(), EventStream::Idle);
        QTRY_COMPARE(fake.stat("sse_open"), 0);
        s->start();
        QTRY_COMPARE(s->state(), EventStream::Live);
        QCOMPARE(fake.stat("sse_open"), 1);
    }

    void unauthenticatedStreamWaitsForSignInThenRestarts()
    {
        Client c(fastOptions(fake.url()));
        QSignalSpy unauth(&c, &Client::unauthenticated);
        auto *s1 = c.osEvents();
        auto *s2 = c.osEvents();
        s1->start();
        s2->start();
        QTRY_COMPARE(s1->state(), EventStream::AuthRequired);
        QTRY_COMPARE(s2->state(), EventStream::AuthRequired);
        QCOMPARE(unauth.size(), 1); // one prompt, not one per stream
        QCOMPARE(fake.stat("sse_open"), 0);

        QVERIFY(signIn(c));
        QTRY_COMPARE(s1->state(), EventStream::Live);
        QTRY_COMPARE(s2->state(), EventStream::Live);
        QCOMPARE(fake.stat("sse_open"), 2);
    }

    void burstIsDeliveredCompleteInOrderOnTheGuiThread()
    {
        Client c(fastOptions(fake.url()));
        QVERIFY(signIn(c));
        auto *s = c.osEvents();
        QStringList ids;
        int wrongThread = 0;
        connect(s, &EventStream::eventReceived, this, [&](const QString &, const QString &, const QJsonValue &d) {
            if (QThread::currentThread() != QCoreApplication::instance()->thread())
                ++wrongThread;
            ids << d.toObject().value(u"id"_s).toString();
        });
        s->start();
        QTRY_COMPARE(s->state(), EventStream::Live);
        constexpr int N = 3000;
        fake.config({{u"burst"_s, N}});
        QTRY_COMPARE_WITH_TIMEOUT(int(ids.size()), N, 20000);
        QCOMPARE(wrongThread, 0);
        for (int i = 0; i < N; ++i)
            QCOMPARE(ids.at(i), u"b%1"_s.arg(i));
    }

    void theTransportAddsAFixedSmallNumberOfThreads()
    {
        auto threads = [] { return QDir(u"/proc/self/task"_s).entryList(QDir::Dirs | QDir::NoDotAndDotDot).size(); };
        const auto before = threads();
        Client c(fastOptions(fake.url()));
        QVERIFY(signIn(c));
        QList<EventStream *> streams;
        for (int i = 0; i < 5; ++i) {
            streams << c.osEvents();
            streams.last()->start();
        }
        for (int i = 0; i < 30; ++i)
            c.call(u"notifications.list"_s);
        QTRY_COMPARE(streams.last()->state(), EventStream::Live);
        QTest::qWait(200);
        const auto after = threads();
        qInfo("threads before=%lld after=%lld (5 streams, 30 requests)", qint64(before), qint64(after));
        QVERIFY2(after - before <= 4, "the transport must not grow a thread per stream or request");
    }

    void destroyWhileEventsAreFlowing()
    {
        Client seed(fastOptions(fake.url()));
        QVERIFY(signIn(seed));
        const QByteArray saved = seed.exportSession();
        for (int round = 0; round < 15; ++round) {
            ClientOptions o = fastOptions(fake.url());
            o.savedSession = saved;
            auto *c = new Client(o);
            auto *s = c->osEvents();
            int n = 0;
            connect(s, &EventStream::eventReceived, c, [&] { ++n; });
            s->start();
            QTRY_COMPARE_WITH_TIMEOUT(s->state(), EventStream::Live, 5000);
            fake.config({{u"burst"_s, 500}});
            QTRY_VERIFY_WITH_TIMEOUT(n > 0, 5000);
            delete c; // mid-burst: callbacks on transport threads are in flight
            QCoreApplication::processEvents();
        }
        QTRY_COMPARE_WITH_TIMEOUT(fake.stat("sse_open"), 0, 5000);
    }
};

QTEST_MAIN(TstStream)
#include "tst_stream.moc"
