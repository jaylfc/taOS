// SPDX-License-Identifier: AGPL-3.0-or-later
// The real QML: loads Dock.qml with a controller and checks keyboard navigation, tooltips'
// accessible names, and the shell-socket commands (Super+D) end to end.
#include <QGuiApplication>
#include <QIcon>
#include <QQmlEngine>
#include <QQuickItem>
#include <QQuickView>
#include <QTemporaryDir>
#include <QtQml>
#include <QtTest>

#include "dockcontroller.h"
#include "dockipc.h"
#include "iconprovider.h"
#include "theme.h"
#include "thememanager.h"

class DockQml : public QObject {
    Q_OBJECT
    QTemporaryDir tmp;
    Theme *theme = nullptr;
    ThemeManager *themes = nullptr;
    DockController *dock = nullptr;
    QQuickView *view = nullptr;
    QQmlEngine *engine = nullptr;
    static WindowInfo win(int id, const char *app, const char *title, bool focused = false, bool min = false) {
        WindowInfo w; w.id = id; w.appId = QString::fromLatin1(app); w.title = QString::fromLatin1(title); w.focused = focused; w.minimised = min; w.workspace = QStringLiteral("1"); return w;
    }
public:
    // The slots in model order. After a model reset the old delegates linger until their deferred delete runs
    // (how soon depends on the machine), so flush those first and keep one slot per index.
    QList<QQuickItem *> slotItems() {
        QTest::qWait(30);
        QCoreApplication::sendPostedEvents(nullptr, QEvent::DeferredDelete);
        QMap<int, QQuickItem *> byIndex;
        std::function<void(QQuickItem *)> walk = [&](QQuickItem *i) {
            if (i->property("accessibleText").isValid() && i->metaObject()->indexOfProperty("kbd") >= 0) byIndex[i->property("index").toInt()] = i;
            for (QQuickItem *c : i->childItems()) walk(c);
        };
        walk(view->rootObject());
        return byIndex.values();
    }
private slots:
    void initTestCase() {
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
        o.swaySocket = tmp.path() + QStringLiteral("/none");
        dock = new DockController(o);
        dock->model()->setWindows({win(1, "my.taos.dashboard", "Activity", true), win(2, "foot", "sh"), win(3, "chromium", "b", false, true)});
        qmlRegisterSingletonInstance("Taos.Dock", 1, 0, "Theme", theme);
        qmlRegisterSingletonInstance("Taos.Dock", 1, 0, "Dock", dock);
        qmlRegisterSingletonInstance("Taos.Dock", 1, 0, "DockSettings", dock->settings());
        engine = new QQmlEngine;
        engine->addImageProvider(QStringLiteral("dockicon"), new DockIconProvider);
        view = new QQuickView(engine, nullptr);
        view->setSource(QUrl::fromLocalFile(QStringLiteral(TAOS_DOCK_QML_DIR "/Dock.qml")));
        QVERIFY2(view->status() == QQuickView::Ready, qPrintable(view->errors().value(0).toString()));
        view->resize(dock->layout().surface);
        view->show();
        QVERIFY(QTest::qWaitForWindowExposed(view));
    }
    void cleanupTestCase() { delete view; delete engine; delete dock; delete themes; delete theme; }

