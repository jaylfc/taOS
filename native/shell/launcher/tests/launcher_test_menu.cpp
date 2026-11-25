// SPDX-License-Identifier: AGPL-3.0-or-later
// The app menu as the launcher's controller drives it: the real Controller, the real MenuController, the real
// models, the keyboard calls the QML makes. Nothing here starts a real application: the fixture apps run `true`.
#include <QGuiApplication>
#include <QJsonArray>
#include <QJsonDocument>
#include <QtTest>

#include "controller.h"
#include "menucontroller.h"

using namespace taos::search;
using namespace taos::launcher;

class TestMenu : public QObject {
    Q_OBJECT
    QTemporaryDir m_tmp;
    SearchEngine m_engine;
    std::shared_ptr<Frecency> m_frec;
    std::shared_ptr<AppsProvider> m_prov;
    std::shared_ptr<AppMenu> m_menu;
    std::unique_ptr<Controller> m_c;
    QString m_apps, m_cfg;

    void put(const QString &id, const QString &name, const QString &cats, const QString &extra = {}) {
        QSaveFile f(m_apps + "/" + id + ".desktop");
        QVERIFY(f.open(QIODevice::WriteOnly));
        f.write(QString("[Desktop Entry]\nType=Application\nName=%1\nIcon=%2\nExec=true\nCategories=%3\n%4").arg(name, id, cats, extra).toUtf8());
        f.commit();
    }
    MenuController *mc() { return m_c->menuController(); }
    QString appAt(AppListModel &m, int i) { return m.data(m.index(i), AppListModel::IdRole).toString(); }
    QStringList idsOf(AppListModel &m) { QStringList l; for (int i = 0; i < m.count(); ++i) l << appAt(m, i); return l; }
    void waitFinal() { QTest::qWait(60); QTRY_VERIFY_WITH_TIMEOUT(!m_c->searching(), 2000); }
private slots:
    void init() {
        if (m_c) { m_c->hide(); m_c->closeFinished(); }
        m_c.reset();
        m_menu.reset();
        QDir(m_tmp.path()).removeRecursively();
        QDir().mkpath(m_tmp.path());
        m_apps = m_tmp.path() + "/apps";
        m_cfg = m_tmp.path() + "/cfg";
        QDir().mkpath(m_apps);
        QDir().mkpath(m_cfg);
        put("firefox", "Firefox", "Network;WebBrowser;");
        put("foot", "Terminal", "System;TerminalEmulator;");
        put("gimp", "GIMP", "Graphics;");
        put("vlc", "VLC media player", "AudioVideo;Player;");
        put("steam", "Steam", "Game;");
        put("my.taos.settings", "Settings", "Settings;System;", "X-taOS-AppId=my.taos.settings\n");
        put("taos-appgroups-blender", "Blender", "Graphics;", "X-taOS-ContainerApp=blender\nX-taOS-AppGroup=My apps\n");
        m_frec = std::make_shared<Frecency>(m_tmp.path() + "/f.json");
        m_prov = std::make_shared<AppsProvider>(m_frec, QStringList{ m_apps }, "C", QStringList{});
        m_engine.addProvider(m_prov);
        m_menu = std::make_shared<AppMenu>(m_prov, m_frec, AppMenu::Paths{ m_cfg + "/startmenu.json", m_cfg + "/dock.json" }, true);
        m_c = std::make_unique<Controller>(&m_engine, m_frec);
        m_c->setLaunchDelay(0);
        m_c->setMenu(m_menu, std::make_shared<ActionMenu>([](const QString &) { return true; }));
        m_c->show();
    }
    void cleanupTestCase() { if (m_c) { m_c->hide(); m_c->closeFinished(); } }

