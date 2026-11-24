// ActivityEngine, ModelActivityFeed and the restart action against the fake
// controller (the foundation's, plus fake_activity.py for the Activity routes).
#include <QSignalSpy>
#include <QtTest>

#include "engine.h"
#include "fakeact.h"
#include "feed.h"
#include "taosqt/session.h"

using namespace Qt::StringLiterals;
using namespace Taos;
using namespace Taos::Activity;

class TstEngine : public QObject
{
    Q_OBJECT
    FakeAct *fake = nullptr;
    Session *session = nullptr;

    void signIn()
    {
        delete session;
        session = new Session(actOptions(fake->url()));
        QSignalSpy ok(session, &Session::signedIn);
        session->signIn(u"alex"_s, FAKE_PASSWORD_TEXT);
        QTRY_COMPARE_WITH_TIMEOUT(ok.size(), 1, 8000);
    }
    static QVariantMap m(const QVariant &v) { return v.toMap(); }

private Q_SLOTS:
    void initTestCase()
    {
        fake = new FakeAct;
        QVERIFY2(fake->ok(), "fake controller did not start");
    }
    void cleanupTestCase() { delete session; delete fake; }
    void init()
    {
        fake->reset();
        signIn();
    }

    void firstBatchLoadsEverything()
    {
        ActivityEngine e;
        e.setClient(session->client());
        QCOMPARE(e.phase(), int(ActivityEngine::Loading));
        e.setActive(true);
        QTRY_COMPARE_WITH_TIMEOUT(e.phase(), int(ActivityEngine::Ready), 8000);
        QVERIFY(e.activity().contains(u"cpu"_s));
        QCOMPARE(e.hasScheduler(), true);
        // tasks are asked for with limit=8, like the web app
        QTRY_VERIFY_WITH_TIMEOUT(fake->stats().value(u"tasks_limits"_s).toArray().contains(8), 5000);
        QVERIFY(e.tasks().size() <= 8);
        QCOMPARE(e.history().value(u"cpu"_s).toList().size(), 1);
        // the zram/network lists the controller sends as {} on its error path would read as empty;
        // here they are real lists
        QCOMPARE(e.gpus().size(), 1);
        QCOMPARE(m(e.gpus().at(0)).value(u"label"_s).toString(), u"NVIDIA GTX 1050 Ti 4GB"_s);
    }

    void loadedModelsMergeWorkersAndWorkerStatus()
    {
        ActivityEngine e;
        e.setClient(session->client());
        e.setActive(true);
        QTRY_COMPARE_WITH_TIMEOUT(e.workers().size(), 2, 8000);
        const QVariantMap pc = m(e.workers().at(0));
        QCOMPARE(pc.value(u"stateLabel"_s).toString(), u"online"_s);   // heartbeat 5 s old
        QCOMPARE(pc.value(u"latent"_s).toList().size(), 1);          // image-gen is potential, not current
        QCOMPARE(m(e.workers().at(1)).value(u"stateLabel"_s).toString(), u"unknown"_s);
        // the worker's loaded model "llama3" is in the merged list with its host
        bool found = false;
        for (const QVariant &v : e.loadedModels())
            if (m(v).value(u"name"_s) == u"llama3"_s && m(v).value(u"host"_s) == u"pc"_s)
                found = true;
        QVERIFY(found);
        // local rows default to "controller"
        QCOMPARE(m(e.loadedModels().at(0)).value(u"host"_s).toString(), u"controller"_s);
    }

    void reducedWorkersUseServerStatus()
    {
        fake->act({{u"workers"_s, u"reduced"_s}});
        ActivityEngine e;
        e.setClient(session->client());
        e.setActive(true);
        QTRY_COMPARE_WITH_TIMEOUT(e.workers().size(), 2, 8000);
        QCOMPARE(m(e.workers().at(0)).value(u"stateLabel"_s).toString(), u"online"_s);
        QCOMPARE(m(e.workers().at(1)).value(u"stateLabel"_s).toString(), u"stale"_s);
    }

