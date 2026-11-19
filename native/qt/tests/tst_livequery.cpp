#include <QSignalSpy>
#include <QtTest>

#include "fakecontroller.h"
#include "taosqt/livequery.h"
#include "taosqt/session.h"

using namespace Qt::StringLiterals;
using namespace Taos;

class TstLiveQuery : public QObject
{
    Q_OBJECT
    FakeController fake;

private Q_SLOTS:
    void initTestCase() { QVERIFY2(fake.ok(), "python3 fake controller did not start"); }
    void init()
    {
        fake.reset();
        fake.config({{u"heartbeat_ms"_s, 40}});
    }

    void listsAndStaysCurrent()
    {
        Session s(fastOptions(fake.url()));
        s.signIn(u"jay"_s, FAKE_PASSWORD);
        QTRY_COMPARE(s.state(), Session::SignedIn);

        LiveQuery q(s.client(), u"notifications.list"_s, {}, {u"notification.added"_s},
                    {u"id"_s, u"title"_s, u"read"_s});
        QSignalSpy loaded(&q, &LiveQuery::loaded);
        q.start();
        QTRY_COMPARE(q.model()->rowCount(), 3);
        QTRY_VERIFY(q.live());
        QTRY_VERIFY(!q.loading());

        // A new notification on the controller shows up without a refresh call.
        fake.config({{u"add_notification"_s, QJsonObject{{u"title"_s, u"Fresh"_s}}}});
        QTRY_COMPARE(q.model()->rowCount(), 4);
        QCOMPARE(q.model()->get(0).value(u"title"_s).toString(), u"Fresh"_s);

        // A drop of the live connection marks the data possibly stale, then catches up.
        fake.config({{u"drop_sse"_s, true}});
        fake.config({{u"add_notification"_s, QJsonObject{{u"title"_s, u"While away"_s}}}});
        QTRY_COMPARE(q.model()->rowCount(), 5);
        QTRY_VERIFY(q.live());
    }

    void errorsAreShownAndClearedOnTheNextGoodFetch()
    {
        Session s(fastOptions(fake.url()));
        s.signIn(u"jay"_s, FAKE_PASSWORD);
        QTRY_COMPARE(s.state(), Session::SignedIn);
        fake.config({{u"fail"_s, QJsonObject{{u"path"_s, u"/api/notifications"_s}, {u"status"_s, 500},
                                            {u"message"_s, u"boom"_s}, {u"times"_s, 1}}}});
        LiveQuery q(s.client(), u"notifications.list"_s, {}, {}, {u"id"_s});
        QSignalSpy failed(&q, &LiveQuery::failed);
        q.start();
        QTRY_COMPARE(failed.size(), 1);
        QVERIFY(q.errorText().contains(u"boom"_s));
        q.refresh();
        QTRY_COMPARE(q.model()->rowCount(), 3);
        QVERIFY(q.errorText().isEmpty());
    }

    void destroyingItClosesTheStream()
    {
        Session s(fastOptions(fake.url()));
        s.signIn(u"jay"_s, FAKE_PASSWORD);
        QTRY_COMPARE(s.state(), Session::SignedIn);
        {
            LiveQuery q(s.client(), u"notifications.list"_s, {}, {}, {u"id"_s});
            q.start();
            QTRY_COMPARE(fake.stat("sse_open"), 1);
        }
        QTRY_COMPARE_WITH_TIMEOUT(fake.stat("sse_open"), 0, 5000);
    }