    void opensOnTheMenuNotOnSuggestions() {
        QVERIFY(m_c->menuMode());
        QVERIFY(m_c->menuVisible());
        QCOMPARE(m_c->count(), 0);                       // no suggestion rows over the menu
        QVERIFY(mc()->pinnedCount() >= 2);
        QVERIFY(mc()->allCount() >= 7);
        QCOMPARE(mc()->zone(), QString());                // nothing is selected until an arrow key
    }
    void typingTakesOverAndClearingBringsTheMenuBack() {
        m_c->setQuery("fire");
        waitFinal();
        QVERIFY(!m_c->menuVisible());
        QVERIFY(m_c->count() >= 1);
        m_c->setQuery("");
        QVERIFY(m_c->menuVisible());
        QCOMPARE(m_c->count(), 0);
    }
    void arrowsEnterLaunchAndRememberUse() {
        m_menu->pin("gimp");
        QSignalSpy hidden(m_c.get(), &Controller::openChanged);
        m_c->menuMove(0, 1);
        QCOMPARE(mc()->zone(), QString("pinned"));
        QCOMPARE(mc()->index(), 0);
        const QString first = appAt(mc()->pinnedList(), 0);
        m_c->activate();                                  // Enter while the menu shows
        QVERIFY(m_c->launching());
        QVERIFY(m_frec->boost("app:" + first) > 0);
        QTRY_VERIFY_WITH_TIMEOUT(!m_c->isOpen(), 2000);
    }
    void rightLeftWalkAGridAndDownLeavesIt() {
        mc()->setColumns("pinned", 3);
        m_c->menuMove(0, 1);
        m_c->menuMove(1, 0);
        QCOMPARE(mc()->index(), 1);
        m_c->menuMove(0, 1);                              // pinned has about 3 apps, one row: goes on to the next zone
        QVERIFY(mc()->zone() != "pinned");
    }
    void escapeClosesLayersBeforeTheLauncher() {
        m_c->menuMove(0, 1);
        m_c->menuContext();
        QVERIFY(mc()->contextOpen());
        m_c->escape();
        QVERIFY(!mc()->contextOpen());
        QVERIFY(m_c->isOpen());
        mc()->openPower();
        QVERIFY(mc()->powerOpen());
        m_c->escape();
        QVERIFY(!mc()->powerOpen());
        QVERIFY(m_c->isOpen());
        m_c->escape();
        QVERIFY(!m_c->isOpen());
    }
    void contextMenuPinsAndUnpinsWithoutClosing() {
        // right click on GIMP in the All apps list
        int at = -1;
        for (int i = 0; i < mc()->allList().count(); ++i) if (appAt(mc()->allList(), i) == "gimp.desktop") at = i;
        QVERIFY(at >= 0);
        QVERIFY(!idsOf(mc()->pinnedList()).contains("gimp.desktop"));
        mc()->openContextFor("all", at);
        QVERIFY(mc()->contextOpen());
        const QStringList labels = mc()->contextLabels();
        QCOMPARE(labels.first(), QString("Open"));
        QVERIFY(labels.contains("Pin to menu"));
        QVERIFY(labels.contains("App info"));
        QVERIFY(!labels.contains("Uninstall"));
        mc()->runContext(labels.indexOf("Pin to menu"));
        QVERIFY(m_c->isOpen());                           // stays open
        QVERIFY(idsOf(mc()->pinnedList()).contains("gimp.desktop"));
        QVERIFY(m_menu->isPinned("gimp"));
        mc()->openContextFor("pinned", idsOf(mc()->pinnedList()).indexOf("gimp.desktop"));
        mc()->runContext(mc()->contextLabels().indexOf("Unpin from menu"));
        QVERIFY(!idsOf(mc()->pinnedList()).contains("gimp.desktop"));
    }
    void appInfoShowsAndEscapeCloses() {
        mc()->openContextFor("all", idsOf(mc()->allList()).indexOf("taos-appgroups-blender.desktop"));
        const QStringList l = mc()->contextLabels();
        QVERIFY(l.contains("Uninstall"));
        QVERIFY(l.contains("Move to group"));
        mc()->runContext(l.indexOf("App info"));
        QVERIFY(mc()->infoOpen());
        QCOMPARE(mc()->info().value("name").toString(), QString("Blender"));
        QCOMPARE(mc()->info().value("group").toString(), QString("My apps"));
        QCOMPARE(mc()->info().value("kind").toString(), QString("App group app"));
        m_c->escape();
        QVERIFY(!mc()->infoOpen());
    }
    void uninstallAsksTwiceAndDoesNotRunTheFirstTime() {
        mc()->openContextFor("all", idsOf(mc()->allList()).indexOf("taos-appgroups-blender.desktop"));
        const int u = mc()->contextLabels().indexOf("Uninstall");
        mc()->runContext(u);
        QCOMPARE(mc()->confirmId(), QString("ctx:uninstall"));
        QVERIFY(mc()->contextOpen());                     // still waiting: nothing ran
        QVERIFY(m_c->isOpen() && !m_c->launching());
    }
    void powerNeedsASecondEnter() {
        mc()->openPower();
        auto *pm = qobject_cast<ActionListModel *>(mc()->powerModel());
        QVERIFY(pm->count() >= 4);
        QStringList titles;
        for (int i = 0; i < pm->count(); ++i) titles << pm->data(pm->index(i, 0), ActionListModel::TitleRole).toString();
        QVERIFY(titles.contains("Shut down") && titles.contains("Restart") && titles.contains("Sign out"));
        mc()->runPower(titles.indexOf("Shut down"));
        QCOMPARE(mc()->confirmId(), QString("system/shutdown"));
        QVERIFY(!m_c->launching());                       // nothing ran
        m_c->escape();                                    // Esc cancels the confirmation first
        QVERIFY(mc()->confirmId().isEmpty());
        QVERIFY(mc()->powerOpen());
    }
    void footerHasSettingsFilesActionsPower() {
        const QStringList f = mc()->footerIds();
        QVERIFY(f.contains("settings")); QVERIFY(f.contains("actions")); QVERIFY(f.contains("power"));
        QVERIFY(!mc()->userName().isEmpty());
    }
    void sortTogglesBetweenCategoriesAndLetters() {
        QCOMPARE(mc()->sort(), QString("category"));
        QCOMPARE(mc()->allList().data(mc()->allList().index(0), AppListModel::SectionRole).toString(), QString("Internet"));
        mc()->toggleSort();
        QCOMPARE(mc()->allList().data(mc()->allList().index(0), AppListModel::SectionRole).toString(), QString("B"));   // Blender
        QVERIFY(mc()->allList().data(mc()->allList().index(0), AppListModel::FirstInSectionRole).toBool());
    }
    void twoPaneLayoutHopsBetweenListAndPinned() {
        mc()->setLayout("anchored");
        mc()->setColumns("pinned", 3);
        QVERIFY(mc()->nav().select("all", 0));
        QVERIFY(mc()->move(1, 0));
        QCOMPARE(mc()->zone(), QString("pinned"));
        QVERIFY(mc()->move(-1, 0));
        QCOMPARE(mc()->zone(), QString("all"));
        // the anchored list leads with what you use
        for (int i = 0; i < 3; ++i) m_frec->record("app:steam.desktop");
        m_menu->refresh();
        QCOMPARE(mc()->allList().data(mc()->allList().index(0), AppListModel::SectionRole).toString(), QString("Recent"));
    }

