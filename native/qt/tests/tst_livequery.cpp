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
        QVERIFY(!q.loading());

        // A new notification on the controller shows up without a refresh call.
        fake.config({{u"add_notification"_s, QJsonObject{{u"title"_s, u"Fresh"_s}}}});
        QTRY_COMPARE(q.model()->rowCount(), 4);
        QCOMPARE(q.model()->get(0).value(u"title"_s).toString(), u"Fresh"_s);

        // A drop of the live connection marks the data possibly stale, then catches up.
        fake.config({{u"drop_sse"_s, true}});
        QTRY_VERIFY(!q.live());
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
