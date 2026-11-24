// SPDX-License-Identifier: AGPL-3.0-or-later
// The dock follows the desktop/tiling mode: desktop.json (written by the window engine) plus the
// focused workspace decide whether the dock auto-hides; dock.json's hideInTiling can opt out.
#include <QFile>
#include <QSignalSpy>
#include <QTemporaryDir>
#include <QtTest>

#include "dockcontroller.h"
#include "desktopmode.h"

static void put(const QString &path, const QByteArray &data) {
    QSaveFile f(path);
    QVERIFY(f.open(QIODevice::WriteOnly));
    f.write(data);
    QVERIFY(f.commit());
}

// An engine whose workspace and windows the test sets, standing in for sway.
class FakeEngine : public WindowEngine {
public:
    QString ws;
    QVector<WindowInfo> w;
    QString name() const override { return QStringLiteral("fake"); }
    void start() override {}
    void refresh() override {}
    QVector<WindowInfo> windows() const override { return w; }
    void close(int) override {}
    void minimise(int) override {}
    void restore(int) override {}
    void toggleMinimise(int) override {}
    void focus(int) override {}
    void full(int) override {}
    QString focusedWorkspace() const override { return ws; }
    void setWs(const QString &x) { ws = x; emit focusedWorkspaceChanged(); }
};

