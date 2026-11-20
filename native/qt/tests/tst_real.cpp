// Runs against a REAL controller. Skipped unless TAOS_REAL_URL is set:
//   TAOS_REAL_URL=http://127.0.0.1:6977 TAOS_REAL_USER=u TAOS_REAL_PASSWORD=p
//   [TAOS_REAL_LOCAL_TOKEN=<contents of .auth_local_token>] tst_real
// Creates and archives its own notifications; use a scratch controller.
#include <QSignalSpy>
#include <QTemporaryDir>
#include <QtTest>

#include "taosqt/api.h"
#include "taosqt/desktopsession.h"
#include "taosqt/livequery.h"
#include "taosqt/session.h"

using namespace Qt::StringLiterals;
using namespace Taos;

namespace {
ClientOptions opts()
{
    ClientOptions o;
    o.baseUrl = QUrl(qEnvironmentVariable("TAOS_REAL_URL"));
    return o;
}
Result wait(Reply *r)
{
    QSignalSpy spy(r, &Reply::finished);
    if (!spy.wait(15000))
        return Result{QJsonValue(), Error(Error::Internal, u"test timeout"_s)};
    return qvariant_cast<Result>(spy.first().first());
}
QString user() { return qEnvironmentVariable("TAOS_REAL_USER"); }
QString pass() { return qEnvironmentVariable("TAOS_REAL_PASSWORD"); }
Result makeNotification(Client *c, const QString &title)
{
    return wait(c->http(u"POST"_s, u"/api/notifications"_s, {},
                        QJsonObject{{u"title"_s, title}, {u"message"_s, u"from tst_real"_s}, {u"level"_s, u"info"_s}}));
}
} // namespace

class TstReal : public QObject
{
    Q_OBJECT
private Q_SLOTS:
    void initTestCase()
    {
        if (qEnvironmentVariableIsEmpty("TAOS_REAL_URL"))
            QSKIP("TAOS_REAL_URL not set");
    }

    void signInListMarkArchive()
    {
        Session s(opts());
        QSignalSpy bad(&s, &Session::signInFailed);
        s.signIn(user(), u"definitely-wrong-password"_s);
        QTRY_COMPARE_WITH_TIMEOUT(bad.size(), 1, 15000);
        const Error e = qvariant_cast<Error>(bad.first().first());
        qInfo() << "wrong password ->" << e.kind() << e.httpStatus() << e.userMessage();
        QCOMPARE(e.kind(), Error::Unauthenticated);

        s.signIn(user(), pass());
        QTRY_COMPARE_WITH_TIMEOUT(s.state(), Session::SignedIn, 15000);
        QCOMPARE(s.userName(), user());
        QVERIFY(s.client()->hasCredentials());

        const Result made = makeNotification(s.client(), u"tst_real one"_s);
        QVERIFY2(made.ok(), qPrintable(made.error.userMessage()));
        const Result list = wait(Notifications(s.client()).list());
        QVERIFY(list.ok());
        qint64 id = -1;
        for (const QJsonValue &v : list.value.toArray())
            if (v.toObject().value(u"title"_s).toString() == u"tst_real one"_s)
                id = qint64(v.toObject().value(u"id"_s).toDouble());
        QVERIFY2(id >= 0, "created notification is in the list");
        QVERIFY(wait(Notifications(s.client()).markRead(id)).ok());
        const Result arch = wait(Notifications(s.client()).archive(id));
        QCOMPARE(arch.value.toObject().value(u"outcome"_s).toString(), u"archived"_s);

        const Result nf = wait(Decisions(s.client()).get(u"no-such-decision"_s));
        qInfo() << "unknown decision ->" << nf.error.kind() << nf.error.userMessage();
        QCOMPARE(nf.error.kind(), Error::NotFound);
        QVERIFY(wait(Dashboard(s.client()).activity()).ok());
        QVERIFY(wait(Models(s.client()).catalog()).ok());
        s.signOut();
    }

    void liveListSeesANotificationCreatedOnTheController()
    {
        Session s(opts());
        s.signIn(user(), pass());
        QTRY_COMPARE_WITH_TIMEOUT(s.state(), Session::SignedIn, 15000);
        LiveQuery q(s.client(), u"notifications.list"_s, {}, {u"notification.added"_s},
                    {u"id"_s, u"title"_s});
        q.start();
        QTRY_VERIFY_WITH_TIMEOUT(q.live(), 15000); // connected: response headers arrived
        QTRY_VERIFY_WITH_TIMEOUT(!q.loading(), 15000);
        const int before = q.model()->rowCount();
        QVERIFY(makeNotification(s.client(), u"tst_real live"_s).ok());
        QTRY_VERIFY_WITH_TIMEOUT(q.model()->rowCount() > before, 15000);
        QCOMPARE(q.model()->get(0).value(u"title"_s).toString(), u"tst_real live"_s);
        qInfo() << "live update arrived; stream state" << s.client()->sharedOsEvents()->state();
        for (int i = 0; i < q.model()->rowCount(); ++i) // clean up
            if (q.model()->get(i).value(u"title"_s).toString().startsWith(u"tst_real"_s))
                wait(Notifications(s.client()).archive(qint64(q.model()->get(i).value(u"id"_s).toDouble())));
    }

    void desktopSessionIsPickedUpByASecondApp()
    {
        QTemporaryDir run;
        Session a(opts());
        a.setTokenSource(new DesktopSessionTokenSource(run.path()));
        QSignalSpy needA(&a, &Session::signInRequired);
        a.start();
        QCOMPARE(needA.size(), 1);
        a.signIn(user(), pass());
        QTRY_COMPARE_WITH_TIMEOUT(a.state(), Session::SignedIn, 15000);
        QVERIFY(QFileInfo::exists(run.filePath(u"taos/session"_s)));

        Session b(opts());
        b.setTokenSource(new DesktopSessionTokenSource(run.path()));
        QSignalSpy needB(&b, &Session::signInRequired);
        b.start();
        QTRY_COMPARE_WITH_TIMEOUT(b.state(), Session::SignedIn, 15000);
        QCOMPARE(needB.size(), 0);
        QCOMPARE(b.userName(), user());
        QVERIFY(wait(Notifications(b.client()).list()).ok());
    }

    void bearerOnAuthMeIsRecorded()
    {
        const QString tok = qEnvironmentVariable("TAOS_REAL_LOCAL_TOKEN").trimmed();
        if (tok.isEmpty())
            QSKIP("TAOS_REAL_LOCAL_TOKEN not set");
        Client c(opts());
        c.setBearerToken(tok);
        const Result me = wait(c.call(u"auth.me"_s));
        const Result list = wait(Notifications(&c).list());
        qInfo() << "RECORDED bearer on /auth/me ->" << (me.ok() ? "OK" : "FAILED") << me.error.kind()
                << me.error.httpStatus() << me.error.message();
        qInfo() << "RECORDED bearer on /api/notifications ->" << (list.ok() ? "OK" : "FAILED") << list.error.kind();
        // Expected until card tsk-wkwppk lands: /auth/me answers 401 to a bearer.
        QVERIFY(list.ok());
    }
};

QTEST_MAIN(TstReal)
#include "tst_real.moc"
