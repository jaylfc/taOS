#include <QFile>
#include <QSignalSpy>
#include <QTemporaryDir>
#include <QtTest>

#include "displaycontroller.h"
#include "drmfixture.h"
#include "fakesway.h"
#include "outputwatcher.h"

static const QString FX = TAOS_DISPLAY_FIXTURES;

struct Rig {
    QTemporaryDir dir;
    FakeSway sway;
    qint64 now = 1000000;
    QString conf, drm;
    DisplayController *dc = nullptr;
    explicit Rig(const char *fixture = "owner.json", bool withDrm = true) {
        conf = dir.path() + "/20-display.conf";
        drm = dir.path() + "/drm";
        QDir().mkpath(drm);
        if (!sway.load(FX + "/" + fixture)) qFatal("fixture");
        if (withDrm && QString(fixture) == "owner.json")
            addConnector(drm, "HDMI-A-1", "3840x1200\n3840x1200\n3840x1200\n3840x2160\n", makeEdid(1043, 326));
        if (withDrm && QString(fixture) == "two.json") {
            addConnector(drm, "eDP-1", "1920x1080\n1920x1080\n", makeEdid(294, 165));
            addConnector(drm, "DP-2", "3840x2160\n3840x2160\n2560x1440\n", makeEdid(597, 336));
        }
        dc = new DisplayController(&sway, DisplayConfigFile(conf), drm, [this] { return now; });
        dc->setSettleMs(0);
    }
    ~Rig() { delete dc; }
    DisplayConfigFile cfg() const { return DisplayConfigFile(conf); }
    DisplayMode cur(const QString &n = "HDMI-A-1") const {
        const QJsonObject c = sway.out(n).value("current_mode").toObject();
        DisplayMode m; m.width = c["width"].toInt(); m.height = c["height"].toInt(); m.mHz = c["refresh"].toInt(); return m;
    }
};

using Opt = DisplayController::ApplyOptions;

