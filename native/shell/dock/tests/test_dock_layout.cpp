// SPDX-License-Identifier: AGPL-3.0-or-later
#include <QtTest>

#include "layout.h"

class LayoutTest : public QObject {
    Q_OBJECT
private slots:
    void iconSizes() {
        QCOMPARE(DockLayout::iconFor(QStringLiteral("small")), 36);
        QCOMPARE(DockLayout::iconFor(QStringLiteral("medium")), 48);
        QCOMPARE(DockLayout::iconFor(QStringLiteral("large")), 64);
        QCOMPARE(DockLayout::iconFor(QStringLiteral("bogus")), 48);
    }
    void exclusiveZonePerPositionAndSize_data() {
        QTest::addColumn<QString>("pos");
        QTest::addColumn<QString>("size");
        QTest::addColumn<bool>("attached");
        QTest::addColumn<bool>("autohide");
        QTest::addColumn<int>("zone");   // what is reserved in total: the exclusive zone plus the layer margin
        // strip thickness is icon + 22; a floating dock also keeps its 8 px gap clear
        for (const QString &p : {QStringLiteral("bottom"), QStringLiteral("left"), QStringLiteral("right")}) {
            QTest::newRow(qPrintable(p + "-small-floating")) << p << "small" << false << false << 58 + 8;
            QTest::newRow(qPrintable(p + "-medium-floating")) << p << "medium" << false << false << 70 + 8;
            QTest::newRow(qPrintable(p + "-large-floating")) << p << "large" << false << false << 86 + 8;
            QTest::newRow(qPrintable(p + "-medium-attached")) << p << "medium" << true << false << 70;
            QTest::newRow(qPrintable(p + "-medium-autohide")) << p << "medium" << false << true << 0;
        }
    }
    void exclusiveZonePerPositionAndSize() {
        QFETCH(QString, pos); QFETCH(QString, size); QFETCH(bool, attached); QFETCH(bool, autohide); QFETCH(int, zone);
        LayoutInput in;
        in.position = pos; in.size = size; in.attached = attached; in.autohide = autohide; in.items = 8;
        const Layout l = DockLayout::compute(in);
        QCOMPARE(l.reserved(), zone);
        QCOMPARE(l.exclusiveZone, autohide ? 0 : l.stripCross);   // the compositor adds the margin itself
        QCOMPARE(l.margin, attached ? 0 : 8);
        QCOMPARE(l.anchorBottom, pos == QLatin1String("bottom"));
        QCOMPARE(l.anchorLeft, pos == QLatin1String("left"));
        QCOMPARE(l.anchorRight, pos == QLatin1String("right"));
        QCOMPARE(l.vertical, pos != QLatin1String("bottom"));
    }
    void scaleKeepsLogicalZoneAndRoundsPhysical() {
        LayoutInput in;
        in.items = 8;
        const Layout l = DockLayout::compute(in);
        QCOMPARE(l.reserved(), 78);                          // logical: does not depend on the output scale
        QCOMPARE(l.physicalExclusive(1.0), 78);
        QCOMPARE(l.physicalExclusive(2.0), 156);
        QCOMPARE(l.physicalExclusive(1.25), 98);             // 97.5 rounds up: never reserve too little
        QCOMPARE(l.physical(1.5).width(), int(std::ceil(l.surface.width() * 1.5)));
    }
    void surfaceIsExactlyTheStrip() {
        LayoutInput in;
        in.items = 6; in.separators = 1;
        const Layout l = DockLayout::compute(in);
        QCOMPARE(l.stripMain, 2 * 10 + 6 * 60 + 14);
        QCOMPARE(l.band, 0);                                       // no transparent padding: nothing extra to blur or cover
        QCOMPARE(l.surface, l.strip.size());
        QCOMPARE(l.strip.topLeft(), QPoint(0, 0));
        QCOMPARE(l.margin, 8);                                     // the gap is a layer-shell margin
        QCOMPARE(l.hiddenSurface.height(), DockLayout::kTriggerPx);   // hidden: a thin line on the screen edge
        QCOMPARE(l.hiddenSurface.width(), l.surface.width());
    }
    void attachedIsFlush() {
        LayoutInput in;
        in.items = 4; in.attached = true;
        const Layout l = DockLayout::compute(in);
        QCOMPARE(l.gap, 0);
        QCOMPARE(l.margin, 0);
        QCOMPARE(l.surfacePos(in.output).y() + l.surface.height(), in.output.height());
    }
    void magnifyAddsHeadroomButNoReservation() {
        LayoutInput in;
        in.items = 5;
        const Layout plain = DockLayout::compute(in);
        in.magnify = true;
        const Layout mag = DockLayout::compute(in);
        QVERIFY(mag.surface.height() > plain.surface.height());
        QVERIFY(mag.surface.width() > plain.surface.width());
        QCOMPARE(mag.exclusiveZone, plain.exclusiveZone);   // headroom is never reserved
        QCOMPARE(plain.surface.height(), 70);
    }
    void positionsAreCentredOnTheirEdge() {
        LayoutInput in;
        in.items = 6;
        in.output = QSize(1920, 1080);
        Layout b = DockLayout::compute(in);
        QCOMPARE(b.surfacePos(in.output).y() + b.surface.height() + b.margin, 1080);
        QCOMPARE(b.surfacePos(in.output).x() * 2 + b.surface.width(), 1920);
        in.position = QStringLiteral("left");
        Layout lft = DockLayout::compute(in);
        QCOMPARE(lft.surfacePos(in.output).x(), lft.margin);
        in.position = QStringLiteral("right");
        Layout r = DockLayout::compute(in);
        QCOMPARE(r.surfacePos(in.output).x() + r.surface.width() + r.margin, 1920);
    }
    void tooManyItemsAreClampedToTheOutput() {
        LayoutInput in;
        in.items = 80;
        in.output = QSize(800, 600);
        QVERIFY(DockLayout::compute(in).surface.width() <= 800);
    }
};
QTEST_APPLESS_MAIN(LayoutTest)
#include "test_dock_layout.moc"