    void everyGpuTheControllerReportsIsShown()
    {
        fake->act({{u"gpus"_s, 3}});
        ActivityEngine e;
        e.setClient(session->client());
        e.setActive(true);
        QTRY_COMPARE_WITH_TIMEOUT(e.gpus().size(), 3, 8000);
        QCOMPARE(m(e.gpus().at(0)).value(u"label"_s).toString(), u"NVIDIA GeForce RTX 3090"_s);
        QCOMPARE(m(e.gpus().at(2)).value(u"label"_s).toString(), u"Intel UHD Graphics 770"_s);
        QCOMPARE(m(e.gpus().at(2)).value(u"loadPercent"_s).toDouble(), -1.0);
        QVERIFY(e.history().contains(u"gpu.1.load"_s));
        QVERIFY(!e.history().contains(u"gpu.2.load"_s)); // no stats, no graph
    }

    void partialFailureKeepsLastGoodData()
    {
        ActivityEngine e;
        e.setClient(session->client());
        e.setPollIntervalMs(60);
        e.setActive(true);
        QTRY_COMPARE_WITH_TIMEOUT(e.phase(), int(ActivityEngine::Ready), 8000);
        QVERIFY(e.hasScheduler());
        // scheduler stats now fail: the rest keeps updating, the old stats stay
        fake->base({{u"fail"_s, QJsonObject{{u"path"_s, u"/api/scheduler/stats"_s}, {u"status"_s, 503}, {u"times"_s, 50}}}});
        const int before = e.history().value(u"cpu"_s).toList().size();
        QTRY_VERIFY_WITH_TIMEOUT(e.history().value(u"cpu"_s).toList().size() > before + 2, 5000);
        QVERIFY(e.hasScheduler());
        QCOMPARE(e.phase(), int(ActivityEngine::Ready));
        QVERIFY(e.errorText().isEmpty());
        // /api/activity now fails: last good data stays and an error is set
        fake->act({{u"fail_activity"_s, true}});
        QTRY_VERIFY_WITH_TIMEOUT(!e.errorText().isEmpty(), 5000);
        QCOMPARE(e.phase(), int(ActivityEngine::Ready));
        QVERIFY(e.activity().contains(u"cpu"_s));
        fake->act({{u"fail_activity"_s, false}});
        QTRY_VERIFY_WITH_TIMEOUT(e.errorText().isEmpty(), 5000);
    }

    void neverSucceededIsUnavailable()
    {
        fake->act({{u"fail_activity"_s, true}});
        ActivityEngine e;
        e.setClient(session->client());
        e.setActive(true);
        QTRY_COMPARE_WITH_TIMEOUT(e.phase(), int(ActivityEngine::Unavailable), 8000);
        QVERIFY(!e.errorText().isEmpty());
    }

    void graphHistoryIsCapped()
    {
        ActivityEngine e;
        e.setClient(session->client());
        e.setPollIntervalMs(5);
        e.setActive(true);
        QTRY_VERIFY_WITH_TIMEOUT(e.history().value(u"cpu"_s).toList().size() >= 3, 8000);
        QSignalSpy s(&e, &ActivityEngine::batchFinished);
        QTRY_VERIFY_WITH_TIMEOUT(s.size() > 5, 8000);
        QVERIFY(e.history().value(u"cpu"_s).toList().size() <= ActivityEngine::kHistory);
    }

    void pollsOnlyWhileActive()
    {
        ActivityEngine e;
        e.setClient(session->client());
        e.setPollIntervalMs(30);
        e.setActive(true);
        QTRY_VERIFY_WITH_TIMEOUT(fake->stat("activity_calls") >= 3, 8000);
        e.setActive(false);
        QTest::qWait(150); // let an in-flight call finish
        const int frozen = fake->stat("activity_calls");
        QTest::qWait(400);
        QCOMPARE(fake->stat("activity_calls"), frozen);
        e.setActive(true); // visible again: refreshes at once
        QTRY_VERIFY_WITH_TIMEOUT(fake->stat("activity_calls") > frozen, 5000);
    }

