// SPDX-License-Identifier: AGPL-3.0-or-later
#include <QFile>
#include <QImage>
#include <QJsonArray>
#include <QJsonDocument>
#include <QJsonObject>
#include <QtTest>

#include "glass.h"
#include "layoutplan.h"
#include "wallpaperprovider.h"

using namespace LayoutMath;

static const QString kBuiltin = QStringLiteral(TAOS_COMMON_DIR "/resources/themes/graphite/theme.json");

struct Extremes { QColor bright, dark; };

// Brightest and darkest local-mean colour inside `r` of `img`. The image is
// averaged over blocks of `block` px first: that is what a reader (and the
// frosted glass) sees; a single star pixel does not hide text.
static Extremes extremes(const QImage &img, const QRectF &r, int block, qreal brightness) {
    const QSize small(qMax(1, img.width() / block), qMax(1, img.height() / block));
    const QImage s = img.scaled(small, Qt::IgnoreAspectRatio, Qt::SmoothTransformation).convertToFormat(QImage::Format_RGB32);
    const qreal kx = qreal(small.width()) / img.width(), ky = qreal(small.height()) / img.height();
    const int x0 = qBound(0, int(r.left() * kx), small.width() - 1), x1 = qBound(x0, int(r.right() * kx), small.width() - 1);
    const int y0 = qBound(0, int(r.top() * ky), small.height() - 1), y1 = qBound(y0, int(r.bottom() * ky), small.height() - 1);
    double lo = 2, hi = -1;
    Extremes e;
    for (int y = y0; y <= y1; ++y)
        for (int x = x0; x <= x1; ++x) {
            QColor c = QColor::fromRgb(s.pixel(x, y));
            c = QColor::fromRgbF(qBound(0.0, c.redF() + brightness, 1.0), qBound(0.0, c.greenF() + brightness, 1.0), qBound(0.0, c.blueF() + brightness, 1.0));
            const double l = Contrast::luminance(c);
            if (l > hi) { hi = l; e.bright = c; }
            if (l < lo) { lo = l; e.dark = c; }
        }
    return e;
}