class TestDisplayController : public QObject {
    Q_OBJECT
private slots:
    void firstRunDefersTheModeButSavesTheScale() {
        Rig r;
        const auto rep = r.dc->applyAll();
        QCOMPARE(rep.deferred.size(), 1);
        QVERIFY(rep.errors.isEmpty());
        QCOMPARE(r.sway.count(" mode "), 0);  // no unconfirmed mode change from the helper
        const auto e = r.cfg().read()["Acme UW43 S0042"];
        QCOMPARE(e.scale, 1.0);               // the ultrawide's recommended scale
        QVERIFY(!e.hasMode());
    }
    void immediateAppliesNativeAtHighestRefresh() {
        Rig r;
        Opt o; o.unsaved = Opt::Immediate;
        const auto rep = r.dc->applyAll(o);
        QVERIFY2(rep.errors.isEmpty(), qPrintable(rep.errors.join(';')));
        QCOMPARE(r.sway.log, QStringList{"output HDMI-A-1 mode 3840x1200@120Hz"});
        QCOMPARE(r.cur().mHz, 120000);
        QCOMPARE(r.cur().height, 1200);
        const auto e = r.cfg().read()["Acme UW43 S0042"];
        QCOMPARE(e.mHz, 120000);
        QCOMPARE(e.scale, 1.0);
        // The output events our own change causes: nothing more to do.
        r.sway.log.clear();
        const auto again = r.dc->applyAll(o);
        QVERIFY(again.changed.isEmpty() && again.errors.isEmpty());
        QVERIFY(r.sway.log.isEmpty());
    }
    void neverIssuesA2160Mode() {
        Rig r;
        Opt o; o.unsaved = Opt::Immediate;
        r.dc->applyAll(o);
        QCOMPARE(r.sway.count("2160"), 0);
    }
    void refusedModeIsRevertedAndNotRetried() {
        Rig r;
        r.sway.refuse << "3840x1200@120Hz";
        Opt o; o.unsaved = Opt::Immediate;
        auto rep = r.dc->applyAll(o);
        QCOMPARE(rep.reverted.size(), 1);
        QVERIFY(!rep.errors.isEmpty());
        QCOMPARE(r.cur().mHz, 60000);
        QCOMPARE(r.sway.count("mode 3840x1200@60Hz"), 1);  // the restore to the previous mode
    }
    void rejectedModeIsNotRetriedOnTheNextEvent() {
        Rig r;
        r.sway.refuse << "3840x1200@120Hz";
        Opt o; o.unsaved = Opt::Immediate;
        r.dc->applyAll(o);
        const int attempts = r.sway.count("mode 3840x1200@120Hz");
        QCOMPARE(attempts, 1);
        r.dc->applyAll(o);  // the hotplug echo
        QCOMPARE(r.sway.count("mode 3840x1200@120Hz"), 1);  // not tried again
        QCOMPARE(r.cur().mHz, 100000);                      // the next best at the same resolution
        r.dc->applyAll(o);
        QCOMPARE(r.sway.count("mode 3840x1200@100Hz"), 1);  // and that one is stable
    }
    void silentlyIgnoredModeIsCaughtByReadBack() {
        Rig r;
        r.sway.ignore << "3840x1200@120Hz";  // exit 0, nothing happens
        Opt o; o.unsaved = Opt::Immediate;
        const auto rep = r.dc->applyAll(o);
        QCOMPARE(rep.reverted.size(), 1);
        QVERIFY(r.cfg().read()["Acme UW43 S0042"].mHz == 0);  // never saved
        QCOMPARE(r.cur().mHz, 60000);
    }
    void blackOutputFallsBack() {
        Rig r;
        r.sway.blank << "3840x1200@120Hz";
        Opt o; o.unsaved = Opt::Immediate;
        const auto rep = r.dc->applyAll(o);
        QCOMPARE(rep.reverted.size(), 1);
        QVERIFY(r.sway.out("HDMI-A-1").value("active").toBool());  // back on
        QCOMPARE(r.cur().mHz, 60000);
        QVERIFY(r.sway.log.last().contains("@60Hz"));  // the restore
    }
    void savedSettingsApplyWithoutAsking() {
        Rig r;
        DisplayEntry e; e.key = "Acme UW43 S0042"; e.width = 3840; e.height = 1200; e.mHz = 100000; e.scale = 1.5;
        QVERIFY(r.cfg().upsert(e));
        const auto rep = r.dc->applyAll();  // Defer: saved settings are not "unsaved"
        QVERIFY(rep.deferred.isEmpty());
        QCOMPARE(r.cur().mHz, 100000);
        QCOMPARE(r.sway.out("HDMI-A-1").value("scale").toDouble(), 1.5);
        QVERIFY(r.sway.log.contains("output HDMI-A-1 scale 1.5"));
    }
    void savedSettingsFollowTheMonitorToAnotherConnector() {
        Rig r;
        DisplayEntry e; e.key = "Acme UW43 S0042"; e.width = 3840; e.height = 1200; e.mHz = 100000; e.scale = 1.25;
        QVERIFY(r.cfg().upsert(e));
        QJsonObject o = r.sway.out("HDMI-A-1");
        o["name"] = "DP-3";
        r.sway.outs = QJsonArray{o};
        r.dc->applyAll();
        QCOMPARE(r.cur("DP-3").mHz, 100000);
        QCOMPARE(r.sway.out("DP-3").value("scale").toDouble(), 1.25);
    }
    void twoOutputs() {
        Rig r("two.json");
        Opt o; o.unsaved = Opt::Immediate;
        const auto rep = r.dc->applyAll(o);
        QVERIFY2(rep.errors.isEmpty(), qPrintable(rep.errors.join(';')));
        QCOMPARE(r.cur("DP-2").width, 3840);
        QCOMPARE(r.cur("DP-2").mHz, 60000);
        QCOMPARE(r.cur("eDP-1").mHz, 60000);
        QCOMPARE(r.sway.out("DP-2").value("scale").toDouble(), 1.25);   // 27 inch 4K
        QCOMPARE(r.sway.out("eDP-1").value("scale").toDouble(), 1.25);  // 13.3 inch full HD
        const auto all = r.cfg().read();
        QCOMPARE(all.size(), 2);
        QVERIFY(all.contains("Contoso P27-4K SN77"));
        QVERIFY(all.contains("LGD 0x05F1 Unknown"));
        QCOMPARE(r.dc->outputs().size(), 2);
    }
    void noPreferredInfoKeepsTheResolution() {
        Rig r("nopref.json");
        Opt o; o.unsaved = Opt::Immediate;
        r.dc->applyAll(o);
        QCOMPARE(r.cur("HDMI-A-2").width, 1920);
        QCOMPARE(r.cur("HDMI-A-2").mHz, 60000);
        QCOMPARE(r.sway.count("2160"), 0);
    }
    void scaleChangeEmitsTheRightCommandAndNeedsNoConfirmation() {
        Rig r;
        r.dc->refresh();
        QSignalSpy say(r.dc, &DisplayController::announcement);
        QVERIFY(r.dc->setScale("HDMI-A-1", 1.25));
        QCOMPARE(r.sway.log, QStringList{"output HDMI-A-1 scale 1.25"});
        QVERIFY(!r.dc->trialActive());
        QCOMPARE(r.cfg().read()["Acme UW43 S0042"].scale, 1.25);
        QCOMPARE(say.last().at(0).toString(), QString("Scale 125 percent"));
        QCOMPARE(r.dc->outputs().at(0).toMap()["scalePercent"].toInt(), 125);
        QVERIFY(r.dc->setScale("HDMI-A-1", 1.1));  // custom values work too
        QCOMPARE(r.sway.log.last(), QString("output HDMI-A-1 scale 1.1"));
        QVERIFY(!r.dc->setScale("NOPE-1", 1.5));
    }
    void trialRevertsOnTimeoutWithAFakeClock() {
        Rig r;
        r.dc->refresh();
        QSignalSpy say(r.dc, &DisplayController::announcement);
        QVERIFY(r.dc->setRefresh("HDMI-A-1", 120000));
        QVERIFY(r.dc->trialActive());
        QCOMPARE(r.cur().mHz, 120000);
        QCOMPARE(r.dc->trialSecondsLeft(), 10);
        QCOMPARE(DisplayController::kConfirmSeconds, 10);
        r.now += 4000; r.dc->poll();
        QVERIFY(r.dc->trialActive());
        QCOMPARE(r.dc->trialSecondsLeft(), 6);
        r.now += 5999; r.dc->poll();
        QVERIFY(r.dc->trialActive());          // one millisecond short
        QCOMPARE(r.dc->trialSecondsLeft(), 1);
        r.now += 1; r.dc->poll();
        QVERIFY(!r.dc->trialActive());         // the deadline: back to the old mode
        QCOMPARE(r.cur().mHz, 60000);
        QVERIFY(!r.cfg().read().contains("Acme UW43 S0042") || !r.cfg().read()["Acme UW43 S0042"].hasMode());
        QCOMPARE(say.last().at(0).toString(), QString("Went back to the previous display setting"));
        // And the same mode is not offered again by a hotplug echo.
        Opt o; o.unsaved = Opt::Immediate;
        r.dc->applyAll(o);
        QCOMPARE(r.cur().mHz, 100000);
    }
    void trialConfirmSaves() {
        Rig r;
        r.dc->refresh();
        QVERIFY(r.dc->setRefresh("HDMI-A-1", 120000));
        r.now += 3000;
        r.dc->confirm();
        QVERIFY(!r.dc->trialActive());
        const auto e = r.cfg().read()["Acme UW43 S0042"];
        QCOMPARE(e.mHz, 120000);
        QCOMPARE(e.width, 3840);
        r.now += 60000; r.dc->poll();
        QCOMPARE(r.cur().mHz, 120000);  // a late tick does nothing
    }
    void escapeRevertsAtOnce() {
        Rig r;
        r.dc->refresh();
        QVERIFY(r.dc->setRefresh("HDMI-A-1", 100000));
        r.dc->revert();
        QVERIFY(!r.dc->trialActive());
        QCOMPARE(r.cur().mHz, 60000);
    }
    void oneTrialAtATime() {
        Rig r;
        r.dc->refresh();
        QVERIFY(r.dc->setRefresh("HDMI-A-1", 120000));
        QVERIFY(!r.dc->setRefresh("HDMI-A-1", 100000));
        QVERIFY(!r.dc->errorText().isEmpty());
    }
    void failedTrialLeavesNoDialog() {
        Rig r;
        r.dc->refresh();
        r.sway.refuse << "3840x1200@120Hz";
        QVERIFY(!r.dc->setRefresh("HDMI-A-1", 120000));
        QVERIFY(!r.dc->trialActive());
        QCOMPARE(r.cur().mHz, 60000);
    }
    void useRecommended() {
        Rig r;
        r.dc->refresh();
        QVERIFY(r.dc->setScale("HDMI-A-1", 1.75));
        r.sway.log.clear();
        QVERIFY(r.dc->useRecommended("HDMI-A-1"));
        QVERIFY(r.sway.log.contains("output HDMI-A-1 scale 1"));
        QVERIFY(r.dc->trialActive());           // the mode went to 120 Hz and waits for confirmation
        QCOMPARE(r.cur().mHz, 120000);
        r.dc->confirm();
        QVERIFY(r.dc->outputs().at(0).toMap()["isRecommended"].toBool());
        r.sway.log.clear();
        QVERIFY(r.dc->useRecommended("HDMI-A-1"));  // already there: nothing to do
        QVERIFY(r.sway.log.isEmpty());
        QVERIFY(!r.dc->trialActive());
    }
    void modelForTheMenu() {
        Rig r;
        r.dc->refresh();
        QVERIFY(r.dc->setScale("HDMI-A-1", 1.1));
        const QVariantMap m = r.dc->outputs().at(0).toMap();
        QCOMPARE(m["label"].toString(), QString("UW43"));
        QCOMPARE(m["modeText"].toString(), QString("3840 x 1200, 60 Hz"));
        QCOMPARE(m["recommendedPercent"].toInt(), 100);
        QCOMPARE(m["nativeText"].toString(), QString("3840 x 1200"));
        QCOMPARE(m["scales"].toList(), (QVariantList{100, 110, 125, 150, 175, 200}));  // the custom value, in order
        const QVariantList rates = m["rates"].toList();
        QCOMPARE(rates.size(), 3);
        QCOMPARE(rates[0].toMap()["text"].toString(), QString("120 Hz"));
        QVERIFY(rates[2].toMap()["current"].toBool());
        QVERIFY(!m["isRecommended"].toBool());
    }
    void xwaylandNote() {
        Rig r;
        QFile t(FX + "/tree-xwayland.json"); QVERIFY(t.open(QIODevice::ReadOnly));
        r.sway.tree = t.readAll();
        r.dc->refresh();
        QVERIFY(r.dc->xwaylandPresent());
        r.sway.tree = "{\"type\":\"root\",\"nodes\":[]}";
        r.dc->refresh();
        QVERIFY(!r.dc->xwaylandPresent());
    }
    void unreadableCompositorReportsInsteadOfGuessing() {
        Rig r;
        r.sway.failReads = true;
        QVERIFY(!r.dc->refresh());
        const auto rep = r.dc->applyAll();
        QVERIFY(!rep.errors.isEmpty());
        QVERIFY(r.sway.log.isEmpty());
        QVERIFY(!QFile::exists(r.conf));
    }
    // ---- the real swaymsg runner against a scripted program ----
    void swayMsgRunner() {
        QTemporaryDir d;
        const QString script = d.path() + "/swaymsg";
        QFile f(script);
        QVERIFY(f.open(QIODevice::WriteOnly));
        f.write("#!/bin/sh\nprintf '%s\\n' \"$*\" >> \"$0.log\"\ncase \"$*\" in\n"
                "  *scale\\ 9*) echo '[{\"success\":false,\"error\":\"Invalid scale\"}]';;\n"
                "  *boom*) echo oops >&2; exit 3;;\n"
                "  *) echo '[{\"success\":true}]';;\nesac\n");
        f.close();
        QFile::setPermissions(script, QFileDevice::ReadOwner | QFileDevice::WriteOwner | QFileDevice::ExeOwner);
        SwayMsg m(script);
        auto ok = m.run({"output", "HDMI-A-1", "scale", "1.25"});
        QVERIFY(ok.ok);
        auto bad = m.run({"output", "HDMI-A-1", "scale", "9"});
        QVERIFY(!bad.ok);
        QCOMPARE(bad.error, QString("Invalid scale"));
        auto ex = m.run({"output", "boom"});
        QVERIFY(!ex.ok);
        QCOMPARE(ex.error, QString("oops"));
        QVERIFY(!SwayMsg(d.path() + "/missing").run({"x"}).ok);
        QFile lg(script + ".log"); QVERIFY(lg.open(QIODevice::ReadOnly));
        QVERIFY(QString::fromUtf8(lg.readAll()).startsWith("output HDMI-A-1 scale 1.25\n"));
    }
    void watcherDebouncesBursts() {
        QTemporaryDir d;
        const QString script = d.path() + "/swaymsg";
        QFile f(script);
        QVERIFY(f.open(QIODevice::WriteOnly));
        f.write("#!/bin/sh\necho '{\"change\":\"unspecified\"}'\necho '{\"change\":\"unspecified\"}'\necho '{\"change\":\"unspecified\"}'\nsleep 3\n");
        f.close();
        QFile::setPermissions(script, QFileDevice::ReadOwner | QFileDevice::WriteOwner | QFileDevice::ExeOwner);
        OutputWatcher w(script, 150);
        QSignalSpy spy(&w, &OutputWatcher::outputsChanged);
        w.start();
        QVERIFY(spy.wait(2500));
        QTest::qWait(400);
        QCOMPARE(spy.size(), 1);
        w.stop();
    }
};
QTEST_MAIN(TestDisplayController)
#include "test_displaycontroller.moc"