class ModeFollow : public QObject {
    Q_OBJECT
private slots:
    void parsing() {
        QTemporaryDir d;
        const QString p = d.path() + QStringLiteral("/desktop.json");
        put(p, R"({"default":"desktop","workspaces":{"2":"tiling","3":"bogus"}})");
        DesktopModes m;
        QVERIFY(DesktopModeFile::read(p, &m));
        QVERIFY(!m.tiling(QStringLiteral("1")));
        QVERIFY(m.tiling(QStringLiteral("2")));
        QVERIFY(!m.tiling(QStringLiteral("3")));   // a bad value is desktop
        put(p, R"({"default":"tiling","workspaces":{"1":"desktop"}})");
        QVERIFY(DesktopModeFile::read(p, &m));
        QVERIFY(m.tiling(QStringLiteral("9")));
        QVERIFY(!m.tiling(QStringLiteral("1")));
        QVERIFY(!DesktopModeFile::read(d.path() + QStringLiteral("/missing.json"), &m));
        QVERIFY(!m.tiling(QStringLiteral("1")));   // missing file: desktop everywhere
    }
    void liveReload() {
        QTemporaryDir d;
        const QString p = d.path() + QStringLiteral("/desktop.json");
        put(p, R"({"default":"desktop"})");
        DesktopModeWatcher w(p);
        QSignalSpy spy(&w, &DesktopModeWatcher::changed);
        put(p, R"({"default":"desktop","workspaces":{"1":"tiling"}})");
        QTRY_COMPARE(spy.count(), 1);
        QVERIFY(w.modes().tiling(QStringLiteral("1")));
        put(p, R"({"default":"tiling"})");
        QTRY_COMPARE(spy.count(), 2);
        QVERIFY(w.modes().tiling(QStringLiteral("7")));
    }
    void controllerFollows() {
        QTemporaryDir d;
        DockController::Options o;
        o.configPath = d.path() + QStringLiteral("/dock.json");
        o.modePath = d.path() + QStringLiteral("/desktop.json");
        o.appDirs = {d.path() + QStringLiteral("/none")};
        put(o.modePath, R"({"default":"desktop","workspaces":{"2":"tiling"}})");
        DockController c(o);
        // swap in the fake engine's workspace by reading the real (unconnected) engine: workspace empty = desktop
        QVERIFY(!c.effectiveAutohide());
        QVERIFY(c.layout().exclusiveZone > 0);
    }
    void effectiveAutohideTable() {
        QTemporaryDir d;
        struct Row { const char *dockJson; const char *modes; const char *ws; bool expect; };
        const Row rows[] = {
            {R"({"autohide":false})", R"({"default":"desktop"})", "1", false},                              // desktop mode: the person's setting
            {R"({"autohide":true})", R"({"default":"desktop"})", "1", true},                                // ...which may be auto-hide
            {R"({"autohide":false})", R"({"default":"desktop","workspaces":{"1":"tiling"}})", "1", true},   // tiling workspace hides it
            {R"({"autohide":false})", R"({"default":"desktop","workspaces":{"1":"tiling"}})", "2", false},  // another workspace is desktop
            {R"({"autohide":false,"hideInTiling":false})", R"({"default":"tiling"})", "1", false},          // opt out: stays visible
            {R"({"autohide":true,"hideInTiling":false})", R"({"default":"tiling"})", "1", true},            // explicit autohide still wins
            {R"({"autohide":false})", R"({"default":"tiling"})", "5", true},                                // default tiling, no override
        };
        int n = 0;
        for (const Row &r : rows) {
            ++n;
            DockController::Options o;
            o.configPath = d.path() + QStringLiteral("/dock%1.json").arg(n);
            o.modePath = d.path() + QStringLiteral("/desktop%1.json").arg(n);
            o.appDirs = {d.path() + QStringLiteral("/none")};
            o.watch = false;
            put(o.configPath, r.dockJson);
            put(o.modePath, r.modes);
            DockController c(o);
            // Drive the focused workspace through a window the model knows about.
            WindowInfo w;
            w.id = 1; w.appId = QStringLiteral("x"); w.workspace = QString::fromLatin1(r.ws); w.focused = true;
            c.model()->setWindows({w});
            QCOMPARE(c.effectiveAutohide(), r.expect);
            QCOMPARE(c.layout().exclusiveZone == 0, r.expect);   // no reserved space exactly when hidden
            QVERIFY2(c.revealed() != r.expect || !r.expect, "a hidden dock starts not revealed");
        }
    }
    void followsNewPinsAndNewLauncherEntriesLive() {
        QTemporaryDir d;
        const QString apps = d.path() + QStringLiteral("/applications");
        DockController::Options o;
        o.configPath = d.path() + QStringLiteral("/dock.json");
        o.modePath = d.path() + QStringLiteral("/desktop.json");
        o.appDirs = {apps};   // does not exist yet: the first install creates it
        put(o.configPath, R"({"pinned":["early"]})");
        DockController c(o);
        auto shown = [&](const char *id) {
            for (const DockItem &i : c.model()->items()) if (i.key == QLatin1String(id)) return true;
            return false;
        };
        QVERIFY(!shown("early"));   // pinned, but nothing launchable by that name yet
        QVERIFY(QDir().mkpath(apps));
        put(apps + QStringLiteral("/early.desktop"), "[Desktop Entry]\nType=Application\nName=Early\nExec=early\n");
        QTRY_VERIFY_WITH_TIMEOUT(shown("early"), 5000);       // the entry appears: the dock follows new launcher entries
        // a pin written after start shows up, then an install of a pinned app in a subfolder
        put(o.configPath, R"({"pinned":["early","late"]})");
        QTest::qWait(500);
        QVERIFY(!shown("late"));
        put(apps + QStringLiteral("/late.desktop"), "[Desktop Entry]\nType=Application\nName=Late\nExec=late\n");
        QTRY_VERIFY_WITH_TIMEOUT(shown("late"), 5000);
        QFile::remove(apps + QStringLiteral("/early.desktop"));
        QTRY_VERIFY_WITH_TIMEOUT(!shown("early"), 5000);       // removal follows too
    }
    void followsLiveChange() {
        QTemporaryDir d;
        DockController::Options o;
        o.configPath = d.path() + QStringLiteral("/dock.json");
        o.modePath = d.path() + QStringLiteral("/desktop.json");
        o.appDirs = {d.path() + QStringLiteral("/none")};
        put(o.modePath, R"({"default":"desktop"})");
        DockController c(o);
        WindowInfo w;
        w.id = 1; w.appId = QStringLiteral("x"); w.workspace = QStringLiteral("1"); w.focused = true;
        c.model()->setWindows({w});
        QVERIFY(!c.effectiveAutohide());
        QSignalSpy spy(&c, &DockController::layoutChanged);
        put(o.modePath, R"({"default":"desktop","workspaces":{"1":"tiling"}})");
        QTRY_VERIFY(c.effectiveAutohide());
        QVERIFY(spy.count() >= 1);
        QCOMPARE(c.layout().exclusiveZone, 0);
        // the person keeps the dock visible while tiling
        QVERIFY(c.settings()->set(QStringLiteral("hideInTiling"), QStringLiteral("false")));
        QVERIFY(!c.effectiveAutohide());
        QVERIFY(c.layout().exclusiveZone > 0);
        // and the dock.json key round-trips
        DockConfig cfg;
        QVERIFY(DockConfigFile::read(o.configPath, &cfg));
        QVERIFY(!cfg.hideInTiling);
        QVERIFY(DockConfigFile::read(o.configPath, &cfg));
    }
};
QTEST_MAIN(ModeFollow)
#include "test_modefollow.moc"
