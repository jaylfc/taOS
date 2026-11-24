// SPDX-License-Identifier: AGPL-3.0-or-later
#include <QtTest>
#include <memory>

#include "dockmodel.h"
#include "swayengine.h"

static DesktopEntry entry(const char *id, const char *name, const char *wm = "", bool single = false, bool newWin = false) {
    DesktopEntry e;
    e.id = QString::fromLatin1(id); e.name = QString::fromLatin1(name); e.exec = e.id; e.icon = e.id;
    e.startupWMClass = QString::fromLatin1(wm); e.singleMainWindow = single;
    if (newWin) e.actions.append({QStringLiteral("new-window"), QStringLiteral("New Window"), e.id + QStringLiteral(" --new")});
    return e;
}
static WindowInfo win(int id, const char *app, const char *title, bool focused = false, bool min = false) {
    WindowInfo w;
    w.id = id; w.appId = QString::fromLatin1(app); w.title = QString::fromLatin1(title); w.focused = focused; w.minimised = min; w.workspace = QStringLiteral("1");
    return w;
}

class DockModelTest : public QObject {
    Q_OBJECT
    DesktopIndex idx;
    std::unique_ptr<DockModel> mp;
    QStringList keys() { QStringList k; for (const DockItem &i : mp->items()) k << i.key; return k; }
private slots:
    void init() {
        idx = DesktopIndex();
        idx.add(entry("files", "Files"));
        idx.add(entry("foot", "Terminal"));
        idx.add(entry("act", "Activity", "act.app", true));
        idx.add(entry("browser", "Browser", "", false, true));
        mp.reset(new DockModel);
        mp->setIndex(&idx);
        mp->setPinned({QStringLiteral("files"), QStringLiteral("foot"), QStringLiteral("act"), QStringLiteral("gone")});   // "gone" is not installed
    }
    void orderingPinnedThenRunningThenMinimised() {
        mp->setWindows({win(4, "browser", "docs"), win(2, "foot", "sh", true), win(9, "mystery", "x"), win(3, "browser", "min", false, true), win(7, "foot", "min2", false, true)});
        QCOMPARE(keys(), (QStringList{QStringLiteral("files"), QStringLiteral("foot"), QStringLiteral("act"),
                                      QStringLiteral("browser"), QStringLiteral("app:mystery"),
                                      QStringLiteral("separator"), QStringLiteral("win:3"), QStringLiteral("win:7")}));
        QVERIFY(mp->items()[1].running && mp->items()[1].focused && mp->items()[1].pinned);
        QVERIFY(!mp->items()[0].running);
        QCOMPARE(mp->items()[1].windows.size(), 2);             // minimised windows count for their app
        QCOMPARE(mp->items()[1].minimisedCount, 1);
        QCOMPARE(mp->items()[6].kind, DockItem::Minimised);
        QCOMPARE(mp->items()[6].title, QStringLiteral("min"));
        QCOMPARE(mp->pinnedCount(), 3);                          // the uninstalled pin is hidden, not lost
        QVERIFY(mp->pinnedList().contains(QStringLiteral("gone")));
    }
    void noSeparatorWithoutMinimisedWindows() {
        mp->setWindows({win(2, "foot", "sh", true)});
        QVERIFY(!keys().contains(QStringLiteral("separator")));
    }
    void accessibleNames() {
        mp->setWindows({win(1, "act.app", "A", true), win(2, "act.app", "B"), win(3, "foot", "t", false, true)});
        QCOMPARE(mp->data(mp->index(2), DockModel::AccessibleRole).toString(), QStringLiteral("Activity, running, 2 windows, focused"));
        QCOMPARE(mp->data(mp->index(0), DockModel::AccessibleRole).toString(), QStringLiteral("Files, not running"));
        QVERIFY(mp->data(mp->index(1), DockModel::AccessibleRole).toString().startsWith(QStringLiteral("Terminal, running, 1 window")));
        QCOMPARE(mp->items().last().kind, DockItem::Minimised);
        QVERIFY(DockModel::accessibleName(mp->items().last()).contains(QStringLiteral("minimised")));
    }
    void clickNotRunningLaunches() {
        mp->setWindows({});
        const DockAction a = mp->primaryAction(0);
        QCOMPARE(a.type, DockAction::Launch);
        QCOMPARE(a.desktopId, QStringLiteral("files"));
        mp->setLaunching(QStringLiteral("files"), true);
        QVERIFY(mp->items()[0].launching);
        QCOMPARE(mp->primaryAction(0).type, DockAction::None);   // already starting: no second launch
        mp->setWindows({win(1, "files", "Files")});              // the window appears: the pulse ends
        QVERIFY(!mp->items()[0].launching);
        QVERIFY(!mp->launching(QStringLiteral("files")));
    }
    void clickRunningFocusesThenMinimisesThenRestores() {
        mp->setWindows({win(5, "foot", "sh")});                  // not focused
        DockAction a = mp->primaryAction(1);
        QCOMPARE(a.type, DockAction::Focus);
        QCOMPARE(a.windowId, 5);
        mp->setWindows({win(5, "foot", "sh", true)});            // focused now: toggle
        a = mp->primaryAction(1);
        QCOMPARE(a.type, DockAction::Minimise);
        QCOMPARE(a.windowId, 5);
        mp->setWindows({win(5, "foot", "sh", false, true)});     // minimised: click restores
        a = mp->primaryAction(1);
        QCOMPARE(a.type, DockAction::Restore);
        QCOMPARE(a.windowId, 5);
        // the minimised item in the separator section restores too
        QCOMPARE(mp->primaryAction(mp->rowForKey(QStringLiteral("win:5"))).type, DockAction::Restore);
    }
    void clickPicksTheMostRecentWindow() {
        mp->setWindows({win(5, "foot", "a", true), win(6, "foot", "b")});
        mp->setWindows({win(5, "foot", "a"), win(6, "foot", "b", true)});   // 6 had focus last
        mp->setWindows({win(5, "foot", "a"), win(6, "foot", "b"), win(9, "files", "f", true)});   // focus is elsewhere now
        const DockAction a = mp->primaryAction(1);
        QCOMPARE(a.type, DockAction::Focus);
        QCOMPARE(a.windowId, 6);
    }
    void clickCyclesSeveralWindows() {
        mp->setWindows({win(5, "foot", "a", true), win(6, "foot", "b"), win(8, "foot", "c")});
        QCOMPARE(mp->primaryAction(1).windowId, 6);
        mp->setWindows({win(5, "foot", "a"), win(6, "foot", "b", true), win(8, "foot", "c")});
        QCOMPARE(mp->primaryAction(1).windowId, 8);
        mp->setWindows({win(5, "foot", "a"), win(6, "foot", "b"), win(8, "foot", "c", true)});
        QCOMPARE(mp->primaryAction(1).windowId, 5);              // wraps
        QCOMPARE(mp->primaryAction(1).type, DockAction::Focus);
        // cycling onto a minimised window restores it
        mp->setWindows({win(5, "foot", "a", false, true), win(6, "foot", "b"), win(8, "foot", "c", true)});
        QCOMPARE(mp->primaryAction(1).type, DockAction::Restore);
        QCOMPARE(mp->primaryAction(1).windowId, 5);
    }
    void toggleMinimiseOnlyForAFocusedSingleWindow() {
        mp->setWindows({win(5, "foot", "a", true), win(6, "foot", "b")});
        QVERIFY(mp->primaryAction(1).type != DockAction::Minimise);
    }
    void middleClick() {
        mp->setWindows({win(1, "browser", "b", true), win(2, "act.app", "a")});
        QCOMPARE(mp->middleAction(mp->rowForKey(QStringLiteral("browser"))).type, DockAction::NewWindow);
        QCOMPARE(mp->middleAction(mp->rowForKey(QStringLiteral("act"))).type, DockAction::None);   // single window app
        QCOMPARE(mp->middleAction(mp->rowForKey(QStringLiteral("files"))).type, DockAction::Launch);   // not running
    }
    void menus() {
        mp->setWindows({win(1, "browser", "docs", true), win(2, "browser", "mail", false, true)});
        QStringList ids;
        for (const DockMenuRow &r : mp->menuFor(mp->rowForKey(QStringLiteral("browser")))) if (r.kind == DockMenuRow::Entry) ids << r.id;
        QCOMPARE(ids, (QStringList{QStringLiteral("window:1"), QStringLiteral("window:2"), QStringLiteral("minimise-all"), QStringLiteral("new-window"),
                                   QStringLiteral("pin"), QStringLiteral("quit"), QStringLiteral("settings")}));
        ids.clear();
        for (const DockMenuRow &r : mp->menuFor(mp->rowForKey(QStringLiteral("foot")))) if (r.kind == DockMenuRow::Entry) ids << r.id;
        QCOMPARE(ids, (QStringList{QStringLiteral("unpin"), QStringLiteral("settings")}));   // pinned, not running: no Quit, no windows
        ids.clear();
        for (const DockMenuRow &r : mp->menuFor(mp->rowForKey(QStringLiteral("win:2")))) if (r.kind == DockMenuRow::Entry) ids << r.id;
        QCOMPARE(ids, (QStringList{QStringLiteral("restore"), QStringLiteral("close"), QStringLiteral("settings")}));
        QCOMPARE(mp->menuFor(-1).size(), 1);
        QCOMPARE(mp->windowListFor(mp->rowForKey(QStringLiteral("browser"))).size(), 2);
    }
    void singleWindowActionsAtTheTopOfTheMenu() {
        mp->setWindows({win(5, "act.app", "Activity", true)});
        auto rows = mp->menuFor(mp->rowForKey(QStringLiteral("act")));
        QCOMPARE(rows[0].id, QStringLiteral("window-min:5"));
        QCOMPARE(rows[0].text, QStringLiteral("Minimise"));
        QCOMPARE(rows[0].accessible, QStringLiteral("Minimise Activity"));
        QCOMPARE(rows[1].id, QStringLiteral("window-full:5"));
        QCOMPARE(rows[1].text, QStringLiteral("Full size"));
        QCOMPARE(rows[2].id, QStringLiteral("window-close:5"));
        QCOMPARE(rows[2].accessible, QStringLiteral("Close Activity"));
        for (const DockMenuRow &r : rows) QVERIFY(r.id != QLatin1String("quit"));   // one window: Close is Quit
        // minimised: Restore, and full size has to wait for the restore
        mp->setWindows({win(5, "act.app", "Activity", false, true)});
        rows = mp->menuFor(mp->rowForKey(QStringLiteral("act")));
        QCOMPARE(rows[0].text, QStringLiteral("Restore"));
        QCOMPARE(rows[0].accessible, QStringLiteral("Restore Activity"));
        QVERIFY(!rows[1].enabled);
        // full size (or fullscreen): Restore size
        WindowInfo big = win(5, "act.app", "Activity", true);
        big.fullSize = true;
        mp->setWindows({big});
        QCOMPARE(mp->menuFor(mp->rowForKey(QStringLiteral("act")))[1].text, QStringLiteral("Restore size"));
        big.fullSize = false; big.fullscreen = true;
        mp->setWindows({big});
        QCOMPARE(mp->menuFor(mp->rowForKey(QStringLiteral("act")))[1].text, QStringLiteral("Restore size"));
    }
    void severalWindowsGetInlineActions() {
        WindowInfo b = win(6, "foot", "build", false, true);
        WindowInfo a = win(5, "foot", "shell", true);
        a.fullSize = true;
        mp->setWindows({a, b});
        const auto rows = mp->menuFor(mp->rowForKey(QStringLiteral("foot")));
        QCOMPARE(rows[0].id, QStringLiteral("window:5"));
        QCOMPARE(rows[0].subs.size(), 3);
        QCOMPARE(rows[0].subs[0].glyph, QStringLiteral("min"));
        QCOMPARE(rows[0].subs[0].accessible, QStringLiteral("Minimise shell"));
        QCOMPARE(rows[0].subs[1].glyph, QStringLiteral("unfull"));
        QCOMPARE(rows[0].subs[1].accessible, QStringLiteral("Restore size of shell"));
        QCOMPARE(rows[0].subs[2].id, QStringLiteral("window-close:5"));
        QCOMPARE(rows[1].subs[0].glyph, QStringLiteral("restore"));          // the minimised one offers Restore
        QCOMPARE(rows[1].subs[0].accessible, QStringLiteral("Restore build"));
        QVERIFY(!rows[1].subs[1].enabled);
        QCOMPARE(rows[1].accessible, QStringLiteral("Restore build"));
        bool all = false, closeAll = false;
        for (const DockMenuRow &r : rows) { all |= r.id == QLatin1String("minimise-all") && r.enabled; closeAll |= r.id == QLatin1String("quit"); }
        QVERIFY(all && closeAll);
    }
    void pinnedListEdits() {
        QCOMPARE(mp->pinnedWith(QStringLiteral("browser")).last(), QStringLiteral("browser"));
        QVERIFY(!mp->pinnedWithout(QStringLiteral("foot")).contains(QStringLiteral("foot")));
        const QStringList moved = mp->pinnedAfterMove(0, 2);
        QCOMPARE(moved.mid(0, 3), (QStringList{QStringLiteral("foot"), QStringLiteral("act"), QStringLiteral("files")}));
        QVERIFY(moved.contains(QStringLiteral("gone")));
    }
    // A dock that starts (or restarts) knows nothing; the compositor's tree is the whole truth.
    void rebuildFromTheCompositorAfterRestart() {
        const QByteArray tree = R"({"type":"root","nodes":[
          {"type":"output","name":"HEADLESS-1","nodes":[{"type":"workspace","name":"1","nodes":[
             {"type":"con","id":11,"app_id":"foot","name":"sh","pid":5,"focused":true,"marks":[],"nodes":[],"floating_nodes":[]}],
           "floating_nodes":[]}]},
          {"type":"output","name":"__i3","nodes":[{"type":"workspace","name":"__i3_scratch","nodes":[],"floating_nodes":[
             {"type":"floating_con","id":12,"app_id":"browser","name":"docs","pid":6,"focused":false,"marks":["taos_min_12"],"nodes":[],"floating_nodes":[]},
             {"type":"floating_con","id":13,"app_id":"browser","name":"stray scratchpad","pid":7,"focused":false,"marks":[],"nodes":[],"floating_nodes":[]}]}]}]})";
        QVector<WindowInfo> w;
        QVERIFY(WindowJson::parseSwayTree(tree, &w));
        QCOMPARE(w.size(), 3);
        QVERIFY(w[1].minimised);
        QVERIFY(!w[2].minimised);                              // in the scratchpad but not marked by us: not minimised
        DockModel first, second;                               // "before" and "after the crash"
        first.setIndex(&idx); first.setPinned({QStringLiteral("files"), QStringLiteral("foot")}); first.setWindows(w);
        second.setIndex(&idx); second.setPinned({QStringLiteral("files"), QStringLiteral("foot")}); second.setWindows(w);
        QCOMPARE(first.items().size(), second.items().size());
        QStringList a, b;
        for (const DockItem &i : first.items()) a << i.key;
        for (const DockItem &i : second.items()) b << i.key;
        QCOMPARE(a, b);
        QVERIFY(a.contains(QStringLiteral("win:12")));         // the minimised window is back, from marks alone
        QVERIFY(!a.contains(QStringLiteral("win:13")));
        // the same list through the engine contract's JSON
        QVector<WindowInfo> viaContract;
        QVERIFY(WindowJson::parseList(WindowJson::toJson(w), &viaContract));
        QVERIFY(viaContract == w);
    }
    void contractJsonParses() {
        QVector<WindowInfo> w;
        QVERIFY(WindowJson::parseList(R"([{"id":3,"app_id":"foot","title":"t","pid":9,"workspace":"2","focused":true,"minimised":true,
            "floating":false,"fullscreen":false,"output":"DP-1"}])", &w));
        QCOMPARE(w.size(), 1);
        QVERIFY(w[0].minimised && w[0].focused);
        QCOMPARE(w[0].workspace, QStringLiteral("2"));
        QVERIFY(!WindowJson::parseList("{}", &w));
    }
    void swayCommands() {
        QCOMPARE(SwayEngine::minimiseCommand(7, false), QStringLiteral("[con_id=7] mark --add taos_min_7; [con_id=7] move scratchpad"));
        QVERIFY(SwayEngine::minimiseCommand(7, true).contains(QStringLiteral("taos_float_7")));
        QVERIFY(SwayEngine::restoreCommand(7, false).contains(QStringLiteral("floating disable")));
        QVERIFY(!SwayEngine::restoreCommand(7, true).contains(QStringLiteral("floating disable")));
    }
};
QTEST_MAIN(DockModelTest)
#include "test_dockmodel.moc"