class TestContrast : public QObject {
    Q_OBJECT
    Theme dark, light;
private slots:
    void initTestCase() {
        QVERIFY(dark.load("graphite", "dark", {}, kBuiltin, false));
        QVERIFY(light.load("graphite", "light", {}, kBuiltin, false));
    }
    void textOnTheBareTintMeetsAA() {
        // The glass with nothing behind it at all (opaque ground colour).
        for (const Theme *t : {&dark, &light}) {
            const GlassSpec g = GlassSpec::forTheme(*t);
            const QColor floor = Contrast::over(g.veil, Contrast::over(g.tint, t->ground()));
            QVERIFY2(Contrast::ratio(g.text, floor) >= 7.0, qPrintable(t->variant() + " text on glass"));
            QVERIFY2(Contrast::ratio(g.placeholder, floor) >= 4.5, qPrintable(t->variant() + " placeholder on glass"));
        }
    }
    void sampleMath() {
        QVERIFY(qAbs(Contrast::ratio(Qt::black, Qt::white) - 21.0) < 0.01);
        QVERIFY(qAbs(Contrast::ratio(QColor("#777777"), Qt::white) - 4.48) < 0.02);
        QCOMPARE(Contrast::over(QColor(0, 0, 0, 128), Qt::white).red(), 127);
    }
    void wallpapersMeetAA() {
        const QString dir = WallpaperProvider::defaultDir();
        QFile f(dir + "/index.json");
        if (!f.open(QIODevice::ReadOnly)) QSKIP("no wallpaper tree (set TAOS_WALLPAPER_DIR)");
        const QJsonArray all = QJsonDocument::fromJson(f.readAll()).object().value("wallpapers").toArray();
        QVERIFY(!all.isEmpty());
        int checked = 0;
        QStringList failures;
        double worst = 99;
        for (const QJsonValue &w : all) {
            const QString id = w.toObject().value("id").toString();
            for (const Theme *t : {&dark, &light}) {
                const GlassSpec g = GlassSpec::forTheme(*t);
                const bool isLight = t->variant() == "light";
                const qreal bright = isLight ? 0.02 : -0.04;  // MultiEffect brightness in Main.qml
                const QJsonObject files = w.toObject().value("files").toObject().value(t->variant()).toObject();
                for (auto it = files.begin(); it != files.end(); ++it) {
                    QImage img(dir + "/" + it.value().toString());
                    QVERIFY2(!img.isNull(), qPrintable(it.value().toString()));
                    const Plan p = plan(QSizeF(img.size()));
                    const qreal s = p.s;
                    // Rectangles, as laid out in Main.qml.
                    const QRectF field((img.width() - p.fieldW) / 2, p.userBlock.top() + p.fieldTop, p.fieldW, p.fieldH);
                    const QRectF names(p.userBlock.left(), p.userBlock.top() + p.avatar + 10 * s, p.userBlock.width(), p.nameH);
                    const QRectF message(field.left(), field.bottom() + 10 * s, p.fieldW, p.messageH);
                    const QRectF power(p.bottomBar.right() - 260 * s, p.bottomBar.top(), 260 * s, p.bottomBar.height());
                    const QRectF host(p.bottomBar.left(), p.bottomBar.top(), 260 * s, p.bottomBar.height());
                    const QRectF clock(p.clockBlock.center().x() - p.clockPx * 1.5, p.clockBlock.top() + p.datePx * 1.3, p.clockPx * 3.0, p.clockPx);
                    const QRectF dateWx(p.clockBlock.center().x() - p.clockPx * 1.5, p.clockBlock.top(), p.clockPx * 3.0, p.datePx * 1.3);

                    struct Check { const char *what; QRectF r; int block; bool glass; QColor fg; double min; };
                    QColor dateFg = g.text; dateFg.setAlphaF(0.92);
                    const Check checks[] = {
                        {"field text", field, 32, true, g.text, 4.5},
                        {"field placeholder", field, 32, true, g.placeholder, 4.5},
                        {"power buttons", power, 32, true, g.text, 4.5},
                        {"clock digits (large)", clock, 24, false, g.text, 3.0},
                        {"date", dateWx, 24, false, dateFg, 4.5},
                        {"user names", names, 24, false, g.text, 4.5},
                        {"hostname", host, 24, false, g.placeholder, 4.5},
                        {"error or info message", message, 24, false, g.text, 4.5},
                    };
                    for (const Check &c : checks) {
                        const Extremes e = extremes(img, c.r, c.block, bright);
                        double lowest = 99;
                        for (const QColor &back : {e.bright, e.dark}) {
                            const QColor under = c.glass ? Contrast::over(g.veil, Contrast::over(g.tint, back)) : Contrast::over(g.scrim, back);
                            const QColor fg = Contrast::over(c.fg, under);
                            lowest = qMin(lowest, Contrast::ratio(fg, under));
                        }
                        ++checked;
                        worst = qMin(worst, lowest - c.min);
                        if (lowest < c.min)
                            failures << QString("%1 %2 %3 %4x%5 %6: %7 < %8").arg(id, t->variant(), it.key()).arg(img.width()).arg(img.height())
                                            .arg(c.what).arg(lowest, 0, 'f', 2).arg(c.min);
                    }
                }
            }
        }
        qInfo().noquote() << "contrast checks:" << checked << "tightest margin above minimum:" << QString::number(worst, 'f', 2);
        QVERIFY2(checked >= 10 * 3 * 2, "sampled every shipped wallpaper");
        QVERIFY2(failures.isEmpty(), qPrintable("\n" + failures.join("\n")));
    }
};
QTEST_MAIN(TestContrast)
#include "test_contrast.moc"
