#include <QtTest>

#include "layoutplan.h"

using namespace LayoutMath;

class TestLayout : public QObject {
    Q_OBJECT
private slots:
    void shapes() {
        QCOMPARE(shapeFor(QSizeF(3840, 1200)), Shape::Ultrawide);
        QCOMPARE(shapeFor(QSizeF(3840, 2160)), Shape::Landscape);
        QCOMPARE(shapeFor(QSizeF(1920, 1080)), Shape::Landscape);
        QCOMPARE(shapeFor(QSizeF(1080, 2400)), Shape::Portrait);
        QCOMPARE(shapeFor(QSizeF(1024, 768)), Shape::Landscape);
        QCOMPARE(shapeFor(QSizeF(0, 0)), Shape::Landscape);  // never divides by zero
    }
    void wordmarkCoversTheMeasuredGlyphs() {
        // Glyph extents measured on the shipped neural wallpaper (fractions of the image).
        struct M { QSizeF s; QRectF glyphs; } cases[] = {
            {{3840, 1200}, QRectF(QPointF(0.445, 0.44), QPointF(0.555, 0.55))},
            {{3840, 2160}, QRectF(QPointF(0.40, 0.445), QPointF(0.595, 0.575))},
            {{1920, 1080}, QRectF(QPointF(0.40, 0.445), QPointF(0.595, 0.575))},
            {{1080, 2400}, QRectF(QPointF(0.27, 0.41), QPointF(0.73, 0.47))},
        };
        for (const M &m : cases) {
            const QRectF box = wordmarkRect(m.s);
            const QRectF g(m.glyphs.x() * m.s.width(), m.glyphs.y() * m.s.height(), m.glyphs.width() * m.s.width(), m.glyphs.height() * m.s.height());
            QVERIFY2(box.contains(g), qPrintable(QString("%1x%2 box %3 does not contain glyphs").arg(m.s.width()).arg(m.s.height()).arg(box.x())));
            // Margin is generous but the box stays a box, not the screen.
            QVERIFY(box.width() < m.s.width() * 0.6 && box.height() < m.s.height() * 0.25);
        }
    }
    void blocksNeverTouchTheWordmarkAndStayOnScreen_data() {
        QTest::addColumn<QSizeF>("size");
        QTest::newRow("3840x1200") << QSizeF(3840, 1200);
        QTest::newRow("3840x2160") << QSizeF(3840, 2160);
        QTest::newRow("1920x1080") << QSizeF(1920, 1080);
        QTest::newRow("1080x2400") << QSizeF(1080, 2400);
        QTest::newRow("2560x1440") << QSizeF(2560, 1440);
        QTest::newRow("1366x768") << QSizeF(1366, 768);
        QTest::newRow("1280x800") << QSizeF(1280, 800);
        QTest::newRow("1024x768") << QSizeF(1024, 768);
        QTest::newRow("5120x1440") << QSizeF(5120, 1440);
        QTest::newRow("1080x1920") << QSizeF(1080, 1920);
        QTest::newRow("720x1560") << QSizeF(720, 1560);
    }
    void blocksNeverTouchTheWordmarkAndStayOnScreen() {
        QFETCH(QSizeF, size);
        const Plan p = plan(size);
        const QRectF screen(QPointF(0, 0), size);
        QVERIFY2(screen.contains(p.clockBlock), "clock block on screen");
        QVERIFY2(screen.contains(p.userBlock), "user block on screen");
        QVERIFY2(screen.contains(p.bottomBar), "bottom bar on screen");
        QVERIFY2(screen.contains(p.wordmark), "wordmark on screen");
        QVERIFY2(!p.clockBlock.intersects(p.wordmark), "clock block clear of the wordmark");
        QVERIFY2(!p.userBlock.intersects(p.wordmark), "user block clear of the wordmark");
        QVERIFY2(!p.bottomBar.intersects(p.wordmark), "bottom bar clear of the wordmark");
        QVERIFY2(!p.clockBlock.intersects(p.userBlock), "clock and users clear of each other");
        QVERIFY2(!p.userBlock.intersects(p.bottomBar), "users clear of the bottom bar");
        QVERIFY2(p.clockBlock.bottom() < p.wordmark.top(), "clock above the wordmark");
        QVERIFY2(p.userBlock.top() > p.wordmark.bottom(), "users below the wordmark");
        // Both blocks are centred on the wordmark's axis.
        QVERIFY(qAbs(p.clockBlock.center().x() - size.width() / 2) < 1);
        QVERIFY(qAbs(p.userBlock.center().x() - size.width() / 2) < 1);
        QVERIFY(qAbs(p.wordmark.center().x() - size.width() / 2) < 1);
        // Sizes stay sensible: nothing tiny, nothing absurd.
        QVERIFY(p.clockPx >= 56 && p.clockPx <= size.height() * 0.2);
        QVERIFY(p.avatar >= 50);
        QVERIFY(p.fieldW >= 150 && p.fieldW <= size.width());
        // A compact pill: about three avatars wide, never the old wide slab.
        QVERIFY2(p.fieldW <= p.avatar * 4.0 && p.fieldH <= p.avatar * 0.6, "field stays in proportion to the avatar");
        // The users block has room for what it must hold: avatar + name + field.
        QVERIFY2(p.userBlock.height() >= p.fieldTop + p.fieldH + p.messageH, "users block holds avatar, name and field");
        // The clock really fits its block: clock line + date + weather row.
        QVERIFY2(p.clockBlock.height() >= p.clockPx * 1.2, "clock block holds clock, date and weather");
    }
    void clockBlockWidthHoldsTheDigits() {
        // "00:00" in a light face is about 2.9 em wide with tabular figures.
        for (QSizeF s : {QSizeF(3840, 1200), QSizeF(3840, 2160), QSizeF(1920, 1080), QSizeF(1080, 2400)}) {
            const Plan p = plan(s);
            QVERIFY(p.clockBlock.width() >= p.clockPx * 3.0);
            QVERIFY(p.clockBlock.width() <= s.width());
        }
    }
    void qmlFaceUpdates() {
        LayoutPlan lp;
        QSignalSpy sp(&lp, &LayoutPlan::changed);
        lp.setWidth(3840);
        lp.setHeight(1200);
        QVERIFY(sp.size() >= 1);
        QCOMPARE(lp.wordmark(), wordmarkRect(QSizeF(3840, 1200)));
        lp.setWidth(3840);  // unchanged
        const int n = sp.size();
        lp.setHeight(1200);
        QCOMPARE(sp.size(), n);
    }
};
QTEST_MAIN(TestLayout)
#include "test_layout.moc"
