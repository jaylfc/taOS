// SPDX-License-Identifier: AGPL-3.0-or-later
#include <QtTest>

#include "taskbarmodel.h"

// The taskbar's window list from fake compositor data: ordering, focus and the share-out of the width.
static WindowInfo win(int id, const QString &app, const QString &title, bool focused = false, bool min = false) {
    WindowInfo w;
    w.id = id;
    w.appId = app;
    w.title = title;
    w.focused = focused;
    w.minimised = min;
    return w;
}
static QVector<int> ids(const TaskbarModel &m) { QVector<int> v; for (const auto &b : m.buttons()) v << b.id; return v; }

class TestTaskbarModel : public QObject {
    Q_OBJECT
private slots:
    void firstSightOrdersByAge() {
        TaskbarModel m;
        m.setWindows({win(9, "foot", "c"), win(3, "foot", "a"), win(5, "foot", "b")});
        QCOMPARE(ids(m), (QVector<int>{3, 5, 9}));
    }
    void focusNeverReorders() {
        TaskbarModel m;
        m.setWindows({win(3, "foot", "a", true), win(5, "foot", "b"), win(9, "foot", "c")});
        QCOMPARE(m.focusedPosition(), 0);
        m.setWindows({win(3, "foot", "a"), win(5, "foot", "b"), win(9, "foot", "c", true)});
        QCOMPARE(ids(m), (QVector<int>{3, 5, 9}));
        QCOMPARE(m.focusedPosition(), 2);
        // the compositor lists windows in focus order: still no reorder
        m.setWindows({win(9, "foot", "c", true), win(3, "foot", "a"), win(5, "foot", "b")});
        QCOMPARE(ids(m), (QVector<int>{3, 5, 9}));
    }
    void newWindowGoesToTheEndAndClosedOnesCloseUp() {
        TaskbarModel m;
        m.setWindows({win(3, "foot", "a"), win(5, "foot", "b"), win(9, "foot", "c")});
        m.setWindows({win(3, "foot", "a"), win(4, "foot", "new"), win(5, "foot", "b"), win(9, "foot", "c")});
        QCOMPARE(ids(m), (QVector<int>{3, 5, 9, 4}));       // not between 3 and 5 although its id is
        m.setWindows({win(3, "foot", "a"), win(4, "foot", "new"), win(9, "foot", "c")});
        QCOMPARE(ids(m), (QVector<int>{3, 9, 4}));
    }
    void minimisedKeepsItsPlaceAndIsNotActive() {
        TaskbarModel m;
        m.setWindows({win(3, "foot", "a"), win(5, "foot", "b", true)});
        m.setWindows({win(3, "foot", "a"), win(5, "foot", "b", true, true)});   // minimised: sway may still call it focused
        QCOMPARE(ids(m), (QVector<int>{3, 5}));
        QVERIFY(m.buttons()[1].minimised);
        QVERIFY(!m.buttons()[1].focused);
        QCOMPARE(m.focusedPosition(), -1);
        QVERIFY(m.data(m.index(1), TaskbarModel::AccessibleRole).toString().contains("minimised"));
    }
    void emptyTitleFallsBackToTheAppName() {
        TaskbarModel m;
        m.setWindows({win(3, "some.app", "")});
        QCOMPARE(m.buttons()[0].title, QStringLiteral("some.app"));
    }
    void noChangeNoSignal() {
        TaskbarModel m;
        m.setWindows({win(3, "foot", "a")});
        QSignalSpy spy(&m, &TaskbarModel::changed);
        m.setWindows({win(3, "foot", "a")});
        QCOMPARE(spy.count(), 0);
        m.setWindows({win(3, "foot", "b")});
        QCOMPARE(spy.count(), 1);
    }
    void widthSharedUpToTheMaximum() {
        const auto r = TaskbarLayout::compute(1000, 4, 0, 60, 160, 4, 40);
        QCOMPARE(r.visible, 4);
        QCOMPARE(r.hidden, 0);
        QCOMPARE(r.buttonWidth, 160);                      // plenty of room: capped
        const auto t = TaskbarLayout::compute(500, 4, 0, 60, 160, 4, 40);
        QCOMPARE(t.buttonWidth, (500 - 3 * 4) / 4);        // shared equally
    }
    void overflowKeepsTheActiveWindowInView() {
        // 12 windows, room for ((600 - 40 - 4 + 4) / 64) = 8 buttons at minimum width
        auto r = TaskbarLayout::compute(600, 12, 0, 60, 160, 4, 40);
        QCOMPARE(r.buttonWidth, 60);
        QCOMPARE(r.visible, 8);
        QCOMPARE(r.hidden, 4);
        QCOMPARE(r.first, 0);
        r = TaskbarLayout::compute(600, 12, 11, 60, 160, 4, 40);
        QCOMPARE(r.first, 4);                              // the last window is the last visible one
        QVERIFY(11 >= r.first && 11 < r.first + r.visible);
        r = TaskbarLayout::compute(600, 12, 5, 60, 160, 4, 40);
        QCOMPARE(r.first, 0);
        QVERIFY(r.visible * (r.buttonWidth + 4) + 40 <= 600 + 4);
    }
    void degenerateWidths() {
        QCOMPARE(TaskbarLayout::compute(0, 3, 0, 60, 160, 4, 40).visible, 0);
        QCOMPARE(TaskbarLayout::compute(100, 0, -1, 60, 160, 4, 40).visible, 0);
        const auto r = TaskbarLayout::compute(70, 5, 2, 60, 160, 4, 40);    // narrower than one button plus "more": still one
        QCOMPARE(r.visible, 1);
        QCOMPARE(r.hidden, 4);
        QCOMPARE(r.first, 2);
    }
};
QTEST_APPLESS_MAIN(TestTaskbarModel)
#include "test_taskbarmodel.moc"
