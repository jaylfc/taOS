// SPDX-License-Identifier: AGPL-3.0-or-later
#include <QtTest>

#include "cli/themecli.h"
#include "theme.h"
#include "themecatalog.h"
#include "thememanager.h"
#include "testenv.h"

// Theme package v2: the optional skin and layout sections. Validation (bad values, unknown keys, defaults when
// absent), what the Theme object does with them, and a broken edit leaving the shown theme alone.
static ThemeParse parse(QJsonObject o, const QJsonObject &skin = {}, const QJsonObject &layout = {}) {
    if (!skin.isEmpty()) o["skin"] = skin;
    if (!layout.isEmpty()) o["layout"] = layout;
    return parseTheme(o, "mine", "x");
}
static QString runCli(const QStringList &args, int *rc, QString *err = nullptr) {
    QString o, e;
    QTextStream so(&o), se(&e);
    *rc = runThemeCli(args, so, se);
    so.flush();
    se.flush();
    if (err) *err = e;
    return o;
}

class TestSkinLayout : public QObject {
    Q_OBJECT
private slots:
    void absentSectionsAreTodaysShell() {
        const ThemeParse p = parse(goodTheme("mine"));
        QVERIFY2(p.ok, qPrintable(p.message()));
        QVERIFY(p.def.skin == Skin());
        QVERIFY(p.def.layout == LayoutSpec());
        QCOMPARE(p.def.skin.radiusScale, 1.0);
        QCOMPARE(p.def.skin.fontSize, 13);
        QCOMPARE(p.def.skin.titleBarHeight, 32);
        QCOMPARE(p.def.skin.windowGap, 8);
        QVERIFY(p.def.layout.topBar);
        QCOMPARE(p.def.layout.dock, QStringLiteral("dock"));
    }
    void shippedThemesKeepTodaysDefaults() {
        TestHome h;
        ThemeCatalog c;
        c.scan(ThemeCatalog::standardDirs(true));
        for (const char *id : {"graphite", "graphite-ice", "graphite-warm"}) {
            QVERIFY(c.find(id));
            QVERIFY2(c.find(id)->skin == Skin(), id);
            QVERIFY2(c.find(id)->layout == LayoutSpec(), id);
        }
    }
    void goodSkinAndLayoutAreRead() {
        const ThemeParse p = parse(goodTheme("mine"),
            {{"radiusScale", 0}, {"borderWidth", 2}, {"shadowStrength", 0.5}, {"fontFamily", "DejaVu Sans"}, {"fontSize", 12},
             {"iconStroke", 1.25}, {"titleBarHeight", 26}, {"barSurface", "raised"}, {"dockSurface", "solid"}, {"density", "compact"},
             {"activeTitle", "accent"}, {"windowRadius", 4}, {"windowGap", 0},
             {"windowButtons", QJsonObject{{"shape", "square"}, {"size", 16}, {"side", "right"}, {"order", QJsonArray{"minimise", "full", "close"}}}}},
            {{"topBar", false}, {"dock", "taskbar"}, {"taskbarHeight", 38}, {"dockEdge", "top"}, {"launcher", "anchored"}, {"startLabel", "Apps"}});
        QVERIFY2(p.ok, qPrintable(p.message()));
        QCOMPARE(p.def.skin.radiusScale, 0.0);
        QCOMPARE(p.def.skin.buttonShape, QStringLiteral("square"));
        QCOMPARE(p.def.windowButtons, QStringLiteral("right"));   // the side comes into the existing field
        QCOMPARE(p.def.skin.buttonOrder, (QStringList{"minimise", "full", "close"}));
        QVERIFY(!p.def.layout.topBar);
        QCOMPARE(p.def.layout.taskbarHeight, 38);
    }
    void everyLauncherStyleIsAccepted() {
        // the launcher styles are renderers of one shared menu: the theme only names which one it wants
        for (const char *style : { "centered", "anchored", "ring", "text" }) {
            const bool taskbar = QByteArray(style) == "anchored";
            const ThemeParse p = parse(goodTheme("mine"), {}, taskbar ? QJsonObject{{"dock", "taskbar"}, {"launcher", style}} : QJsonObject{{"launcher", style}});
            QVERIFY2(p.ok, qPrintable(QString(style) + ": " + p.message()));
            QCOMPARE(p.def.layout.launcher, QString(style));
        }
        const ThemeParse bad = parse(goodTheme("mine"), {}, {{"launcher", "olauncher"}});
        QVERIFY(!bad.ok);
        QVERIFY2(bad.message().contains("centered, anchored, ring, text"), qPrintable(bad.message()));   // the refusal names every style
    }
    void unknownKeysAreRefusedByName() {
        ThemeParse p = parse(goodTheme("mine"), {{"radiusScal", 1}});
        QVERIFY(!p.ok);
        QVERIFY2(p.message().contains("skin: unknown key 'radiusScal'"), qPrintable(p.message()));
        p = parse(goodTheme("mine"), {}, {{"taskbar", true}});
        QVERIFY(!p.ok);
        QVERIFY2(p.message().contains("layout: unknown key 'taskbar'"), qPrintable(p.message()));
        p = parse(goodTheme("mine"), {{"windowButtons", QJsonObject{{"colour", "red"}}}});
        QVERIFY2(p.message().contains("skin.windowButtons: unknown key 'colour'"), qPrintable(p.message()));
    }
    void rangesAndTypesAreChecked() {
        struct Case { const char *section; QJsonObject body; const char *expect; };
        const QVector<Case> bad = {
            {"skin", {{"radiusScale", 3}}, "skin.radiusScale is 3, it must be between 0 and 2"},
            {"skin", {{"radiusScale", "big"}}, "skin.radiusScale must be a number"},
            {"skin", {{"fontSize", 8}}, "skin.fontSize is 8"},
            {"skin", {{"fontSize", 12.5}}, "skin.fontSize must be a whole number"},
            {"skin", {{"borderWidth", -1}}, "skin.borderWidth is -1"},
            {"skin", {{"titleBarHeight", 60}}, "skin.titleBarHeight is 60"},
            {"skin", {{"iconStroke", 0.1}}, "skin.iconStroke is 0.1"},
            {"skin", {{"barSurface", "glass"}}, "skin.barSurface must be one of: solid, translucent, raised"},
            {"skin", {{"density", 2}}, "skin.density must be one of"},
            {"skin", {{"windowGap", 99}}, "skin.windowGap is 99"},
            {"layout", {{"barHeight", 10}}, "layout.barHeight is 10"},
            {"layout", {{"dock", "start"}}, "layout.dock must be one of: dock, taskbar"},
            {"layout", {{"topBar", "no"}}, "layout.topBar must be true or false"},
            {"layout", {{"launcher", "centred"}}, "layout.launcher must be one of"},
        };
        for (const Case &c : bad) {
            const ThemeParse p = c.section == QByteArray("skin") ? parse(goodTheme("mine"), c.body) : parse(goodTheme("mine"), {}, c.body);
            QVERIFY2(!p.ok, c.expect);
            QVERIFY2(p.message().contains(c.expect), qPrintable(QString(c.expect) + " not in: " + p.message()));
        }
    }
    void sectionsMustBeObjects() {
        QJsonObject o = goodTheme("mine");
        o["skin"] = "flat";
        QVERIFY(parse(o).message().contains("skin must be an object"));
    }
    void aThemeCannotInjectIntoSwayCommands() {
        for (const char *f : {"Inter; exec x", "A\"B", "../x", "A,B", ""}) {
            const ThemeParse p = parse(goodTheme("mine"), {{"fontFamily", f}});
            QVERIFY2(!p.ok, f);
            QVERIFY(p.message().contains("fontFamily"));
        }
        for (const char *w : {"../wall.png", "/etc/passwd.png", "a b.png", "wall.svg", "w\".png", "a/b.png"}) {
            const ThemeParse p = parse(goodTheme("mine"), {{"wallpaper", w}});
            QVERIFY2(!p.ok, w);
            QVERIFY(p.message().contains("skin.wallpaper"));
        }
        QVERIFY(validFontFamily("IBM Plex Mono"));
        QVERIFY(!validFontFamily("a'b"));
    }
    void wallpaperMustExistInTheThemeFolder() {
        TestHome h;
        QJsonObject o = goodTheme("mine");
        o["skin"] = QJsonObject{{"wallpaper", "wall.png"}};
        writeTheme(h.userThemes(), "mine", o);
        ThemeParse p = parseThemeFile(h.userThemes() + "/mine/theme.json", "mine");
        QVERIFY(!p.ok);
        QVERIFY2(p.message().contains("'wall.png' is not in the theme folder"), qPrintable(p.message()));
        QFile f(h.userThemes() + "/mine/wall.png");
        QVERIFY(f.open(QIODevice::WriteOnly));
        f.write("x");
        f.close();
        p = parseThemeFile(h.userThemes() + "/mine/theme.json", "mine");
        QVERIFY2(p.ok, qPrintable(p.message()));
    }
    void noTopBarNeedsATaskbar() {
        const ThemeParse p = parse(goodTheme("mine"), {}, {{"topBar", false}});
        QVERIFY(!p.ok);
        QVERIFY2(p.message().contains("with no top bar the dock must be a taskbar"), qPrintable(p.message()));
        QVERIFY(parse(goodTheme("mine"), {}, {{"topBar", false}, {"dock", "taskbar"}}).ok);
    }
    void buttonOrderAndSideAreChecked() {
        ThemeParse p = parse(goodTheme("mine"), {{"windowButtons", QJsonObject{{"order", QJsonArray{"close", "close", "full"}}}}});
        QVERIFY(p.message().contains("must list close, minimise and full once each"));
        QJsonObject o = goodTheme("mine");
        o["windowButtons"] = "left";
        o["skin"] = QJsonObject{{"windowButtons", QJsonObject{{"side", "right"}}}};
        QVERIFY(parse(o).message().contains("contradicts the top level windowButtons"));
        o["skin"] = QJsonObject{{"windowButtons", QJsonObject{{"side", "left"}}}};
        QVERIFY(parse(o).ok);
    }
    void themeObjectFollowsSkinAndLayoutOncePerSwitch() {
        Theme t;
        QSignalSpy skin(&t, &Theme::skinChanged), lay(&t, &Theme::layoutChanged);
        ThemeParse plain = parse(goodTheme("mine"));
        QVERIFY(t.apply(plain.def, "dark", false));
        QCOMPARE(t.radius(8), 8.0);                 // today
        QCOMPARE(t.fontSize(), 13);
        QCOMPARE(t.buttonOrder(), (QStringList{"close", "minimise", "full"}));
        QCOMPARE(t.dockMode(), QStringLiteral("dock"));
        skin.clear();
        lay.clear();
        ThemeParse wb = parse(goodTheme("mine"), {{"radiusScale", 0}, {"fontSize", 12}, {"density", "compact"}, {"windowButtons", QJsonObject{{"side", "right"}}}},
                              {{"topBar", false}, {"dock", "taskbar"}, {"taskbarHeight", 40}});
        QVERIFY2(wb.ok, qPrintable(wb.message()));
        t.setTransitionMs(150);
        QVERIFY(t.apply(wb.def, "dark", false));
        QCOMPARE(skin.count(), 1);                  // once, not once per fade frame
        QCOMPARE(lay.count(), 1);
        QCOMPARE(t.radius(8), 0.0);
        QCOMPARE(t.fontSize(), 12);
        QCOMPARE(t.space(10), 8.0);                 // compact: 0.75, rounded
        QVERIFY(!t.topBar());
        QCOMPARE(t.windowButtons(), QStringLiteral("right"));
        QCOMPARE(t.buttonOrder(), (QStringList{"minimise", "full", "close"}));
        QVERIFY(t.trayWidth() > 100);
        QCOMPARE(t.fontStack(), QStringLiteral("Inter, Noto Sans, DejaVu Sans, sans-serif"));
        // a theme with the same colours and a changed skin is not "the same": it is applied
        wb.def.skin.fontSize = 14;
        QVERIFY(t.apply(wb.def, "dark", false));
        QCOMPARE(t.fontSize(), 14);
        QCOMPARE(skin.count(), 2);
        QCOMPARE(lay.count(), 1);
        // and back to the plain one
        QVERIFY(t.apply(plain.def, "dark", false));
        QCOMPARE(t.fontSize(), 13);
        QVERIFY(t.topBar());
    }
    void aBrokenEditLeavesTheShownThemeAlone() {
        TestHome h;
        QJsonObject o = goodTheme("mine");
        o["skin"] = QJsonObject{{"fontSize", 12}};
        writeTheme(h.userThemes(), "mine", o);
        Appearance a;
        a.theme = "mine";
        QVERIFY(AppearanceFile::write(h.appearance(), a));
        Theme t;
        ThemeManager::Options opt;
        opt.transitionMs = 0;
        ThemeManager m(&t, opt);
        QVERIFY(m.start());
        QCOMPARE(m.themeId(), QStringLiteral("mine"));
        QCOMPARE(t.fontSize(), 12);
        // the edit breaks it: an unknown key
        o["skin"] = QJsonObject{{"fontSize", 12}, {"blink", true}};
        writeTheme(h.userThemes(), "mine", o);
        m.reload();
        QCOMPARE(m.themeId(), QStringLiteral("mine"));      // not graphite: what showed before stays
        QCOMPARE(t.fontSize(), 12);
        QVERIFY2(m.lastError().contains("unknown key 'blink'"), qPrintable(m.lastError()));
        // the edit is fixed: it applies, the error clears
        o["skin"] = QJsonObject{{"fontSize", 15}};
        writeTheme(h.userThemes(), "mine", o);
        m.reload();
        QCOMPARE(t.fontSize(), 15);
        QVERIFY(m.lastError().isEmpty());
    }
    void startupWithABrokenThemeFallsBackToTheDefault() {
        TestHome h;
        QJsonObject o = goodTheme("mine");
        o["skin"] = QJsonObject{{"fontSize", 99}};
        writeTheme(h.userThemes(), "mine", o);
        Appearance a;
        a.theme = "mine";
        QVERIFY(AppearanceFile::write(h.appearance(), a));
        Theme t;
        ThemeManager m(&t, ThemeManager::Options());
        QVERIFY(m.start());
        QCOMPARE(m.themeId(), QStringLiteral("graphite"));
    }
    void cliValidate() {
        TestHome h;
        int rc = 0;
        QString err;
        QJsonObject o = goodTheme("mine");
        writeTheme(h.dir.path(), "mine", o);
        QString out = runCli({"validate", h.dir.path() + "/mine/theme.json"}, &rc, &err);
        QCOMPARE(rc, 0);
        QVERIFY2(out.startsWith("OK mine"), qPrintable(out));
        o["skin"] = QJsonObject{{"fontSize", 99}, {"bogus", 1}};
        o["layout"] = QJsonObject{{"dock", "start"}};
        writeTheme(h.dir.path(), "mine", o);
        out = runCli({"validate", h.dir.path() + "/mine"}, &rc, &err);     // a folder works too
        QCOMPARE(rc, 1);
        QVERIFY(err.contains("skin.fontSize is 99"));
        QVERIFY(err.contains("unknown key 'bogus'"));
        QVERIFY(err.contains("layout.dock must be one of"));
        QVERIFY(err.contains("3 problems"));
        runCli({"validate", h.dir.path() + "/nothing.json"}, &rc, &err);
        QCOMPARE(rc, 3);
        runCli({"validate"}, &rc, &err);
        QCOMPARE(rc, 2);
    }
    static QJsonObject withOptions(QJsonObject o) {
        QJsonObject choices{{"signal", QJsonObject{{"label", "Signal"}, {"colors", QJsonObject{{"accent", "#7FB2FF"}}}}},
                            {"ember", QJsonObject{{"label", "Ember"}, {"colors", QJsonObject{{"accent", "#E8A23D"}}}}},
                            {"depth", QJsonObject{{"label", "Depth"}, {"available", false}, {"note", "later"}}}};
        o["options"] = QJsonObject{{"accent", QJsonObject{{"label", "Accent"}, {"default", "signal"}, {"choices", choices}}}};
        return o;
    }
    void optionsAreReadAndApplied() {
        const ThemeParse p = parse(withOptions(goodTheme("mine")));
        QVERIFY2(p.ok, qPrintable(p.message()));
        QCOMPARE(p.def.options.size(), 1);
        QCOMPARE(p.def.options[0].choices.size(), 3);
        const ThemeDef def = applyOptions(p.def, {{"accent", "ember"}});
        QCOMPARE(def.colors.value("dark")[ThemeTokens::Accent], parseCssColor("#E8A23D"));
        QCOMPARE(applyOptions(p.def, {}).colors.value("dark")[ThemeTokens::Accent], parseCssColor("#7FB2FF"));    // the default
        QCOMPARE(applyOptions(p.def, {{"accent", "nonsense"}}).colors.value("dark")[ThemeTokens::Accent], parseCssColor("#7FB2FF"));
        QStringList notes;
        applyOptions(p.def, {{"accent", "depth"}}, &notes);                      // saved but not available: said out loud
        QVERIFY2(notes.value(0).contains("Depth is not available on this system yet"), qPrintable(notes.join(";")));
    }
    void optionsAreValidated() {
        QJsonObject o = withOptions(goodTheme("mine"));
        QJsonObject opt = o["options"].toObject();
        QJsonObject acc = opt["accent"].toObject();
        QJsonObject ch = acc["choices"].toObject();
        ch["bad"] = QJsonObject{{"label", "Bad"}, {"colors", QJsonObject{{"accentText", "#7FB2FF"}, {"accent", "#7FB2FF"}}}};   // text on accent unreadable
        acc["choices"] = ch;
        opt["accent"] = acc;
        o["options"] = opt;
        ThemeParse p = parse(o);
        QVERIFY(!p.ok);
        QVERIFY2(p.message().contains("options.accent.choices.bad: dark: accentText on accent has a contrast"), qPrintable(p.message()));
        o = withOptions(goodTheme("mine"));
        acc = o["options"].toObject()["accent"].toObject();
        acc["default"] = "nope";
        acc["extra"] = 1;
        o["options"] = QJsonObject{{"accent", acc}};
        p = parse(o);
        QVERIFY(p.message().contains("default must name one of the choices"));
        QVERIFY(p.message().contains("unknown key 'extra'"));
    }
    void optionsPersistPerPersonAndApplyLive() {
        TestHome h;
        QJsonObject o = withOptions(goodTheme("mine"));
        writeTheme(h.userThemes(), "mine", o);
        Appearance a;
        a.theme = "mine";
        QVERIFY(AppearanceFile::write(h.appearance(), a));
        Theme t;
        ThemeManager::Options opt;
        opt.transitionMs = 0;
        ThemeManager m(&t, opt);
        QVERIFY(m.start());
        QCOMPARE(t.accent(), parseCssColor("#7FB2FF"));
        QString err;
        QVERIFY(m.setOption("mine", "accent", "ember", &err));
        QCOMPARE(t.accent(), parseCssColor("#E8A23D"));              // live
        Appearance saved;
        QVERIFY(AppearanceFile::read(h.appearance(), &saved));
        QCOMPARE(saved.options.value("mine").value("accent"), QStringLiteral("ember"));    // saved
        QVERIFY(!m.setOption("mine", "accent", "nope", &err));
        QVERIFY(err.contains("not a choice"));
        QVERIFY(!m.setOption("mine", "nokey", "x", &err));
        QVERIFY(m.setOption("mine", "accent", "depth", &err));       // accepted, reported, not shown
        QVERIFY2(m.notes().value(0).contains("not available on this system yet"), qPrintable(m.notes().join(";")));
        QCOMPARE(t.accent(), parseCssColor("#7FB2FF"));
        // a theme switch keeps the choices
        QVERIFY(m.setTheme("graphite"));
        QVERIFY(AppearanceFile::read(h.appearance(), &saved));
        QCOMPARE(saved.options.value("mine").value("accent"), QStringLiteral("depth"));
    }
    void cliOptions() {
        TestHome h;
        writeTheme(h.userThemes(), "mine", withOptions(goodTheme("mine")));
        int rc = 0;
        QString err;
        QString out = runCli({"options", "mine"}, &rc, &err);
        QCOMPARE(rc, 0);
        QVERIFY2(out.contains("[signal]") && out.contains("depth (not available yet)"), qPrintable(out));
        out = runCli({"option", "mine", "accent", "ember"}, &rc, &err);
        QCOMPARE(rc, 0);
        QVERIFY(runCli({"options", "mine"}, &rc).contains("[ember]"));
        out = runCli({"option", "mine", "accent", "depth"}, &rc, &err);
        QCOMPARE(rc, 0);
        QVERIFY2(out.contains("not available on this system yet"), qPrintable(out));
        runCli({"option", "mine", "accent", "nope"}, &rc, &err);
        QCOMPARE(rc, 1);
        QVERIFY(err.contains("is not a choice of Accent"));
        runCli({"option", "mine", "nokey", "x"}, &rc, &err);
        QCOMPARE(rc, 1);
        runCli({"option", "mine"}, &rc, &err);
        QCOMPARE(rc, 2);
    }
    void bundledThemesShip() {
        for (const char *id : {"parallax", "workbench"}) {
            int rc = 0;
            QString err;
            runCli({"validate", QStringLiteral(TAOS_COMMON_DIR "/../themes/") + id}, &rc, &err);
            QVERIFY2(rc == 0, qPrintable(QString(id) + ": " + err));
        }
    }
    void workbenchShipsAndIsValid() {
        int rc = 0;
        QString err;
        const QString out = runCli({"validate", QStringLiteral(TAOS_COMMON_DIR "/../themes/workbench")}, &rc, &err);
        QVERIFY2(rc == 0, qPrintable(err));
        QVERIFY(out.contains("dock mode taskbar, top bar no"));
    }
};

QTEST_MAIN(TestSkinLayout)
#include "test_skinlayout.moc"
