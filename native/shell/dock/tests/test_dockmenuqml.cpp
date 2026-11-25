// SPDX-License-Identifier: AGPL-3.0-or-later
// The menu overlay itself (DockMenu.qml) driven by the keyboard and the pointer, with the fake `taos-window` recording
// what reaches the engine: Up and Down, Right and Left for submenus, Enter, Escape (innermost first), the Workbench
// taskbar button's menu, and the submenu that opens beside its row.
#include <QFile>
#include <QGuiApplication>
#include <QQmlEngine>
#include <QQuickItem>
#include <QQuickView>
#include <QSignalSpy>
#include <QTemporaryDir>
#include <QtQml>
#include <QtTest>

#include "dockcontroller.h"
#include "theme.h"
#include "thememanager.h"

class DockMenuQml : public QObject {
    Q_OBJECT
    QTemporaryDir tmp;
    Theme *theme = nullptr;
    ThemeManager *themes = nullptr;
    DockController *dock = nullptr;
    QQmlEngine *engine = nullptr;
    QQuickView *view = nullptr;

    QStringList calls() {
        QFile f(tmp.path() + QStringLiteral("/calls.log"));
        if (!f.open(QIODevice::ReadOnly)) return {};
        return QString::fromUtf8(f.readAll()).split(QLatin1Char('\n'), Qt::SkipEmptyParts);
    }
    QString lastCall() { const QStringList c = calls(); return c.isEmpty() ? QString() : c.last(); }
    static WindowInfo win(int id, const char *app, const char *title, bool focused = false, bool min = false) {
        WindowInfo w; w.id = id; w.appId = QString::fromLatin1(app); w.title = QString::fromLatin1(title); w.focused = focused; w.minimised = min; w.workspace = QStringLiteral("1"); w.floating = true; return w;
    }
    void setWindows(const QVector<WindowInfo> &w) {
        QFile f(tmp.path() + QStringLiteral("/windows.json"));
        QVERIFY(f.open(QIODevice::WriteOnly | QIODevice::Truncate));
        f.write(WindowJson::toJson(w));
        f.close();
        dock->model()->setWindows(w);
        dock->taskbar()->setWindows(w);
    }
    QVariantList cur() {
        if (!view->rootObject()) return {QStringLiteral("NO OVERLAY")};
        QVariant v = view->rootObject()->property("cur");
        if (v.metaType() == QMetaType::fromType<QJSValue>()) v = v.value<QJSValue>().toVariant();
        return v.toList();
    }
    void key(Qt::Key k) { QTest::keyClick(view, k); }
    int rowOf(const QString &id) {
        const QVariantList rows = dock->menuRows();
        for (int i = 0; i < rows.size(); ++i) if (rows[i].toMap().value(QStringLiteral("id")).toString() == id) return i;
        return -1;
    }
    void open(int row) {                       // a fresh overlay for a freshly opened menu
        view->setSource(QUrl());
        dock->openMenu(row, 400, 300);
        view->setSource(QUrl::fromLocalFile(QStringLiteral(TAOS_DOCK_QML_DIR "/DockMenu.qml")));
        if (view->status() != QQuickView::Ready) { for (const auto &e : view->errors()) qWarning("QML: %s", qPrintable(e.toString())); return; }
        view->rootObject()->setProperty("anchorX", 400);
        view->rootObject()->setProperty("anchorY", 600);
        view->rootObject()->forceActiveFocus();
        QTest::qWait(50);
    }
private slots:
    void initTestCase() {
        QFile s(tmp.path() + QStringLiteral("/taos-window"));
        QVERIFY(s.open(QIODevice::WriteOnly));
        s.write("#!/bin/sh\n[ \"$1\" = list ] && { cat " + tmp.path().toLocal8Bit() + "/windows.json; exit 0; }\n[ \"$1\" = subscribe ] && { sleep 30; exit 0; }\n"
                "[ \"$1\" = spaces ] && { echo '[{\"name\":\"1\",\"output\":\"O\",\"focused\":true,\"visible\":true,\"windows\":1},{\"name\":\"2\",\"output\":\"O\",\"focused\":false,\"visible\":false,\"windows\":0}]'; exit 0; }\n"
                "echo \"$@\" >> " + tmp.path().toLocal8Bit() + "/calls.log\n");
        s.close();
        s.setPermissions(QFile::ReadOwner | QFile::WriteOwner | QFile::ExeOwner);
        theme = new Theme;
        ThemeManager::Options to;
        to.watch = false; to.transitionMs = 0; to.appearancePath = tmp.path() + QStringLiteral("/appearance.json");
        themes = new ThemeManager(theme, to);
        QVERIFY(themes->start());
        DockController::Options o;
        o.configPath = tmp.path() + QStringLiteral("/dock.json");
        o.modePath = tmp.path() + QStringLiteral("/desktop.json");
        o.appDirs = {QStringLiteral(TAOS_DOCK_FIXTURES "/applications")};
        o.watch = false;
        o.engine = QStringLiteral("cli");
        o.cliProgram = s.fileName();
        dock = new DockController(o);
        qmlRegisterSingletonInstance("Taos.Dock", 1, 0, "Theme", theme);
        qmlRegisterSingletonInstance("Taos.Dock", 1, 0, "Dock", dock);
        qmlRegisterSingletonInstance("Taos.Dock", 1, 0, "DockSettings", dock->settings());
        engine = new QQmlEngine;
        view = new QQuickView(engine, nullptr);
        view->setResizeMode(QQuickView::SizeRootObjectToView);
        view->resize(1280, 720);
        view->show();
        QVERIFY(QTest::qWaitForWindowExposed(view));
        dock->engine()->refreshSpaces();
        QTRY_VERIFY(!dock->engine()->spaces().isEmpty());
    }
    void cleanupTestCase() { delete view; delete engine; delete dock; delete themes; delete theme; }

