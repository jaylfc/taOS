// SPDX-License-Identifier: AGPL-3.0-or-later
#include <QCoreApplication>
#include <QSignalSpy>
#include <QThread>
#include <QtTest>

#include "fakecontroller.h"
#include "taosqt/api.h"
#include "taosqt/client.h"

using namespace Qt::StringLiterals;
using namespace Taos;

namespace {
Result waitFor(Reply *r)
{
    QSignalSpy spy(r, &Reply::finished);
    if (!spy.wait(8000))
        return Result{QJsonValue(), Error(Error::Internal, u"test timeout"_s)};
    return qvariant_cast<Result>(spy.first().first());
}
Result signIn(Client &c)
{
    return waitFor(c.call(u"auth.login"_s, {{u"username"_s, u"alex"_s}, {u"password"_s, FAKE_PASSWORD}}));
}
} // namespace

class TstClient : public QObject
{
    Q_OBJECT
    FakeController fake;

private Q_SLOTS:
    void initTestCase() { QVERIFY2(fake.ok(), "python3 fake controller did not start"); }
    void init() { fake.reset(); }

    void signInAndList()
    {
        Client c(fastOptions(fake.url()));
        QCOMPARE(c.connectionState(), Client::Unknown);
        QVERIFY(!c.hasCredentials());
        const Result login = signIn(c);
        QVERIFY2(login.ok(), qPrintable(login.error.userMessage()));
        QCOMPARE(login.value.toObject().value(u"user"_s).toObject().value(u"username"_s).toString(), u"alex"_s);
        QVERIFY(c.hasCredentials());
        QCOMPARE(c.connectionState(), Client::Online);

        const Result list = waitFor(Notifications(&c).list());
        QVERIFY(list.ok());
        QCOMPARE(list.value.toArray().size(), 3);
        QCOMPARE(list.value.toArray().first().toObject().value(u"title"_s).toString(), u"Access request"_s);
        // A POST needs the CSRF header; the transport adds it.
        QVERIFY(waitFor(Notifications(&c).markRead(12)).ok());
        const Result arch = waitFor(Notifications(&c).archive(12));
        QCOMPARE(arch.value.toObject().value(u"outcome"_s).toString(), u"kept_pending_request"_s);
        QVERIFY(waitFor(Dashboard(&c).snapshot()).value.toObject().contains(u"activity"_s));
        QVERIFY(waitFor(Models(&c).catalog()).ok());
    }

    void unauthenticatedIsOneCoalescedSignal()
    {
        Client c(fastOptions(fake.url()));
        QSignalSpy unauth(&c, &Client::unauthenticated);
        int finishedCount = 0;
        for (int i = 0; i < 5; ++i)
            c.call(u"notifications.list"_s)->then(this, [&](const Result &r) {
                QCOMPARE(r.error.kind(), Error::Unauthenticated);
                ++finishedCount;
            });
        QTRY_COMPARE(finishedCount, 5);
        QCOMPARE(unauth.size(), 1); // not five

        // A wrong password is reported by its own reply, not by the signal.
        const Result bad = waitFor(c.call(u"auth.login"_s, {{u"username"_s, u"alex"_s}, {u"password"_s, u"nope"_s}}));
        QCOMPARE(bad.error.kind(), Error::Unauthenticated);
        QCOMPARE(bad.error.message(), u"Invalid username or password"_s);
        QCOMPARE(unauth.size(), 1);

        // After signing in, a new rejection signals again.
        QVERIFY(signIn(c).ok());
        fake.config({{u"expire"_s, true}});
        QCOMPARE(waitFor(c.call(u"notifications.list"_s)).error.kind(), Error::Unauthenticated);
        QCOMPARE(unauth.size(), 2);
    }

    void errorMapping()
    {
        Client c(fastOptions(fake.url()));
        QVERIFY(signIn(c).ok());
        Result r = waitFor(Decisions(&c).get(u"nope"_s));
        QCOMPARE(r.error.kind(), Error::NotFound);
        QCOMPARE(r.error.httpStatus(), 404);
        QCOMPARE(r.error.userMessage(), u"decision not found"_s);

        QVERIFY(waitFor(Decisions(&c).answer(u"dec_1"_s, u"pg"_s)).ok());
        r = waitFor(Decisions(&c).answer(u"dec_1"_s, u"pg"_s));
        QCOMPARE(r.error.kind(), Error::Conflict);
        QVERIFY(r.error.userMessage().contains(u"already answered"_s));

        fake.config({{u"fail"_s, QJsonObject{{u"path"_s, u"/api/activity"_s}, {u"status"_s, 500},
                                            {u"message"_s, u"disk on fire"_s}, {u"times"_s, 1}}}});
        r = waitFor(Dashboard(&c).activity());
        QCOMPARE(r.error.kind(), Error::Server);
        QVERIFY(r.error.isRetryable());
        QVERIFY(r.error.userMessage().contains(u"disk on fire"_s));
        QCOMPARE(c.connectionState(), Client::Online); // a 5xx is an answer

        fake.config({{u"fail"_s, QJsonObject{{u"path"_s, u"/api/models"_s}, {u"status"_s, 429},
                                            {u"message"_s, u"slow down"_s}, {u"times"_s, 1}}}});
        r = waitFor(Models(&c).catalog());
        QCOMPARE(r.error.kind(), Error::RateLimited);

        r = waitFor(c.call(u"bogus.op"_s));
        QCOMPARE(r.error.kind(), Error::Internal);
    }