    // ---- model activity feed ----
    void feedSeedsFromHistoryAndStreamsLive()
    {
        ModelActivityFeed f;
        f.setClient(session->client());
        f.setActive(true);
        QTRY_COMPARE_WITH_TIMEOUT(f.count(), 3, 8000);
        QTRY_VERIFY_WITH_TIMEOUT(f.live(), 8000);
        QCOMPARE(f.eventModel()->get(0).value(u"seq"_s).toInt(), 3); // newest first
        QCOMPARE(f.eventTypes().size(), 7);
        QCOMPARE(fake->stats().value(u"stream_query"_s).toObject().value(u"limit"_s).toString(), u"0"_s); // live only
        QCOMPARE(fake->stats().value(u"history_query"_s).toObject().value(u"limit"_s).toString(), u"200"_s);
        fake->act({{u"add_event"_s, QJsonObject{{u"model"_s, u"new-model"_s}, {u"worker"_s, u"nas"_s}}}});
        QTRY_COMPARE_WITH_TIMEOUT(f.count(), 4, 5000);
        QCOMPARE(f.eventModel()->get(0).value(u"model"_s).toString(), u"new-model"_s);
        QVERIFY(f.workerOptions().contains(u"nas"_s));   // option lists grow from what was seen
        QVERIFY(f.modelOptions().contains(u"new-model"_s));
    }

    void feedMergesFramesThatArriveDuringTheHistoryFetch()
    {
        fake->act({{u"history_delay_ms"_s, 700}});
        ModelActivityFeed f;
        f.setClient(session->client());
        f.setActive(true);
        QTRY_VERIFY_WITH_TIMEOUT(f.live(), 8000);
        fake->act({{u"add_event"_s, QJsonObject{{u"model"_s, u"during-fetch"_s}}}});
        QTRY_COMPARE_WITH_TIMEOUT(f.count(), 1, 3000);   // the live frame landed first
        // ... and is still there once the history arrives (merge, not replace)
        QTRY_COMPARE_WITH_TIMEOUT(f.count(), 4, 8000);
        bool kept = false;
        for (int i = 0; i < f.count(); ++i)
            kept |= f.eventModel()->get(i).value(u"model"_s) == u"during-fetch"_s;
        QVERIFY(kept);
    }

    void feedMergesBySeqNotDuplicating()
    {
        ModelActivityFeed f;
        f.setClient(session->client());
        f.setActive(true);
        QTRY_VERIFY_WITH_TIMEOUT(f.live() && f.count() == 3, 8000);
        fake->act({{u"add_event"_s, QJsonObject{{u"seq"_s, 3}, {u"model"_s, u"bge-m3"_s}}}}); // same seq as a history row
        QTest::qWait(300);
        QCOMPARE(f.count(), 3);
    }

    void feedKeepsAtMost200()
    {
        ModelActivityFeed f;
        f.setClient(session->client());
        f.setActive(true);
        QTRY_VERIFY_WITH_TIMEOUT(f.live(), 8000);
        for (int i = 0; i < 215; ++i)
            fake->act({{u"add_event"_s, QJsonObject{{u"model"_s, u"m%1"_s.arg(i)}}}});
        QTRY_COMPARE_WITH_TIMEOUT(f.eventModel()->get(0).value(u"model"_s).toString(), u"m214"_s, 15000);
        QCOMPARE(f.count(), ModelActivityFeed::kMaxEvents);
    }

    void feedFiltersAskTheServerAndReset()
    {
        ModelActivityFeed f;
        f.setClient(session->client());
        f.setActive(true);
        QTRY_COMPARE_WITH_TIMEOUT(f.count(), 3, 8000);
        f.setWorker(u"pc"_s);
        QTRY_COMPARE_WITH_TIMEOUT(f.count(), 1, 8000);
        QCOMPARE(fake->stats().value(u"history_query"_s).toObject().value(u"worker"_s).toString(), u"pc"_s);
        QTRY_VERIFY_WITH_TIMEOUT(fake->stats().value(u"stream_query"_s).toObject().value(u"worker"_s).toString() == u"pc"_s, 5000);
        QVERIFY(f.workerOptions().contains(u"controller"_s)); // options persist across filters
        f.setWorker(QString());
        f.setEventType(u"model.evict"_s);
        QTRY_COMPARE_WITH_TIMEOUT(f.count(), 1, 8000);
    }