    // ---- the system action menu
    void gtOpensActionsAndTypingFilters() {
        m_c->setQuery(">");
        QVERIFY(m_c->actionsMode());
        QVERIFY(mc()->actionsMode());
        QCOMPARE(mc()->actionList().count(), 6);
        QCOMPARE(m_c->count(), 0);
        m_c->setQuery(">sys");
        QCOMPARE(mc()->actionList().data(mc()->actionList().index(0), ActionListModel::TitleRole).toString(), QString("System"));
        m_c->setQuery(">");
        m_c->setQuery("");
        QVERIFY(!mc()->actionsMode());
    }
    void drillDownAndBackUp() {
        m_c->setQuery(">sys");
        m_c->menuEnter();                                  // Enter on the System group
        QCOMPARE(mc()->actionPath(), QStringList{ "system" });
        QCOMPARE(mc()->actionTitle(), QString("System"));
        QVERIFY(mc()->actionList().count() >= 4);
        QVERIFY(m_c->menuBack());                          // Backspace on the empty filter, or Escape
        QVERIFY(mc()->actionPath().isEmpty());
        QVERIFY(!m_c->menuBack());                         // nothing above the top
        m_c->menuEnter();
        m_c->escape();                                     // Escape goes up first
        QVERIFY(mc()->actionPath().isEmpty());
        QVERIFY(m_c->isOpen());
    }
    void powerLeafInTheActionMenuAsksTwice() {
        m_c->setQuery(">sys");
        m_c->menuEnter();
        m_c->setQuery(">shut");
        QCOMPARE(mc()->actionList().data(mc()->actionList().index(0), ActionListModel::TitleRole).toString(), QString("Shut down"));
        m_c->menuEnter();
        QCOMPARE(mc()->confirmId(), QString("system/shutdown"));
        QVERIFY(!m_c->launching());
    }

    // ---- agents: the same things over the launcher's IPC
    void ipcMenuPinsAndActions() {
        const QString full = m_c->handleCommand({ "menu" });
        QVERIFY(full.startsWith("ok {"));
        const QJsonObject o = QJsonDocument::fromJson(full.mid(3).toUtf8()).object();
        QVERIFY(o.value("categories").toArray().size() >= 4);
        QVERIFY(m_c->handleCommand({ "menu", "nonsense" }).startsWith("error:"));
        QCOMPARE(m_c->handleCommand({ "pin", "steam" }), QString("ok"));
        QVERIFY(m_menu->isPinned("steam"));
        QVERIFY(m_c->handleCommand({ "pin", "nope" }).startsWith("error: no application"));
        QCOMPARE(m_c->handleCommand({ "unpin", "steam" }), QString("ok"));
        QVERIFY(m_c->handleCommand({ "unpin", "steam" }).startsWith("error:"));
        QCOMPARE(m_c->handleCommand({ "move-pin", "foot", "0" }), QString("ok"));
        QCOMPARE(mc()->pinnedList().data(mc()->pinnedList().index(0), AppListModel::IdRole).toString(), QString("foot.desktop"));
        const QString acts = m_c->handleCommand({ "actions", "system" });
        QVERIFY(acts.contains("Shut down"));
        const QString nope = m_c->handleCommand({ "run", "system/shutdown" });
        QVERIFY2(nope.startsWith("error:") && nope.contains("confirm"), qPrintable(nope));   // an agent must say so explicitly
        QVERIFY(m_c->handleCommand({ "run", "style" }).startsWith("error:"));               // a group is not an action
    }
    void livePinsAndNewAppsReachTheModels() {
        const int before = mc()->allCount();
        put("newtool", "Aardvark Tool", "Development;");
        QTRY_VERIFY_WITH_TIMEOUT(mc()->allCount() == before + 1, 3000);
        QVERIFY(idsOf(mc()->allList()).contains("newtool.desktop"));
    }
};

int main(int argc, char **argv) {
    qputenv("QT_QPA_PLATFORM", "offscreen");
    qputenv("XDG_CONFIG_HOME", "/nonexistent-menu-test");
    QGuiApplication app(argc, argv);
    qRegisterMetaType<QList<taos::search::Row>>();
    TestMenu t;
    return QTest::qExec(&t, argc, argv);
}
#include "launcher_test_menu.moc"
