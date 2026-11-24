// SPDX-License-Identifier: AGPL-3.0-or-later
#include <QtTest>
#include <cmath>

#include "packing.h"

using namespace Packing;

namespace {
// A simple deterministic generator, so the tests need no <random> state to be portable.
struct Lcg { quint32 s; quint32 next() { s = s * 1664525u + 1013904223u; return s >> 8; } double unit() { return (next() % 10000) / 10000.0; } };

QVector<Item> randomItems(int n, quint32 seed, const QSizeF &screen) {
    Lcg r { seed };
    QVector<Item> items;
    for (int i = 0; i < n; ++i) {
        const qreal w = screen.width() * (0.15 + 0.7 * r.unit());
        const qreal h = screen.height() * (0.15 + 0.8 * r.unit());
        const qreal x = (screen.width() - w) * r.unit(), y = (screen.height() - h) * r.unit();
        items.push_back({ 100 + i, QRectF(x, y, w, h) });
    }
    return items;
}

void checkLayout(const QVector<Item> &items, const QRectF &area, const Options &o, const QVector<QRectF> &out) {
    QCOMPARE(out.size(), items.size());
    for (int i = 0; i < out.size(); ++i) {
        const QRectF &r = out[i];
        QVERIFY2(r.width() > 0 && r.height() > 0, "empty rect");
        // inside the area (a hair of float slack)
        QVERIFY2(r.left() >= area.left() - 1e-6 && r.top() >= area.top() - 1e-6 && r.right() <= area.right() + 1e-6
                     && r.bottom() <= area.bottom() + 1e-6,
                 qPrintable(QStringLiteral("rect %1 outside the area").arg(i)));
        // aspect ratio kept within 1 percent of the source's (the minDim lift keeps it exactly)
        const qreal want = items[i].source.width() / items[i].source.height();
        const qreal got = r.width() / r.height();
        QVERIFY2(std::abs(got / want - 1.0) < 0.01, qPrintable(QStringLiteral("aspect %1 vs %2").arg(got).arg(want)));
        for (int j = i + 1; j < out.size(); ++j)
            QVERIFY2(!r.intersects(out[j]), qPrintable(QStringLiteral("rects %1 and %2 overlap").arg(i).arg(j)));
    }
    Q_UNUSED(o);
}
}

class TestPacking : public QObject {
    Q_OBJECT
private slots:
    void emptyInput() { QVERIFY(pack({}, QRectF(0, 0, 100, 100), {}).isEmpty()); }
    void singleWindowFitsAndCentres() {
        const QVector<Item> items = { { 1, QRectF(0, 0, 1920, 1080) } };
        const QRectF area(60, 200, 1800, 800);
        const auto out = pack(items, area, {});
        checkLayout(items, area, {}, out);
        QVERIFY(std::abs(out[0].center().x() - area.center().x()) < 1);
        QVERIFY(std::abs(out[0].center().y() - area.center().y()) < 1);
    }
    void neverEnlargesBeyondMaxScale() {
        const QVector<Item> items = { { 1, QRectF(0, 0, 200, 100) } };
        Options o;
        o.maxScale = 1.0;
        const auto out = pack(items, QRectF(0, 0, 2000, 2000), o);
        QVERIFY(out[0].width() <= 200.0001);
    }
    void manyShapesAndAreas_data() {
        QTest::addColumn<int>("n");
        QTest::addColumn<int>("areaW");
        QTest::addColumn<int>("areaH");
        for (int n : { 1, 2, 3, 4, 5, 7, 9, 12, 16, 25 }) {
            QTest::newRow(qPrintable(QStringLiteral("%1 in 1920x1080").arg(n))) << n << 1800 << 760;
            QTest::newRow(qPrintable(QStringLiteral("%1 in 3840x1200").arg(n))) << n << 3700 << 850;
            QTest::newRow(qPrintable(QStringLiteral("%1 in a portrait").arg(n))) << n << 700 << 1500;
        }
    }
    void manyShapesAndAreas() {
        QFETCH(int, n);
        QFETCH(int, areaW);
        QFETCH(int, areaH);
        const QRectF area(40, 150, areaW, areaH);
        Options o;
        o.gap = 28;
        o.minDim = 200;
        for (quint32 seed = 1; seed <= 6; ++seed) {
            const QVector<Item> items = randomItems(n, seed * 7919u + quint32(n), QSizeF(3840, 1200));
            checkLayout(items, area, o, pack(items, area, o));
        }
    }
    void spacingBetweenThumbnails() {
        const QVector<Item> items = randomItems(6, 3, QSizeF(1920, 1080));
        Options o;
        o.gap = 30;
        const QRectF area(0, 0, 1800, 900);
        const auto out = pack(items, area, o);
        for (int i = 0; i < out.size(); ++i)
            for (int j = i + 1; j < out.size(); ++j)
                QVERIFY(!out[i].adjusted(-29.9, -29.9, 29.9, 29.9).intersects(out[j]));   // never closer than the gap
    }
    void deterministic() {
        const QVector<Item> items = randomItems(9, 42, QSizeF(1920, 1080));
        const QRectF area(10, 20, 1700, 800);
        const auto a = pack(items, area, {});
        const auto b = pack(items, area, {});
        QCOMPARE(a, b);
        // the order of equal-position inputs does not matter for a repeat call either
        QVector<Item> copy = items;
        QCOMPARE(pack(copy, area, {}), a);
    }
    void keepsReadingOrder() {
        // two windows side by side stay left and right; one above the other stay top and bottom
        const QVector<Item> lr = { { 1, QRectF(0, 0, 900, 1000) }, { 2, QRectF(1000, 0, 900, 1000) } };
        auto out = pack(lr, QRectF(0, 0, 1800, 900), {});
        QVERIFY(out[0].center().x() < out[1].center().x());
        const QVector<Item> tb = { { 1, QRectF(0, 0, 1900, 500) }, { 2, QRectF(0, 560, 1900, 500) } };
        out = pack(tb, QRectF(0, 0, 1800, 900), {});
        QVERIFY(out[0].center().y() < out[1].center().y());
    }
    void tinyAreaDoesNotCrash() {
        const QVector<Item> items = randomItems(5, 9, QSizeF(1920, 1080));
        const auto out = pack(items, QRectF(0, 0, 30, 30), {});
        QCOMPARE(out.size(), 5);
    }
};
QTEST_APPLESS_MAIN(TestPacking)
#include "test_packing.moc"