    void feedStopsStreamWhenHidden()
    {
        ModelActivityFeed f;
        f.setClient(session->client());
        f.setActive(true);
        QTRY_VERIFY_WITH_TIMEOUT(f.live(), 8000);
        QTRY_COMPARE_WITH_TIMEOUT(fake->stat("stream_open"), 1, 5000);
        f.setActive(false);
        QTRY_COMPARE_WITH_TIMEOUT(fake->stat("stream_open"), 0, 5000);
        QVERIFY(!f.live());
        fake->act({{u"add_event"_s, QJsonObject{{u"model"_s, u"while-hidden"_s}}}});
        f.setActive(true);   // shown again: history is merged, the missed event appears
        QTRY_COMPARE_WITH_TIMEOUT(f.count(), 4, 8000);
        QTRY_VERIFY_WITH_TIMEOUT(f.live(), 8000);
    }

    void feedNeedsASessionCookie()
    {
        // the host local token (a bearer) is refused by the model-activity routes
        ClientOptions o = actOptions(fake->url());
        o.bearerToken = u"local-token-123"_s;
        Client c(o);
        ModelActivityFeed f;
        f.setClient(&c);
        f.setActive(true);
        QTRY_VERIFY_WITH_TIMEOUT(f.signInNeeded(), 8000);
        QCOMPARE(f.errorText(), u"Sign in to see model activity"_s);
        QCOMPARE(f.count(), 0);
    }

    // ---- the recovery action ----
    void restartConfirmRunningDone()
    {
        ActivityEngine e;
        e.setClient(session->client());
        QCOMPARE(e.restartPhase(), int(ActivityEngine::RestartIdle));
        e.askRestart();
        QCOMPARE(e.restartPhase(), int(ActivityEngine::RestartConfirm));
        QCOMPARE(fake->stat("restarts"), 0);   // asking changes nothing on the server
        e.confirmRestart();
        QCOMPARE(e.restartPhase(), int(ActivityEngine::RestartRunning));
        QTRY_COMPARE_WITH_TIMEOUT(e.restartPhase(), int(ActivityEngine::RestartDone), 8000);
        QCOMPARE(fake->stat("restarts"), 1);
        const QVariantMap r = e.restartResult();
        QCOMPARE(r.value(u"status"_s).toString(), u"partial"_s);
        QCOMPARE(r.value(u"results"_s).toList().size(), 2);
        e.dismissRestart();
        QCOMPARE(e.restartPhase(), int(ActivityEngine::RestartIdle));
        QVERIFY(e.restartResult().isEmpty());
    }

    void restartCancelDoesNotCallTheServer()
    {
        ActivityEngine e;
        e.setClient(session->client());
        e.askRestart();
        e.dismissRestart();
        QCOMPARE(e.restartPhase(), int(ActivityEngine::RestartIdle));
        QCOMPARE(fake->stat("restarts"), 0);
    }

    void restartRefusedForNonAdminSaysSo()
    {
        fake->act({{u"admin"_s, false}});
        ActivityEngine e;
        e.setClient(session->client());
        e.askRestart();
        e.confirmRestart();
        QTRY_COMPARE_WITH_TIMEOUT(e.restartPhase(), int(ActivityEngine::RestartError), 8000);
        QCOMPARE(e.restartError(), u"Admin access is required to restart AI services."_s);
    }

    void restartServerErrorShowsItsText()
    {
        fake->act({{u"restart_status"_s, 500}});
        ActivityEngine e;
        e.setClient(session->client());
        e.askRestart();
        e.confirmRestart();
        QTRY_COMPARE_WITH_TIMEOUT(e.restartPhase(), int(ActivityEngine::RestartError), 8000);
        QCOMPARE(e.restartError(), u"restart blew up"_s);
    }

    void restartSummaryNamesWhatRestarts()
    {
        const QString s = ActivityEngine::restartSummary();
        QVERIFY(s.contains(u"qmd"_s));
        QVERIFY(s.contains(u"rkllama"_s));
        QVERIFY(s.contains(u"interrupted"_s));
    }
};

QTEST_MAIN(TstEngine)
#include "tst_engine.moc"
