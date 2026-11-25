// SPDX-License-Identifier: AGPL-3.0-or-later
// The menu's window actions reach the real engine's command line: taos-window minimise|restore|full|close <id>.
#include <QFile>
#include <QTemporaryDir>
#include <QtTest>

#include "dockcontroller.h"

class DockMenuTest : public QObject {
    Q_OBJECT
    QTemporaryDir tmp;
    QString log() const { return tmp.path() + QStringLiteral("/calls.log"); }
    QStringList calls() {
        QFile f(log());
        if (!f.open(QIODevice::ReadOnly)) return {};
        return QString::fromUtf8(f.readAll()).split(QLatin1Char('\n'), Qt::SkipEmptyParts);
    }
    QString lastCall() { const QStringList c = calls(); return c.isEmpty() ? QString() : c.last(); }
    static WindowInfo win(int id, const char *app, const char *title, bool focused = false, bool min = false) {
        WindowInfo w; w.id = id; w.appId = QString::fromLatin1(app); w.title = QString::fromLatin1(title); w.focused = focused; w.minimised = min; w.workspace = QStringLiteral("1"); return w;
    }
    // The engine answers `list` from this file, so a refresh after a verb keeps the same windows.
    void setWindows(DockController *c, const QVector<WindowInfo> &w) {
        QFile f(tmp.path() + QStringLiteral("/windows.json"));
        QVERIFY(f.open(QIODevice::WriteOnly));
        f.write(WindowJson::toJson(w));
        f.close();
        c->model()->setWindows(w);
        c->taskbar()->setWindows(w);
    }
    DockController *make() {
        QFile s(tmp.path() + QStringLiteral("/taos-window"));
        s.open(QIODevice::WriteOnly);
        s.write("#!/bin/sh\n[ \"$1\" = list ] && { cat " + tmp.path().toLocal8Bit() + "/windows.json; exit 0; }\n[ \"$1\" = subscribe ] && { sleep 30; exit 0; }\n[ \"$1\" = spaces ] && { cat " + tmp.path().toLocal8Bit() + "/spaces.json 2>/dev/null || echo '[]'; exit 0; }\necho \"$@\" >> " + log().toLocal8Bit() + "\n");
        s.close();
        s.setPermissions(QFile::ReadOwner | QFile::WriteOwner | QFile::ExeOwner);
        DockController::Options o;
        o.configPath = tmp.path() + QStringLiteral("/dock.json");
        o.modePath = tmp.path() + QStringLiteral("/desktop.json");
        o.appDirs = {QStringLiteral(TAOS_DOCK_FIXTURES "/applications")};
        o.watch = false;
        o.engine = QStringLiteral("cli");
        o.cliProgram = s.fileName();
        return new DockController(o);
    }
    int menuIndex(DockController *c, const QString &id) {
        const QVariantList rows = c->menuRows();
        for (int i = 0; i < rows.size(); ++i) if (rows[i].toMap().value(QStringLiteral("id")).toString() == id) return i;
        return -1;
    }
private slots:
    void singleWindowActions() {
        QScopedPointer<DockController> c(make());
        setWindows(c.data(), {win(5, "my.taos.dashboard", "Activity", true)});
        const int row = c->model()->rowForKey(QStringLiteral("my.taos.dashboard"));
        c->openMenu(row, 10, 10);
        c->invokeMenu(menuIndex(c.data(), QStringLiteral("window-min:5")));
        QTRY_COMPARE(calls(), QStringList{QStringLiteral("minimise 5")});
        c->openMenu(row, 10, 10);
        c->invokeMenu(menuIndex(c.data(), QStringLiteral("window-full:5")));
        QTRY_COMPARE(lastCall(), QStringLiteral("maximise 5"));       // enters full size, never toggles
        c->openMenu(row, 10, 10);
        c->invokeMenu(menuIndex(c.data(), QStringLiteral("window-close:5")));
        QTRY_COMPARE(lastCall(), QStringLiteral("close 5"));
    }
    void minimisedWindowRestores() {
        QScopedPointer<DockController> c(make());
        setWindows(c.data(), {win(7, "my.taos.dashboard", "Activity", false, true)});
        c->openMenu(c->model()->rowForKey(QStringLiteral("my.taos.dashboard")), 0, 0);
        c->invokeMenu(menuIndex(c.data(), QStringLiteral("window-min:7")));
        QTRY_VERIFY(calls().contains(QStringLiteral("restore 7")));
    }
    void severalWindowsInlineAndAll() {
        QScopedPointer<DockController> c(make());
        setWindows(c.data(), {win(5, "foot", "a", true), win(6, "foot", "b"), win(8, "foot", "c", false, true)});
        const int row = c->model()->rowForKey(QStringLiteral("foot"));
        c->openMenu(row, 0, 0);
        const int w6 = menuIndex(c.data(), QStringLiteral("window:6"));
        QVERIFY(w6 >= 0);
        const QVariantList kids = c->menuRows()[w6].toMap().value(QStringLiteral("children")).toList();
        int closeAt = -1;
        for (int i = 0; i < kids.size(); ++i) if (kids[i].toMap().value(QStringLiteral("id")).toString() == QLatin1String("window-close:6")) closeAt = i;
        QVERIFY(closeAt >= 0);
        c->invokePath({w6, closeAt});   // close b
        QTRY_VERIFY(calls().contains(QStringLiteral("close 6")));
        c->openMenu(row, 0, 0);
        c->invokeMenu(menuIndex(c.data(), QStringLiteral("minimise-all")));
        QTRY_VERIFY(calls().contains(QStringLiteral("minimise 5")));
        QTRY_VERIFY(calls().contains(QStringLiteral("minimise 6")));
        QVERIFY(!calls().contains(QStringLiteral("minimise 8")));    // already minimised
        c->openMenu(row, 0, 0);
        c->invokeMenu(menuIndex(c.data(), QStringLiteral("quit")));
        QTRY_VERIFY(calls().contains(QStringLiteral("close 8")));
        QTRY_VERIFY(calls().contains(QStringLiteral("close 5")));
    }
    // Every control of the menu reaches the engine as the real command line, and nothing but the engine.
    void everyControlIsAnEngineCommand() {
        QScopedPointer<DockController> c(make());
        WindowInfo w = win(5, "my.taos.dashboard", "Activity", true);
        w.floating = true;
        setWindows(c.data(), {w});
        QFile sp(tmp.path() + QStringLiteral("/spaces.json"));
        QVERIFY(sp.open(QIODevice::WriteOnly));
        sp.write(R"([{"name":"1","output":"O","focused":true,"visible":true,"windows":1},{"name":"2","output":"O","focused":false,"visible":false,"windows":0}])");
        sp.close();
        const int row = c->model()->rowForKey(QStringLiteral("my.taos.dashboard"));
        QSignalSpy spaces(c->engine(), &WindowEngine::spacesChanged);
        c->openMenu(row, 10, 10);                                   // asks the engine for the spaces
        QTRY_VERIFY(spaces.count() >= 1);
        struct Case { const char *id; const char *call; };
        const Case cases[] = {{"window-min:5", "minimise 5"}, {"window-full:5", "maximise 5"}, {"window-fs:5", "fullscreen 5 on"},
                              {"window-bring:5", "bring-here 5"}, {"window-reset:5", "reset-size 5"}, {"window-close:5", "close 5"}};
        for (const Case &k : cases) {
            c->openMenu(row, 10, 10);
            const int i = menuIndex(c.data(), QString::fromLatin1(k.id));
            QVERIFY2(i >= 0, k.id);
            c->invokeMenu(i);
            QTRY_COMPARE_WITH_TIMEOUT(lastCall(), QString::fromLatin1(k.call), 3000);
        }
        // the spaces submenu: by path (row, child)
        c->openMenu(row, 10, 10);
        const int sub = menuIndex(c.data(), QStringLiteral("window-spaces:5"));
        QVERIFY(sub >= 0);
        const QVariantList kids = c->menuRows()[sub].toMap().value(QStringLiteral("children")).toList();
        QCOMPARE(kids.size(), 4);                                    // Space 1 (here), Space 2, separator, New space
        QVERIFY(!kids[0].toMap().value(QStringLiteral("enabled")).toBool());
        c->invokePath({sub, 0});                                     // where it already is: nothing happens
        c->openMenu(row, 10, 10);
        c->invokePath({sub, 1});
        QTRY_COMPARE_WITH_TIMEOUT(lastCall(), QStringLiteral("move-to-space 5 2"), 3000);
        c->openMenu(row, 10, 10);
        c->invokePath({sub, 3});
        QTRY_COMPARE_WITH_TIMEOUT(lastCall(), QStringLiteral("new-space 5"), 3000);
        QVERIFY(!calls().contains(QStringLiteral("move-to-space 5 1")));
    }
    void backAndOutAgain() {
        QScopedPointer<DockController> c(make());
        WindowInfo big = win(5, "my.taos.dashboard", "Activity", true);
        big.floating = true; big.fullSize = true;
        setWindows(c.data(), {big});
        const int row = c->model()->rowForKey(QStringLiteral("my.taos.dashboard"));
        c->openMenu(row, 10, 10);
        c->invokeMenu(menuIndex(c.data(), QStringLiteral("window-full:5")));
        QTRY_COMPARE(lastCall(), QStringLiteral("restore 5"));        // Restore size is the engine's restore
        WindowInfo fs = win(5, "my.taos.dashboard", "Activity", true);
        fs.floating = true; fs.fullscreen = true;
        setWindows(c.data(), {fs});
        c->openMenu(row, 10, 10);
        c->invokeMenu(menuIndex(c.data(), QStringLiteral("window-fs:5")));
        QTRY_COMPARE(lastCall(), QStringLiteral("fullscreen 5 off"));
    }
    void severalWindowsControlsByPath() {
        QScopedPointer<DockController> c(make());
        WindowInfo a = win(5, "foot", "a", true), b = win(6, "foot", "b");
        a.floating = b.floating = true;
        setWindows(c.data(), {a, b});
        const int row = c->model()->rowForKey(QStringLiteral("foot"));
        c->openMenu(row, 0, 0);
        const int w6 = menuIndex(c.data(), QStringLiteral("window:6"));
        const QVariantList kids = c->menuRows()[w6].toMap().value(QStringLiteral("children")).toList();
        int bring = -1;
        for (int i = 0; i < kids.size(); ++i) if (kids[i].toMap().value(QStringLiteral("id")).toString() == QLatin1String("window-bring:6")) bring = i;
        QVERIFY(bring >= 0);
        c->invokePath({w6, bring});
        QTRY_COMPARE(lastCall(), QStringLiteral("bring-here 6"));
        c->openMenu(row, 0, 0);
        c->invokeMenu(w6);                                          // the title still focuses
        QTRY_COMPARE(lastCall(), QStringLiteral("focus 6"));
    }
    // The Workbench taskbar button's menu is the same list.
    void taskbarMenuHasTheSameControls() {
        QScopedPointer<DockController> c(make());
        WindowInfo w = win(5, "my.taos.dashboard", "Activity", true);
        w.floating = true;
        setWindows(c.data(), {w});
        c->taskbarMenu(5, 20, 20);
        QStringList ids;
        for (const QVariant &v : c->menuRows()) ids << v.toMap().value(QStringLiteral("id")).toString();
        for (const char *want : {"window-min:5", "window-full:5", "window-fs:5", "window-bring:5", "window-reset:5", "window-spaces:5", "window-close:5"})
            QVERIFY2(ids.contains(QString::fromLatin1(want)), want);
        c->invokeMenu(menuIndex(c.data(), QStringLiteral("window-bring:5")));
        QTRY_COMPARE(lastCall(), QStringLiteral("bring-here 5"));
        c->taskbarMenu(5, 20, 20);
        c->invokeMenu(menuIndex(c.data(), QStringLiteral("window-fs:5")));
        QTRY_COMPARE(lastCall(), QStringLiteral("fullscreen 5 on"));
        c->taskbarMenu(5, 20, 20);
        c->invokeMenu(menuIndex(c.data(), QStringLiteral("window-close:5")));
        QTRY_COMPARE(lastCall(), QStringLiteral("close 5"));
        // minimised: the first row restores
        w.minimised = true;
        setWindows(c.data(), {w});
        c->taskbarMenu(5, 20, 20);
        c->invokeMenu(menuIndex(c.data(), QStringLiteral("window-min:5")));
        QTRY_COMPARE(lastCall(), QStringLiteral("restore 5"));
    }
};
QTEST_MAIN(DockMenuTest)
#include "test_dockmenu.moc"
