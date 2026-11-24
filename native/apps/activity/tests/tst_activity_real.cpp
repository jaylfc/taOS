// SPDX-License-Identifier: AGPL-3.0-or-later
// Runs against a REAL controller; skipped unless TAOS_REAL_URL is set.
//   TAOS_REAL_URL=http://127.0.0.1:6979 TAOS_REAL_ADMIN=user TAOS_REAL_ADMIN_PASSWORD=pw
//   TAOS_REAL_USER=nonadmin TAOS_REAL_PASSWORD=pw [TAOS_REAL_LOCAL_TOKEN=<token>] tst_activity_real
// Use a scratch controller: the admin restart is NEVER sent here (it would
// restart the host's real AI services); only the non-admin refusal is.
#include <QSignalSpy>
#include <QtTest>

#include "engine.h"
#include "feed.h"
#include "taosqt/session.h"

using namespace Qt::StringLiterals;
using namespace Taos;
using namespace Taos::Activity;

namespace {
ClientOptions opts()
{
    ClientOptions o;
    o.baseUrl = QUrl(qEnvironmentVariable("TAOS_REAL_URL"));
    return o;
}
} // namespace

class TstRealActivity : public QObject
{
    Q_OBJECT
private Q_SLOTS:
    void initTestCase()
    {
        if (qEnvironmentVariableIsEmpty("TAOS_REAL_URL"))
            QSKIP("TAOS_REAL_URL not set");
    }

    void snapshotLoadsAndGraphsUpdate()
    {
        Session s(opts());
        QSignalSpy ok(&s, &Session::signedIn);
        s.signIn(qEnvironmentVariable("TAOS_REAL_ADMIN"), qEnvironmentVariable("TAOS_REAL_ADMIN_PASSWORD"));
        QTRY_COMPARE_WITH_TIMEOUT(ok.size(), 1, 15000);
        QCOMPARE(s.user().value(u"is_admin"_s).toBool(), true);
        ActivityEngine e;
        e.setClient(s.client());
        e.setPollIntervalMs(300); // faster than the app so the test is short
        e.setActive(true);
        QTRY_COMPARE_WITH_TIMEOUT(e.phase(), int(ActivityEngine::Ready), 20000);
        const QVariantMap act = e.activity();
        qInfo() << "hardware:" << e.hardware().value(u"board"_s) << "cores:" << act.value(u"cpu"_s).toMap().value(u"cores"_s).toList().size()
                << "gpus:" << e.gpus().size() << "workers:" << e.workers().size() << "models:" << e.loadedModels().size()
                << "scheduler:" << e.hasScheduler();
        QVERIFY(!act.value(u"cpu"_s).toMap().value(u"cores"_s).toList().isEmpty());
        QVERIFY(act.value(u"memory"_s).toMap().value(u"total_mb"_s).toDouble() > 0);
        QVERIFY(e.hasScheduler());
        QTRY_VERIFY_WITH_TIMEOUT(e.history().value(u"cpu"_s).toList().size() >= 4, 20000);   // graphs update
        QTRY_VERIFY_WITH_TIMEOUT(!e.workers().isEmpty(), 20000);  // the controller registers itself as worker "local"
        // every GPU the controller reports is in the list (this controller reports as many as it sends)
        const QVariantMap raw = act.value(u"gpu"_s).toMap();
        QCOMPARE(e.gpus().size(), (raw.value(u"type"_s).toString().isEmpty() || raw.value(u"type"_s) == u"none"_s) ? 0 : 1);
    }

    void modelActivityOverTheSessionCookie()
    {
        Session s(opts());
        QSignalSpy ok(&s, &Session::signedIn);
        s.signIn(qEnvironmentVariable("TAOS_REAL_ADMIN"), qEnvironmentVariable("TAOS_REAL_ADMIN_PASSWORD"));
        QTRY_COMPARE_WITH_TIMEOUT(ok.size(), 1, 15000);
        ModelActivityFeed f;
        f.setClient(s.client());
        f.setActive(true);
        QTRY_VERIFY_WITH_TIMEOUT(f.live(), 20000);   // the SSE stream is open
        QTRY_VERIFY_WITH_TIMEOUT(!f.loading(), 20000);
        QVERIFY2(f.errorText().isEmpty(), qPrintable(f.errorText()));
        QCOMPARE(f.eventTypes().size(), 7);
    }

    void nonAdminRestartIsRefused()
    {
        Session s(opts());
        QSignalSpy ok(&s, &Session::signedIn);
        s.signIn(qEnvironmentVariable("TAOS_REAL_USER"), qEnvironmentVariable("TAOS_REAL_PASSWORD"));
        QTRY_COMPARE_WITH_TIMEOUT(ok.size(), 1, 15000);
        QCOMPARE(s.user().value(u"is_admin"_s).toBool(), false);
        ActivityEngine e;
        e.setClient(s.client());
        e.askRestart();
        e.confirmRestart();
        QTRY_COMPARE_WITH_TIMEOUT(e.restartPhase(), int(ActivityEngine::RestartError), 15000);
        QCOMPARE(e.restartError(), u"Admin access is required to restart AI services."_s);
        // a non-admin gets the reduced worker view: no heartbeat, status comes from the server
        e.setActive(true);
        QTRY_COMPARE_WITH_TIMEOUT(e.phase(), int(ActivityEngine::Ready), 20000);
        QTRY_VERIFY_WITH_TIMEOUT(!e.workers().isEmpty(), 20000);
        qInfo() << "non-admin worker view:" << e.workers().at(0).toMap().value(u"stateLabel"_s);
    }

    void localTokenCannotReadModelActivity()
    {
        if (qEnvironmentVariableIsEmpty("TAOS_REAL_LOCAL_TOKEN"))
            QSKIP("TAOS_REAL_LOCAL_TOKEN not set");
        ClientOptions o = opts();
        o.bearerToken = qEnvironmentVariable("TAOS_REAL_LOCAL_TOKEN");
        Client c(o);
        ActivityEngine e;
        e.setClient(&c);
        e.setActive(true);
        QTRY_COMPARE_WITH_TIMEOUT(e.phase(), int(ActivityEngine::Ready), 20000);   // the local token reads /api/activity
        ModelActivityFeed f;
        f.setClient(&c);
        f.setActive(true);
        QTRY_VERIFY_WITH_TIMEOUT(f.signInNeeded(), 20000);
        QCOMPARE(f.errorText(), u"Sign in to see model activity"_s);
    }
};

QTEST_MAIN(TstRealActivity)
#include "tst_activity_real.moc"