    void networkDownIsReconnectingState()
    {
        Client c(fastOptions(QUrl(u"http://127.0.0.1:9"_s)));
        QSignalSpy st(&c, &Client::connectionStateChanged);
        const Result r = waitFor(c.call(u"auth.me"_s));
        QCOMPARE(r.error.kind(), Error::Network);
        QVERIFY(r.error.isRetryable());
        QCOMPARE(r.error.userMessage(), u"Can't reach the controller. Retrying."_s);
        QCOMPARE(c.connectionState(), Client::Reconnecting);
        QCOMPARE(st.size(), 1);
    }

    void slowResponsesAndTimeout()
    {
        Client c(fastOptions(fake.url()));
        QVERIFY(signIn(c).ok());
        fake.config({{u"delay_ms"_s, 400}, {u"delay_path"_s, u"/api/notifications"_s}});
        QElapsedTimer t;
        t.start();
        QVERIFY(waitFor(Notifications(&c).list()).ok());
        QVERIFY(t.elapsed() >= 380);

        ClientOptions o = fastOptions(fake.url());
        o.requestTimeoutMs = 100;
        Client impatient(o);
        const Result r = waitFor(impatient.call(u"notifications.list"_s));
        QCOMPARE(r.error.kind(), Error::Network); // timed out: same as unreachable
    }

    void abortAndDestroyDeliverNothing()
    {
        Client c(fastOptions(fake.url()));
        QVERIFY(signIn(c).ok());
        fake.config({{u"delay_ms"_s, 300}, {u"delay_path"_s, u"/api/notifications"_s}});

        Reply *a = Notifications(&c).list();
        QSignalSpy spy(a, &Reply::finished);
        a->abort();
        QCOMPARE(spy.size(), 1);
        QCOMPARE(qvariant_cast<Result>(spy.first().first()).error.kind(), Error::Cancelled);

        // Deleting the context before the answer: the callback never runs.
        bool called = false;
        auto *ctx = new QObject;
        Notifications(&c).list()->then(ctx, [&](const Result &) { called = true; });
        delete ctx;
        // Deleting the reply itself: nothing is delivered either.
        Reply *b = Notifications(&c).list();
        QSignalSpy spyB(b, &Reply::finished);
        delete b;
        QTest::qWait(700);
        QVERIFY(!called);
        QCOMPARE(spyB.size(), 0);
        // And the client is still healthy.
        fake.config({{u"delay_ms"_s, 0}});
        QVERIFY(waitFor(Notifications(&c).list()).ok());
    }

    void destroyingClientWithRequestsInFlight()
    {
        fake.config({{u"delay_ms"_s, 200}, {u"delay_path"_s, u"/auth/me"_s}});
        for (int i = 0; i < 10; ++i) {
            auto *c = new Client(fastOptions(fake.url()));
            for (int j = 0; j < 5; ++j)
                c->call(u"auth.me"_s);
            delete c;
        }
        QTest::qWait(500); // late answers must find nothing to deliver to
    }

    void restoreSessionAndBearer()
    {
        Client a(fastOptions(fake.url()));
        QVERIFY(signIn(a).ok());
        const QByteArray saved = a.exportSession();
        QVERIFY(!saved.isEmpty());

        Client b(fastOptions(fake.url()));
        QSignalSpy cred(&b, &Client::credentialsChanged);
        fake.config({{u"delay_ms"_s, 300}, {u"delay_path"_s, u"/auth/me"_s}});
        Reply *pending = b.call(u"auth.me"_s);
        QSignalSpy pendingDone(pending, &Reply::finished);
        QVERIFY(b.restoreSession(saved));
        QCOMPARE(pendingDone.size(), 1); // pending work on the old transport is cancelled
        QCOMPARE(qvariant_cast<Result>(pendingDone.first().first()).error.kind(), Error::Cancelled);
        QVERIFY(cred.size() >= 1);
        fake.config({{u"delay_ms"_s, 0}});
        QVERIFY(waitFor(b.call(u"auth.me"_s)).ok());

        Client t(fastOptions(fake.url()));
        t.setBearerToken(u"local-token-123"_s);
        QVERIFY(t.hasCredentials());
        QVERIFY(waitFor(Notifications(&t).list()).ok());
        t.setBearerToken(u"wrong"_s);
        QCOMPARE(waitFor(Notifications(&t).list()).error.kind(), Error::Unauthenticated);
        t.forgetCredentials();
        QVERIFY(!t.hasCredentials());
    }

    void callbacksAreDeliveredOnTheClientThread()
    {
        Client c(fastOptions(fake.url()));
        QVERIFY(signIn(c).ok());
        constexpr int N = 200;
        int done = 0, wrongThread = 0;
        for (int i = 0; i < N; ++i)
            c.call(u"notifications.list"_s)->then(this, [&](const Result &r) {
                if (QThread::currentThread() != QCoreApplication::instance()->thread())
                    ++wrongThread;
                QVERIFY(r.ok());
                ++done;
            });
        QTRY_COMPARE_WITH_TIMEOUT(done, N, 20000);
        QCOMPARE(wrongThread, 0);
    }

    void clientCreatedAndUsedInAWorkerThread()
    {
        // A Client lives in the thread that created it and delivers there.
        QThread th;
        th.start();
        int done = 0;
        bool onWorker = false;
        auto *c = new Client(fastOptions(fake.url()));
        c->moveToThread(&th);
        QMetaObject::invokeMethod(c, [&] {
            c->call(u"auth.login"_s, {{u"username"_s, u"alex"_s}, {u"password"_s, FAKE_PASSWORD}})
                ->then(c, [&](const Result &r) {
                    onWorker = QThread::currentThread() == &th;
                    done = r.ok() ? 1 : -1;
                });
        });
        QTRY_COMPARE(done, 1);
        QVERIFY(onWorker);
        QMetaObject::invokeMethod(c, [c] { delete c; }, Qt::BlockingQueuedConnection);
        th.quit();
        th.wait();
    }
};

QTEST_MAIN(TstClient)
#include "tst_client.moc"
