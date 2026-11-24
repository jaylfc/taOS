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
    }
    DockController *make() {
        QFile s(tmp.path() + QStringLiteral("/taos-window"));
        s.open(QIODevice::WriteOnly);
        s.write("#!/bin/sh\n[ \"$1\" = list ] && { cat " + tmp.path().toLocal8Bit() + "/windows.json; exit 0; }\n[ \"$1\" = subscribe ] && { sleep 30; exit 0; }\necho \"$@\" >> " + log().toLocal8Bit() + "\n");
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
        QTRY_COMPARE(calls().last(), QStringLiteral("full 5"));
        c->openMenu(row, 10, 10);
        c->invokeMenu(menuIndex(c.data(), QStringLiteral("window-close:5")));
        QTRY_COMPARE(calls().last(), QStringLiteral("close 5"));
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
        QCOMPARE(c->menuRows()[w6].toMap().value(QStringLiteral("subs")).toList().size(), 3);
        c->invokeSub(w6, 2);   // close b
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
};
QTEST_MAIN(DockMenuTest)
#include "test_dockmenu.moc"
