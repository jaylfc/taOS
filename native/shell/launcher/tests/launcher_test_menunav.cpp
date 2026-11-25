// SPDX-License-Identifier: AGPL-3.0-or-later
#include <QtTest>

#include "menunav.h"

using taos::launcher::MenuNav;

class TestMenuNav : public QObject {
    Q_OBJECT
    static MenuNav single() {   // centred panel: pinned grid (4 columns, 6 apps), recent row (3), all list (1 column, 10), footer (3)
        MenuNav n;
        n.setOrder({ "pinned", "recent", "all", "footer" });
        n.setZone("pinned", 6, 4); n.setZone("recent", 3, 3); n.setZone("all", 10, 1); n.setZone("footer", 3, 3);
        return n;
    }
    static MenuNav twoPane() {  // anchored: all apps list on the left, pinned grid (3 columns) on the right
        MenuNav n;
        n.setOrder({ "all", "footer" });
        n.setZone("all", 8, 1); n.setZone("pinned", 7, 3); n.setZone("footer", 3, 3); n.setZone("recent", 0, 1);
        n.setLink("all", "pinned");
        return n;
    }
private slots:
    void nothingIsSelectedUntilAnArrowKey() {
        MenuNav n = single();
        QVERIFY(!n.active());
        QVERIFY(n.move(0, 1));
        QCOMPARE(n.zone(), QString("pinned")); QCOMPARE(n.index(), 0);
    }
    void rowsAndColumnsInAGrid() {
        MenuNav n = single();
        n.select("pinned", 1);
        QVERIFY(n.move(1, 0)); QCOMPARE(n.index(), 2);
        QVERIFY(n.move(0, 1)); QCOMPARE(n.index(), 5);          // second row exists for column 2
        QVERIFY(!n.move(1, 0));                                  // 5 is the last: no wrap, no change
        QCOMPARE(n.index(), 5);
        QVERIFY(n.move(-1, 0)); QCOMPARE(n.index(), 4);
        QVERIFY(n.move(0, -1)); QCOMPARE(n.index(), 0);
    }
    void leftAtTheStartOfARowDoesNotJumpUpARow() {
        MenuNav n = single();
        n.select("pinned", 4);
        QVERIFY(!n.move(-1, 0));
        QCOMPARE(n.index(), 4);
    }
    void downCrossesIntoTheNextZoneInTheSameColumn() {
        MenuNav n = single();
        n.select("pinned", 5);       // row 1, column 1
        QVERIFY(n.move(0, 1));
        QCOMPARE(n.zone(), QString("recent")); QCOMPARE(n.index(), 1);
        QVERIFY(n.move(0, 1));
        QCOMPARE(n.zone(), QString("all")); QCOMPARE(n.index(), 0);   // a list has one column
    }
    void upReturnsToTheLastRowOfThePreviousZone() {
        MenuNav n = single();
        n.select("recent", 1);
        QVERIFY(n.move(0, -1));
        QCOMPARE(n.zone(), QString("pinned")); QCOMPARE(n.index(), 5);   // last row, column 1
    }
    void emptyZonesAreSkipped() {
        MenuNav n = single();
        n.setZone("recent", 0, 3);
        n.select("pinned", 0);
        QVERIFY(n.move(0, 1)); QCOMPARE(n.index(), 4);
        QVERIFY(n.move(0, 1)); QCOMPARE(n.zone(), QString("all"));
    }
    void aShortLastRowIsReachable() {
        MenuNav n = single();
        n.select("pinned", 3);       // row 0 column 3: row 1 only has columns 0 and 1
        QVERIFY(n.move(0, 1));
        QCOMPARE(n.index(), 5);
    }
    void listsEndAtTheFooterAndStopThere() {
        MenuNav n = single();
        n.select("all", 9);
        QVERIFY(n.move(0, 1)); QCOMPARE(n.zone(), QString("footer")); QCOMPARE(n.index(), 0);
        QVERIFY(!n.move(0, 1));
        QVERIFY(n.move(1, 0)); QCOMPARE(n.index(), 1);
        QVERIFY(n.move(0, -1)); QCOMPARE(n.zone(), QString("all")); QCOMPARE(n.index(), 9);
    }
    void twoPaneRightHopsToThePinnedGridAndLeftComesBack() {
        MenuNav n = twoPane();
        n.select("all", 1);
        QVERIFY(n.move(1, 0));
        QCOMPARE(n.zone(), QString("pinned")); QCOMPARE(n.index(), 3);     // same row (1), first column
        QVERIFY(n.move(1, 0)); QCOMPARE(n.index(), 4);
        QVERIFY(n.move(-1, 0)); QCOMPARE(n.index(), 3);
        QVERIFY(n.move(-1, 0));                                            // at the left edge of the grid: back to the list
        QCOMPARE(n.zone(), QString("all")); QCOMPARE(n.index(), 1);        // same row (row 1)
    }
    void twoPaneLeftOnTheListDoesNothing() {
        MenuNav n = twoPane();
        n.select("all", 2);
        QVERIFY(!n.move(-1, 0));
    }
    void twoPaneDownFromEitherPaneReachesTheFooter() {
        MenuNav n = twoPane();
        n.select("all", 7);
        QVERIFY(n.move(0, 1)); QCOMPARE(n.zone(), QString("footer"));
        n.select("pinned", 6);
        QVERIFY(n.move(0, 1));                                              // the pinned grid sits beside the list: Down goes to the footer
        QCOMPARE(n.zone(), QString("footer"));
    }
    void shrinkingAZoneClampsTheCursor() {
        MenuNav n = single();
        n.select("all", 9);
        n.setZone("all", 4, 1);
        QCOMPARE(n.index(), 3);
        n.setZone("all", 0, 1);
        QVERIFY(!n.active());
    }
};
QTEST_APPLESS_MAIN(TestMenuNav)
#include "launcher_test_menunav.moc"