    void arrowsWalkTheRowsAndEnterRuns() {
        setWindows({win(5, "my.taos.dashboard", "Activity", true)});
        open(dock->model()->rowForKey(QStringLiteral("my.taos.dashboard")));
        QCOMPARE(cur(), (QVariantList{-1}));
        key(Qt::Key_Down);                                       // Minimise
        QCOMPARE(cur(), (QVariantList{0}));
        key(Qt::Key_Down); key(Qt::Key_Down);                    // Maximise, Full screen
        key(Qt::Key_Return);
        QTRY_COMPARE(lastCall(), QStringLiteral("fullscreen 5 on"));
        open(dock->model()->rowForKey(QStringLiteral("my.taos.dashboard")));
        key(Qt::Key_Up);                                         // wraps to the last row (Dock settings), skipping separators
        QVERIFY(!cur().isEmpty());
        QCOMPARE(cur().value(0).toInt(), dock->menuRows().size() - 1);
        key(Qt::Key_Down);                                       // and round to the first
        QCOMPARE(cur(), (QVariantList{0}));
    }
    void rightOpensASubmenuLeftAndEscapeCloseIt() {
        setWindows({win(5, "my.taos.dashboard", "Activity", true)});
        const int row = dock->model()->rowForKey(QStringLiteral("my.taos.dashboard"));
        open(row);
        const int spaces = rowOf(QStringLiteral("window-spaces:5"));
        QVERIFY(spaces > 0);
        for (int guard = 0; guard < 20 && cur().value(0).toInt() != spaces; ++guard) key(Qt::Key_Down);
        QCOMPARE(cur().value(0).toInt(), spaces);
        key(Qt::Key_Right);
        QCOMPARE(cur().size(), 2);
        QCOMPARE(cur().value(1).toInt(), 1);                        // "Space 1" is where the window is (not selectable): lands on Space 2
        key(Qt::Key_Left);
        QCOMPARE(cur(), (QVariantList{spaces}));                 // the submenu closed, the cursor is back on its row
        key(Qt::Key_Right);
        QCOMPARE(cur().size(), 2);
        QSignalSpy closed(dock, &DockController::menuClosed);
        key(Qt::Key_Escape);                                     // Escape closes the submenu first
        QCOMPARE(cur(), (QVariantList{spaces}));
        QCOMPARE(closed.count(), 0);
        key(Qt::Key_Right);
        key(Qt::Key_Return);                                     // Space 2
        QTRY_COMPARE(lastCall(), QStringLiteral("move-to-space 5 2"));
        open(row);
        key(Qt::Key_Escape);                                     // from the first panel Escape closes the menu
        QCOMPARE(closed.count() >= 1, true);
    }
    void downInTheSubmenuReachesNewSpace() {
        setWindows({win(5, "my.taos.dashboard", "Activity", true)});
        open(dock->model()->rowForKey(QStringLiteral("my.taos.dashboard")));
        const int spaces = rowOf(QStringLiteral("window-spaces:5"));
        for (int guard = 0; guard < 20 && cur().value(0).toInt() != spaces; ++guard) key(Qt::Key_Down);
        key(Qt::Key_Right);
        key(Qt::Key_Down);                                       // Space 2 -> (separator skipped) -> New space
        QCOMPARE(cur().value(1).toInt(), 3);
        key(Qt::Key_Return);
        QTRY_COMPARE(lastCall(), QStringLiteral("new-space 5"));
    }
    void severalWindowsEachHaveAControlsSubmenu() {
        setWindows({win(5, "foot", "a", true), win(6, "foot", "b")});
        open(dock->model()->rowForKey(QStringLiteral("foot")));
        key(Qt::Key_Down);                                       // window a
        key(Qt::Key_Down);                                       // window b
        QCOMPARE(cur(), (QVariantList{1}));
        key(Qt::Key_Right);
        QCOMPARE(cur().size(), 2);
        key(Qt::Key_Down); key(Qt::Key_Down);                    // Maximise (first row after Minimise), Full screen ... walk to Bring here
        key(Qt::Key_Down);
        key(Qt::Key_Return);
        QTRY_COMPARE(lastCall(), QStringLiteral("bring-here 6"));
        open(dock->model()->rowForKey(QStringLiteral("foot")));
        key(Qt::Key_Down); key(Qt::Key_Down);
        key(Qt::Key_Return);                                     // Enter on the title of window b: focuses it
        QTRY_COMPARE(lastCall(), QStringLiteral("focus 6"));
    }
    void theTaskbarMenuIsTheSameMenu() {
        setWindows({win(5, "my.taos.dashboard", "Activity", true)});
        dock->taskbarMenu(5, 300, 500);
        view->setSource(QUrl::fromLocalFile(QStringLiteral(TAOS_DOCK_QML_DIR "/DockMenu.qml")));
        QVERIFY(view->status() == QQuickView::Ready);
        view->rootObject()->forceActiveFocus();
        QTest::qWait(50);
        key(Qt::Key_Down);                                       // Show
        key(Qt::Key_Down);                                       // Minimise
        key(Qt::Key_Down);                                       // Maximise
        key(Qt::Key_Return);
        QTRY_COMPARE(lastCall(), QStringLiteral("maximise 5"));
    }
    void thePointerOpensTheSubmenuBesideItsRow() {
        setWindows({win(5, "my.taos.dashboard", "Activity", true)});
        open(dock->model()->rowForKey(QStringLiteral("my.taos.dashboard")));
        const int spaces = rowOf(QStringLiteral("window-spaces:5"));
        QMetaObject::invokeMethod(view->rootObject(), "hover", Q_ARG(QVariant, 0), Q_ARG(QVariant, spaces), Q_ARG(QVariant, true));
        QCOMPARE(cur().size(), 2);
        // two glass panels are on screen: find them by the items that carry a `path` property
        QList<QQuickItem *> panels;
        std::function<void(QQuickItem *)> walk = [&](QQuickItem *i) {
            if (i->property("placeAbsX").isValid() && i->property("rows").isValid()) panels << i;
            for (QQuickItem *c : i->childItems()) walk(c);
        };
        walk(view->rootObject());
        QTRY_COMPARE(panels.size(), 2);
        QQuickItem *a = panels[0], *b = panels[1];
        if (b->property("level").toInt() < a->property("level").toInt()) std::swap(a, b);
        const QRectF ra(a->property("placeAbsX").toReal(), a->property("placeAbsY").toReal(), a->width(), a->height());
        const QRectF rb(b->property("placeAbsX").toReal(), b->property("placeAbsY").toReal(), b->width(), b->height());
        QVERIFY2(!ra.intersects(rb.adjusted(5, 0, -5, 0)), "the submenu overlaps its parent");
        const QRectF out(0, 0, 1280, 720);
        QVERIFY2(out.contains(ra) && out.contains(rb), "both panels are on screen");
        // moving the pointer to another row closes it
        QMetaObject::invokeMethod(view->rootObject(), "hover", Q_ARG(QVariant, 0), Q_ARG(QVariant, 0), Q_ARG(QVariant, false));
        QCOMPARE(cur(), (QVariantList{0}));
    }
};

int main(int argc, char **argv) {
    qputenv("QT_QPA_PLATFORM", "offscreen");
    qputenv("QT_QUICK_BACKEND", "software");
    QGuiApplication app(argc, argv);
    DockMenuQml t;
    return QTest::qExec(&t, argc, argv);
}
#include "test_dockmenuqml.moc"