    void everyItemHasAButtonName() {
        QQuickItem *root = view->rootObject();
        QList<QQuickItem *> buttons;
        std::function<void(QQuickItem *)> walk = [&](QQuickItem *i) {
            if (i->property("accessibleText").isValid() && i->metaObject()->indexOfProperty("kbd") >= 0) buttons << i;
            for (QQuickItem *c : i->childItems()) walk(c);
        };
        walk(root);
        QCOMPARE(buttons.size(), dock->model()->items().size());   // Dock.qml draws one slot per model row
        bool found = false;
        for (QQuickItem *b : buttons) if (b->property("accessibleText").toString() == QStringLiteral("Activity, running, 1 window, focused")) found = true;
        QVERIFY(found);
    }
    void superDGrabsTheKeyboardAndArrowsMove() {
        QQuickItem *root = view->rootObject();
        QCOMPARE(root->property("focusIndex").toInt(), -1);
        dock->handleCommand(QStringLiteral("focus"));
        QVERIFY(dock->keyboardActive());
        QCOMPARE(root->property("focusIndex").toInt(), 0);
        QTest::keyClick(view, Qt::Key_Right);
        QCOMPARE(root->property("focusIndex").toInt(), 1);
        QTest::keyClick(view, Qt::Key_Left);
        QTest::keyClick(view, Qt::Key_Left);                           // wraps, skipping the separator
        const int last = root->property("focusIndex").toInt();
        QCOMPARE(dock->model()->items()[last].kind, DockItem::Minimised);
        QTest::keyClick(view, Qt::Key_Right);
        QCOMPARE(root->property("focusIndex").toInt(), 0);
        QTest::keyClick(view, Qt::Key_End);
        QCOMPARE(root->property("focusIndex").toInt(), last);
        QTest::keyClick(view, Qt::Key_Escape);                        // leaves
        QVERIFY(!dock->keyboardActive());
        QCOMPARE(root->property("focusIndex").toInt(), -1);
    }
    void enterActivatesTheFocusedItem() {
        QStringList spawned;
        dock->setLauncherForTests([&](const QString &p, const QStringList &) { spawned << p; });
        dock->handleCommand(QStringLiteral("focus"));
        QCOMPARE(view->rootObject()->property("focusIndex").toInt(), 0);   // Files: not running
        QTest::keyClick(view, Qt::Key_Return);
        QCOMPARE(spawned, QStringList{QStringLiteral("taos-files")});
        dock->handleCommand(QStringLiteral("focus"));                  // second press releases
        QVERIFY(!dock->keyboardActive());
    }
    void menuKeyOpensTheMenu() {
        dock->handleCommand(QStringLiteral("focus"));
        QSignalSpy spy(dock, &DockController::menuRequested);
        QTest::keyClick(view, Qt::Key_Menu);
        QCOMPARE(spy.count(), 1);
        QVERIFY(!dock->menuRows().isEmpty());
        dock->closeMenu();
        dock->releaseKeyboard();
    }
    void dragReordersPinnedItems() {
        const auto b = slotItems();
        QVERIFY(b.size() >= 4);
        const QPoint from = b[0]->mapToScene(QPointF(b[0]->width() / 2, b[0]->height() / 2)).toPoint();
        const QPoint to = b[2]->mapToScene(QPointF(b[2]->width() / 2, b[2]->height() / 2)).toPoint();
        QTest::mousePress(view, Qt::LeftButton, Qt::NoModifier, from);
        for (int i = 1; i <= 10; ++i) QTest::mouseMove(view, from + (to - from) * i / 10);
        QTest::mouseRelease(view, Qt::LeftButton, Qt::NoModifier, to);
        QTRY_COMPARE(dock->model()->items()[2].key, QStringLiteral("my.taos.files"));   // Files moved from slot 0 to slot 2
        QCOMPARE(dock->settings()->config().pinned.mid(0, 3), (QStringList{QStringLiteral("foot"), QStringLiteral("chromium"), QStringLiteral("my.taos.files")}));
        QVERIFY(dock->settings()->config().pinnedSet);
    }
    void clickDoesNotDragAndRunsTheAction() {
        QStringList spawned;
        dock->setLauncherForTests([&](const QString &p, const QStringList &) { spawned << p; });
        const auto b = slotItems();
        const int idx = dock->model()->rowForKey(QStringLiteral("my.taos.settings"));
        QVERIFY(idx >= 0);
        const QPoint at = b[idx]->mapToScene(QPointF(b[idx]->width() / 2, b[idx]->height() / 2)).toPoint();
        QTest::mouseClick(view, Qt::LeftButton, Qt::NoModifier, at);
        QTRY_COMPARE(spawned, QStringList{QStringLiteral("taos-settings")});
    }
    void magnificationGrowsTowardsThePointer() {
        QVERIFY(dock->settings()->set(QStringLiteral("magnify"), QStringLiteral("true")));
        view->resize(dock->layout().surface);   // what the layer surface would do: headroom around the strip
        QTest::qWait(50);
        const auto b = slotItems();
        auto body = [&](int i) { return b[i]->childItems().value(1); };   // the body item follows the separator rectangle
        const QPoint at = b[1]->mapToScene(QPointF(b[1]->width() / 2, b[1]->height() / 2)).toPoint();
        QTest::mouseMove(view, at);
        QTest::mouseMove(view, at + QPoint(1, 0));
        auto scaleOf = [&](QQuickItem *slot) { for (QQuickItem *c : slot->childItems()) if (c->property("mag").isValid()) return c->property("mag").toDouble(); return 1.0; };
        QTRY_VERIFY(scaleOf(b[1]) > 1.4);
        QVERIFY(scaleOf(b[1]) > scaleOf(b[3]));
        QVERIFY(scaleOf(b[6]) < 1.1);                            // far items barely move
        (void)body;
        QVERIFY(dock->settings()->set(QStringLiteral("magnify"), QStringLiteral("false")));
        view->resize(dock->layout().surface);
        QTRY_COMPARE(scaleOf(b[1]), 1.0);
    }
    void socketCommandsReachTheDock() {
        qputenv("XDG_RUNTIME_DIR", tmp.path().toLocal8Bit());
        DockIpc ipc;
        QVERIFY(ipc.listen());
        QStringList got;
        connect(&ipc, &DockIpc::command, this, [&](const QString &w) { got << w; });
        QString err;
        QVERIFY(DockIpc::send(QStringLiteral("focus"), &err));
        QTRY_COMPARE(got, QStringList{QStringLiteral("focus")});
        QVERIFY(DockIpc::send(QStringLiteral("bogus"), &err));          // unknown words are ignored
        QVERIFY(DockIpc::send(QStringLiteral("reveal"), &err));
        QTRY_COMPARE(got.size(), 2);
        QCOMPARE(got.last(), QStringLiteral("reveal"));
        ipc.deleteLater();
        QVERIFY(!DockIpc::send(QStringLiteral("focus"), &err, tmp.path() + QStringLiteral("/nobody.sock")));
    }
    void autohideRevealsOnPointerAndHidesAfterwards() {
        QVERIFY(dock->settings()->set(QStringLiteral("autohide"), QStringLiteral("true")));
        QVERIFY(dock->layout().exclusiveZone == 0);
        QTRY_VERIFY(!dock->revealed());
        dock->setPointerInside(true);
        QVERIFY(dock->revealed());
        dock->setPointerInside(false);
        QTRY_VERIFY_WITH_TIMEOUT(!dock->revealed(), 2000);
        QTRY_VERIFY_WITH_TIMEOUT(dock->shrunk(), 2000);   // the surface then shrinks to the edge line
        dock->setPointerInside(true);
        QVERIFY(!dock->shrunk());                          // and is full size again before the strip slides in
        dock->setPointerInside(false);
        QTRY_VERIFY_WITH_TIMEOUT(!dock->revealed(), 2000);
        dock->handleCommand(QStringLiteral("reveal"));
        QVERIFY(dock->revealed());
        dock->handleCommand(QStringLiteral("hide"));
        QVERIFY(!dock->revealed());
        QVERIFY(dock->settings()->set(QStringLiteral("autohide"), QStringLiteral("false")));
        QVERIFY(dock->revealed());
        QVERIFY(dock->layout().exclusiveZone > 0);
    }
};
int main(int argc, char **argv) {
    qputenv("QT_QPA_PLATFORM", "offscreen");
    qputenv("QT_QUICK_BACKEND", "software");
    QGuiApplication app(argc, argv);
    DockQml t;
    return QTest::qExec(&t, argc, argv);
}
#include "test_dockqml.moc"
