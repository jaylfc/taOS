// SPDX-License-Identifier: AGPL-3.0-or-later
#include <QtTest>

#include "contrast.h"
#include "theme.h"
#include "themecatalog.h"
#include "testenv.h"

class TestThemes : public QObject {
    Q_OBJECT
private slots:
    void goodThemePasses() {
        const ThemeParse p = parseTheme(goodTheme("mine"), "mine", "x");
        QVERIFY2(p.ok, qPrintable(p.message()));
        QCOMPARE(p.def.name, QStringLiteral("Theme mine"));
        QCOMPARE(p.def.swatches.value("dark").size(), 3);  // derived: ground, surface, accent
        QCOMPARE(p.def.colors.value("dark")[ThemeTokens::Stroke].alpha(), 0x14);
    }
    void windowButtonsKey() {
        QJsonObject o = goodTheme("mine");
        QCOMPARE(parseTheme(o, "mine", "x").def.windowButtons, QStringLiteral("left"));   // default
        o["windowButtons"] = "right";
        const ThemeParse r = parseTheme(o, "mine", "x");
        QVERIFY2(r.ok, qPrintable(r.message()));
        QCOMPARE(r.def.windowButtons, QStringLiteral("right"));
        o["windowButtons"] = "middle";
        const ThemeParse bad = parseTheme(o, "mine", "x");
        QVERIFY(!bad.ok);
        QVERIFY2(bad.message().contains("windowButtons must be"), qPrintable(bad.message()));
        o["windowButtons"] = 3;
        QVERIFY(!parseTheme(o, "mine", "x").ok);
    }
    void themeSideAndOverride() {
        QJsonObject o = goodTheme("mine");
        o["windowButtons"] = "right";
        const ThemeParse p = parseTheme(o, "mine", "x");
        Theme t;
        QSignalSpy changed(&t, &Theme::changed);
        QVERIFY(t.apply(p.def, "dark", false));
        QCOMPARE(t.windowButtons(), QStringLiteral("right"));   // from the theme
        t.setWindowButtonsOverride("left");                      // the person's choice wins
        QCOMPARE(t.windowButtons(), QStringLiteral("left"));
        const int n = changed.count();
        t.setWindowButtonsOverride("left");
        QCOMPARE(changed.count(), n);                            // no change, no signal
        t.setWindowButtonsOverride(QString());                   // back to the theme
        QCOMPARE(t.windowButtons(), QStringLiteral("right"));
        t.setWindowButtonsOverride("sideways");                  // junk follows the theme
        QCOMPARE(t.windowButtons(), QStringLiteral("right"));
    }
    void missingTokenRefused() {
        QJsonObject o = goodTheme("mine");
        QJsonObject colors = o["colors"].toObject();
        QJsonObject dark = colors["dark"].toObject();
        dark.remove("warning");
        colors["dark"] = dark;
        o["colors"] = colors;
        const ThemeParse p = parseTheme(o, "mine", "x");
        QVERIFY(!p.ok);
        QVERIFY2(p.message().contains("'warning' is missing"), qPrintable(p.message()));
    }
    void notHexRefused() {
        QJsonObject o = goodTheme("mine");
        QJsonObject colors = o["colors"].toObject();
        QJsonObject light = colors["light"].toObject();
        light["accent"] = "red";
        colors["light"] = light;
        o["colors"] = colors;
        const ThemeParse p = parseTheme(o, "mine", "x");
        QVERIFY(!p.ok);
        QVERIFY2(p.message().contains("'accent' is not a hex colour"), qPrintable(p.message()));
    }
    void lowContrastRefused() {
        QJsonObject o = goodTheme("mine");
        QJsonObject colors = o["colors"].toObject();
        QJsonObject dark = colors["dark"].toObject();
        dark["text"] = "#2A2A2E";  // text almost the surface colour
        colors["dark"] = dark;
        o["colors"] = colors;
        const ThemeParse p = parseTheme(o, "mine", "x");
        QVERIFY(!p.ok);
        QVERIFY2(p.message().contains("text on surface"), qPrintable(p.message()));
        QVERIFY2(p.message().contains("4.5:1"), qPrintable(p.message()));
    }
    void contrastJustAbove() {
        // 4.5:1 is the line: build text a hair either side of it on the same surface.
        const QColor surface = parseCssColor("#18181B");
        int refusedAt = -1;
        for (int v = 0x20; v < 0xA0; ++v) {
            if (Contrast::ratio(QColor(v, v, v), surface) >= kMinContrast) { refusedAt = v; break; }
        }
        QVERIFY(refusedAt > 0);
        auto withText = [&](int v) {
            QJsonObject o = goodTheme("mine");
            QJsonObject colors = o["colors"].toObject();
            QJsonObject dark = colors["dark"].toObject();
            dark["text"] = QColor(v, v, v).name().toUpper();
            dark["ground"] = "#18181B";
            colors["dark"] = dark;
            o["colors"] = colors;
            return parseTheme(o, "mine", "x");
        };
        QVERIFY(withText(refusedAt).ok);
        QVERIFY(!withText(refusedAt - 1).ok);
    }
    void idMustMatchFolder() {
        QVERIFY(!parseTheme(goodTheme("mine"), "other", "x").ok);
        QVERIFY(!parseTheme(goodTheme("Bad Id"), "", "x").ok);
    }
    void notJsonRefused() {
        TestHome h;
        QDir().mkpath(h.userThemes() + "/broken");
        QFile f(h.userThemes() + "/broken/theme.json");
        f.open(QIODevice::WriteOnly);
        f.write("{not json");
        f.close();
        ThemeCatalog c;
        c.scan(ThemeCatalog::standardDirs(true));
        QCOMPARE(c.refused().size(), 1);
        QVERIFY(c.refused()[0].message.contains("not valid JSON"));
    }
    void shippedThemes() {
        TestHome h;
        ThemeCatalog c;
        c.scan(ThemeCatalog::standardDirs(true));
        QVERIFY2(c.refused().isEmpty(), qPrintable(c.refused().value(0).message));
        QStringList ids;
        for (const ThemeDef &d : c.themes()) ids << d.id;
        QCOMPARE(ids, QStringList({"graphite", "graphite-ice", "graphite-warm"}));
        QVERIFY(!c.find("taos-default"));
        QCOMPARE(c.find("graphite")->name, QStringLiteral("Graphite"));
        QCOMPARE(c.find("graphite-ice")->name, QStringLiteral("Graphite + Ice"));
        QCOMPARE(c.find("graphite-warm")->name, QStringLiteral("Warm Graphite"));
        for (const ThemeDef &d : c.themes()) {
            QVERIFY(!d.description.isEmpty());
            QCOMPARE(d.variants, QStringList({"dark", "light"}));
            for (const QString &v : d.variants) QCOMPARE(d.swatches.value(v).size(), 3);
        }
    }
    void warmWarningIsNotTheAccent() {
        TestHome h;
        ThemeCatalog c;
        c.scan(ThemeCatalog::standardDirs(true));
        const ThemeDef *w = c.find("graphite-warm");
        QVERIFY(w);
        for (const QString &v : w->variants) {
            const TokenSet &t = w->colors.value(v);
            double dh = qAbs(t[ThemeTokens::Warning].hslHueF() - t[ThemeTokens::Accent].hslHueF()) * 360.0;
            dh = qMin(dh, 360.0 - dh);
            QVERIFY2(dh >= 25.0, qPrintable(QString("%1 hue gap %2").arg(v).arg(dh)));
            double dd = qAbs(t[ThemeTokens::Warning].hslHueF() - t[ThemeTokens::Danger].hslHueF()) * 360.0;
            QVERIFY(dd >= 25.0);
            QVERIFY(Contrast::ratio(t[ThemeTokens::Warning], t[ThemeTokens::Surface]) >= 4.5);
            QVERIFY(Contrast::ratio(t[ThemeTokens::Warning], t[ThemeTokens::Ground]) >= 4.5);
        }
    }
    void shippedThemesMeetContrast() {
        TestHome h;
        ThemeCatalog c;
        c.scan(ThemeCatalog::standardDirs(true));
        for (const ThemeDef &d : c.themes())
            for (const QString &v : d.variants) {
                const TokenSet &t = d.colors.value(v);
                for (int fg : {ThemeTokens::Text, ThemeTokens::TextSecondary, ThemeTokens::TextTertiary})
                    QVERIFY2(Contrast::ratio(t[fg], t[ThemeTokens::Surface]) >= 4.5, qPrintable(d.id + v + ThemeTokens::name(fg)));
                QVERIFY2(Contrast::ratio(t[ThemeTokens::AccentText], t[ThemeTokens::Accent]) >= 4.5, qPrintable(d.id + v));
                // placeholder text sits on a field: the text colour at 6 % over the page, raised or ground colour (apps Look.field)
                for (int bg : {ThemeTokens::Ground, ThemeTokens::Surface, ThemeTokens::Raised}) {
                    QColor fld = Contrast::over(QColor::fromRgbF(t[ThemeTokens::Text].redF(), t[ThemeTokens::Text].greenF(), t[ThemeTokens::Text].blueF(), 0.06), t[bg]);
                    QVERIFY2(Contrast::ratio(t[ThemeTokens::TextTertiary], fld) >= 4.5, qPrintable(d.id + v + QStringLiteral(" tertiary on a field over ") + ThemeTokens::name(bg)));
                    QVERIFY2(Contrast::ratio(t[ThemeTokens::TextTertiary], t[bg]) >= 4.5, qPrintable(d.id + v + QStringLiteral(" tertiary on ") + ThemeTokens::name(bg)));
                }
            }
    }
    void thirdPartyThemesLoadFromUserAndSystem() {
        TestHome h;
        writeTheme(h.userThemes(), "mine", goodTheme("mine"));
        writeTheme(h.systemThemes(), "shared", goodTheme("shared"));
        ThemeCatalog c;
        c.scan(ThemeCatalog::standardDirs(true));
        QVERIFY(c.find("mine"));
        QVERIFY(c.find("shared"));
        QCOMPARE(c.themes().last().id, QStringLiteral("shared"));  // shipped first, then by name
        // The greeter does not read a person's home.
        ThemeCatalog g;
        g.scan(ThemeCatalog::standardDirs(false));
        QVERIFY(!g.find("mine"));
        QVERIFY(g.find("shared"));
    }
    void shippedCannotBeShadowed() {
        TestHome h;
        QJsonObject o = goodTheme("graphite", "#FF0000");
        writeTheme(h.userThemes(), "graphite", o);
        ThemeCatalog c;
        c.scan(ThemeCatalog::standardDirs(true));
        QVERIFY(c.find("graphite")->builtin);
    }
    void unknownSavedThemeFallsBack() {
        TestHome h;
        ThemeCatalog c;
        c.scan(ThemeCatalog::standardDirs(true));
        QCOMPARE(c.resolve("taos-default")->id, QStringLiteral("graphite"));
        QCOMPARE(c.resolve("")->id, QStringLiteral("graphite"));
    }
    void invalidThemeIsNotChosenEvenIfSaved() {
        TestHome h;
        QJsonObject o = goodTheme("bad");
        o.remove("name");
        writeTheme(h.userThemes(), "bad", o);
        ThemeCatalog c;
        c.scan(ThemeCatalog::standardDirs(true));
        QVERIFY(!c.find("bad"));
        QCOMPARE(c.refused().size(), 1);
        QCOMPARE(c.resolve("bad")->id, QStringLiteral("graphite"));
    }
    void themeObjectLoadsAndNotifies() {
        TestHome h;
        ThemeCatalog c;
        c.scan(ThemeCatalog::standardDirs(true));
        Theme t;
        QSignalSpy spy(&t, &Theme::changed);
        QVERIFY(t.apply(*c.find("graphite"), "dark", false));
        QCOMPARE(spy.count(), 1);
        QCOMPARE(t.ground().name().toUpper(), QStringLiteral("#0B0B0C"));
        QCOMPARE(t.barBackground().alpha(), 184);
        QVERIFY(t.apply(*c.find("graphite"), "dark", false));  // same again: nothing happens
        QCOMPARE(spy.count(), 1);
        QVERIFY(t.apply(*c.find("graphite-ice"), "light", false));
        QCOMPARE(spy.count(), 2);
        QCOMPARE(t.accent().name().toUpper(), QStringLiteral("#1F5FD1"));
        QVERIFY(!t.dark());
        // Glass and scrim come from the tokens, not literals: the warm theme's scrim is warm.
        QVERIFY(t.apply(*c.find("graphite-warm"), "dark", false));
        QCOMPARE(t.scrim().red(), t.ground().red());
        QCOMPARE(t.scrim().alpha(), 89);
    }
    void crossFade() {
        TestHome h;
        ThemeCatalog c;
        c.scan(ThemeCatalog::standardDirs(true));
        Theme t;
        t.setTransitionMs(150);
        t.apply(*c.find("graphite"), "dark", false);
        QSignalSpy spy(&t, &Theme::changed);
        t.apply(*c.find("graphite"), "light", false);
        QVERIFY(t.transitioning());
        QVERIFY(spy.wait(1000));
        QTRY_VERIFY_WITH_TIMEOUT(!t.transitioning(), 1000);
        QVERIFY(spy.count() >= 3);  // frames in between
        QCOMPARE(t.ground(), c.find("graphite")->colors.value("light")[ThemeTokens::Ground]);
    }
    void reducedMotionDoesNotFade() {
        TestHome h;
        ThemeCatalog c;
        c.scan(ThemeCatalog::standardDirs(true));
        Theme t;
        t.setTransitionMs(150);
        t.apply(*c.find("graphite"), "dark", true);
        QSignalSpy spy(&t, &Theme::changed);
        t.apply(*c.find("graphite"), "light", true);
        QVERIFY(!t.transitioning());
        QCOMPARE(spy.count(), 1);
        QCOMPARE(t.ground(), c.find("graphite")->colors.value("light")[ThemeTokens::Ground]);
    }
    void loadHelperFallsBackToTheBuiltinFile() {
        TestHome h;
        Theme t;
        QString err;
        QVERIFY(t.load("nope", "dark", {}, TAOS_COMMON_DIR "/resources/themes/graphite/theme.json", false, &err));
        QCOMPARE(t.id(), QStringLiteral("graphite"));
        QVERIFY(!t.load("x", "dark", {}, "/nonexistent.json", false, &err));
        QVERIFY(!err.isEmpty());
    }
};

QTEST_MAIN(TestThemes)
#include "test_themes.moc"
