#include <QtTest>

#include "windowlist.h"

static WinInfo win(int id, const char *title, const char *app = "foot", const char *ws = "1") {
    WinInfo w;
    w.conId = id;
    w.title = QString::fromLatin1(title);
    w.appId = QString::fromLatin1(app);
    w.workspace = QString::fromLatin1(ws);
    return w;
}

class TestWindowList : public QObject {
    Q_OBJECT
private slots:
    void seedKeepsGivenOrder() {
        MruTracker m;
        m.reconcile({ 1, 2, 3 }, { 3, 1, 2 });
        QCOMPARE(m.order(), QVector<int>({ 3, 1, 2 }));
    }
    void focusMovesToFront() {
        MruTracker m;
        m.reconcile({ 1, 2, 3, 4 }, { 1, 2, 3, 4 });
        m.focus(3);
        QCOMPARE(m.order(), QVector<int>({ 3, 1, 2, 4 }));
        m.focus(4);
        m.focus(3);
        QCOMPARE(m.order(), QVector<int>({ 3, 4, 1, 2 }));
        m.focus(3);                                       // already first: stable
        QCOMPARE(m.order(), QVector<int>({ 3, 4, 1, 2 }));
    }
    void alternatingFocusKeepsTwoMostRecent() {
        // Alt+Tab twice must come back: the second most recent window is always the one before.
        MruTracker m;
        m.reconcile({ 1, 2, 3 }, { 1, 2, 3 });
        m.focus(2);
        QCOMPARE(m.order()[1], 1);
        m.focus(1);
        QCOMPARE(m.order()[1], 2);
        m.focus(2);
        QCOMPARE(m.order()[1], 1);
    }
    void removeAndUnknownFocus() {
        MruTracker m;
        m.reconcile({ 1, 2, 3 }, { 1, 2, 3 });
        m.remove(2);
        QCOMPARE(m.order(), QVector<int>({ 1, 3 }));
        m.focus(9);                                       // unknown id is added in front
        QCOMPARE(m.order(), QVector<int>({ 9, 1, 3 }));
    }
    void reconcileDropsGoneAndAppendsNew() {
        MruTracker m;
        m.reconcile({ 1, 2, 3 }, { 1, 2, 3 });
        m.focus(3);
        m.reconcile({ 2, 3, 7 }, {});                     // 1 closed, 7 opened
        QCOMPARE(m.order(), QVector<int>({ 3, 2, 7 }));
    }
    void sortedFollowsOrderAndTolerantOfUnknown() {
        MruTracker m;
        m.reconcile({ 1, 2, 3 }, { 2, 3, 1 });
        const QVector<WinInfo> in = { win(1, "a"), win(2, "b"), win(3, "c"), win(4, "d") };
        const QVector<WinInfo> out = m.sorted(in);
        QCOMPARE(out[0].conId, 2);
        QCOMPARE(out[1].conId, 3);
        QCOMPARE(out[2].conId, 1);
        QCOMPARE(out[3].conId, 4);                        // unknown goes last
    }
    void filterMatching() {
        const WinInfo w = win(1, "Build log: ninja", "foot", "dev");
        QVERIFY(WindowFilter::matches(w, "Terminal", ""));
        QVERIFY(WindowFilter::matches(w, "Terminal", "   "));
        QVERIFY(WindowFilter::matches(w, "Terminal", "build"));
        QVERIFY(WindowFilter::matches(w, "Terminal", "NINJA"));          // case insensitive
        QVERIFY(WindowFilter::matches(w, "Terminal", "foot"));            // app id
        QVERIFY(WindowFilter::matches(w, "Terminal", "termin"));          // app display name
        QVERIFY(WindowFilter::matches(w, "Terminal", "dev"));             // workspace name
        QVERIFY(WindowFilter::matches(w, "Terminal", "log ninja"));       // every word, any order
        QVERIFY(WindowFilter::matches(w, "Terminal", "ninja build"));
        QVERIFY(!WindowFilter::matches(w, "Terminal", "ninja zzz"));      // one word missing: no match
        QVERIFY(!WindowFilter::matches(w, "Terminal", "browser"));
    }
    void filterEmptyFields() {
        WinInfo w;
        w.conId = 5;
        QVERIFY(WindowFilter::matches(w, "", ""));
        QVERIFY(!WindowFilter::matches(w, "", "x"));
    }
};
QTEST_APPLESS_MAIN(TestWindowList)
#include "test_windowlist.moc"