    void anEventBetweenTheFirstFetchAndTheStreamGoingLiveIsNotLost()
    {
        Session s(fastOptions(fake.url()));
        s.signIn(u"jay"_s, FAKE_PASSWORD);
        QTRY_COMPARE(s.state(), Session::SignedIn);
        // The first answer is a snapshot taken now but delivered late; the event
        // stream is accepted even later. The notification added in between is
        // in neither the snapshot nor the stream.
        fake.config({{u"snapshot_delay_ms"_s, 400}, {u"sse_delay_ms"_s, 800}});
        LiveQuery q(s.client(), u"notifications.list"_s, {}, {u"notification.added"_s}, {u"id"_s, u"title"_s});
        q.start();
        QTest::qWait(150);
        fake.config({{u"add_notification"_s, QJsonObject{{u"title"_s, u"In the gap"_s}}}});
        QTRY_COMPARE_WITH_TIMEOUT(q.model()->rowCount(), 3, 3000); // the stale snapshot
        fake.config({{u"snapshot_delay_ms"_s, 0}});
        QTRY_VERIFY_WITH_TIMEOUT(q.live(), 5000);
        QTRY_COMPARE_WITH_TIMEOUT(q.model()->rowCount(), 4, 3000); // refetched on connected
        QCOMPARE(q.model()->get(0).value(u"title"_s).toString(), u"In the gap"_s);
    }

    void severalListsShareOneConnection()
    {
        Session s(fastOptions(fake.url()));
        s.signIn(u"jay"_s, FAKE_PASSWORD);
        QTRY_COMPARE(s.state(), Session::SignedIn);
        auto *a = new LiveQuery(s.client(), u"notifications.list"_s, {}, {u"notification.added"_s}, {u"id"_s});
        auto *b = new LiveQuery(s.client(), u"decisions.list"_s, {}, {u"decision.answered"_s}, {u"id"_s});
        a->start();
        b->start();
        QTRY_VERIFY(a->live() && b->live());
        QCOMPARE(fake.stat("sse_total"), 1);
        QCOMPARE(fake.stat("sse_open"), 1);
        QTRY_VERIFY(b->model()->rowCount() > 0);

        // Each list reacts only to its own kinds.
        const int reqBefore = fake.stat("requests");
        fake.config({{u"push"_s, QJsonObject{{u"kind"_s, u"decision.answered"_s}}}});
        QTRY_VERIFY(fake.stat("requests") > reqBefore);
        QCOMPARE(a->model()->rowCount(), 3);

        delete a; // the other still uses the stream
        QTest::qWait(150);
        QCOMPARE(fake.stat("sse_open"), 1);
        delete b; // the last one closes it
        QTRY_COMPARE_WITH_TIMEOUT(fake.stat("sse_open"), 0, 5000);
    }

    void configuredLikeQmlThroughPropertiesAndTheDefaultClient()
    {
        Session s(fastOptions(fake.url()));
        s.signIn(u"jay"_s, FAKE_PASSWORD);
        QTRY_COMPARE(s.state(), Session::SignedIn);
        setDefaultClient(s.client());
        LiveQuery q; // what a QML `LiveQuery { ... }` creates
        q.setOp(u"notifications.list"_s);
        q.setRoles({u"id"_s, u"title"_s});
        q.setEventKinds({u"notification.added"_s});
        q.setRunning(true); // applied on the next event-loop turn, after all properties
        QTRY_COMPARE(q.model()->rowCount(), 3);
        QCOMPARE(q.model()->roleNames().size(), 3); // item + 2 roles
        q.setRunning(false);
        QTRY_COMPARE_WITH_TIMEOUT(fake.stat("sse_open"), 0, 5000);
        setDefaultClient(nullptr);
    }

    void signInAfterAnUnauthenticatedStartFillsTheList()
    {
        Session s(fastOptions(fake.url()));
        LiveQuery q(s.client(), u"notifications.list"_s, {}, {}, {u"id"_s});
        q.start();
        QTRY_VERIFY(!q.errorText().isEmpty());
        s.signIn(u"jay"_s, FAKE_PASSWORD);
        QTRY_COMPARE(q.model()->rowCount(), 3);
        QTRY_VERIFY(q.live());
    }
};

QTEST_MAIN(TstLiveQuery)
#include "tst_livequery.moc"
