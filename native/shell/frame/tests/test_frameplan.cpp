// SPDX-License-Identifier: AGPL-3.0-or-later
// Pure tests: which windows are framed, where each strip goes (from fixture sway trees), when it hides, the border
// commands, the button hit regions and the title bar tuner.
#include <QDir>
#include <QFile>
#include <QTemporaryDir>
#include <QtTest>

#include "frameconfig.h"
#include "frameplan.h"

static FrameTree load(const char *name)
{
    QFile f(QStringLiteral(TAOS_FRAME_FIXTURES "/") + QLatin1String(name));
    if (!f.open(QIODevice::ReadOnly))
        qFatal("no fixture %s", name);
    FrameTree t;
    QString err;
    if (!parseFrameTree(f.readAll(), &t, &err))
        qFatal("fixture %s: %s", name, qPrintable(err));
    return t;
}

static const Strip *byId(const QVector<Strip> &v, int id)
{
    for (const Strip &s : v)
        if (s.id == id)
            return &s;
    return nullptr;
}

class TestFramePlan : public QObject {
    Q_OBJECT
private slots:
    // ---- rules
    void defaultRules()
    {
        FrameConfig c;
        QVERIFY(c.shouldFrame(QStringLiteral("chrome-www.youtube.com__-Default")));
        QVERIFY2(c.shouldFrame(QStringLiteral("foot")), "the terminal is framed: one rule for every window");
        QVERIFY(c.shouldFrame(QStringLiteral("org.kde.kate")));
        QVERIFY(c.shouldFrame(QString()));                    // a window without an app id still gets its buttons
        QVERIFY(!c.shouldFrame(QStringLiteral("my.taos.settings")));
        QVERIFY2(!c.shouldFrame(QStringLiteral("org.mozilla.firefox")), "a header bar app is on the self decorating list");
        QVERIFY(!c.shouldFrame(QStringLiteral("firefox")));
        QVERIFY2(c.shouldFrame(QStringLiteral("org.gnome.Calculator")), "a plain GTK 3 window takes server side decoration: framed");
        QVERIFY(!c.shouldFrame(QStringLiteral("chromium")));
    }
    void selfDecoratingIsData()
    {
        FrameConfig c;
        c.selfDecorating = {QStringLiteral("^foot$")};
        QVERIFY(!c.shouldFrame(QStringLiteral("foot")));
        QVERIFY2(c.shouldFrame(QStringLiteral("org.mozilla.firefox")), "the file replaces the shipped list");
        QTemporaryDir d;
        const QString p = d.path() + QStringLiteral("/frame.json");
        QFile f(p);
        QVERIFY(f.open(QIODevice::WriteOnly));
        f.write("{\"selfDecorating\":[\"^x$\"],\"dialogs\":[\"^pick\"],\"fixedSize\":[\"^calc\"]}");
        f.close();
        FrameConfig r;
        QString err;
        QVERIFY(FrameConfigFile::read(p, &r, &err));
        QCOMPARE(r.selfDecorating, QStringList{QStringLiteral("^x$")});
        QCOMPARE(r.dialogs, QStringList{QStringLiteral("^pick")});
        QVERIFY(r.kindOf(QStringLiteral("picker")) == FrameConfig::Kind::Dialog);
        QVERIFY(r.kindOf(QStringLiteral("calc")) == FrameConfig::Kind::FixedSize);
        QVERIFY(r.kindOf(QStringLiteral("foot")) == FrameConfig::Kind::Normal);
    }
    void includeExclude()
    {
        FrameConfig c;
        c.include = {QStringLiteral("^chrome-"), QStringLiteral("^foot$")};
        c.exclude = {QStringLiteral("^chrome-private")};
        QVERIFY(c.shouldFrame(QStringLiteral("foot")));
        QVERIFY(c.shouldFrame(QStringLiteral("chrome-a__-Default")));
        QVERIFY2(!c.shouldFrame(QStringLiteral("chrome-private.example__-Default")), "exclude beats include");
    }
    void frameAllForeign()
    {
        FrameConfig c;
        c.frameAllForeign = true;
        QVERIFY(c.shouldFrame(QStringLiteral("foot")));
        QVERIFY(c.shouldFrame(QStringLiteral("org.kde.dolphin")));
        QVERIFY2(!c.shouldFrame(QStringLiteral("my.taos.dashboard")), "a taOS app draws its own header");
        QVERIFY2(!c.shouldFrame(QStringLiteral("chromium")), "a normal browser window draws its own frame");
        QVERIFY(!c.shouldFrame(QStringLiteral("google-chrome-stable")));
    }
    void builtinExcludeBeatsInclude()
    {
        FrameConfig c;
        c.include = {QStringLiteral(".*")};
        QVERIFY(!c.shouldFrame(QStringLiteral("my.taos.x")));
    }
    void configFile()
    {
        QTemporaryDir d;
        const QString p = d.path() + QStringLiteral("/frame.json");
        FrameConfig c;
        QString err;
        QVERIFY(FrameConfigFile::read(p, &c, &err));          // missing file: defaults
        QVERIFY(c == FrameConfig());
        QFile f(p);
        QVERIFY(f.open(QIODevice::WriteOnly));
        f.write("{\"include\":[\"^foot$\",\"(\"],\"exclude\":[\"^x\"],\"frameAllForeign\":true}");
        f.close();
        QVERIFY(FrameConfigFile::read(p, &c, &err));
        QCOMPARE(c.include, QStringList{QStringLiteral("^foot$")});   // the broken pattern is dropped
        QVERIFY(err.contains(QLatin1String("invalid")));
        QCOMPARE(c.exclude, QStringList{QStringLiteral("^x")});
        QVERIFY(c.frameAllForeign);
        QVERIFY(f.open(QIODevice::WriteOnly | QIODevice::Truncate));
        f.write("not json");
        f.close();
        QVERIFY(!FrameConfigFile::read(p, &c, &err));
        QVERIFY(c == FrameConfig());                          // bad file: the defaults, and the reason
    }

    // ---- geometry from fixture trees
    void tiledGeometry()
    {
        const FrameTree t = load("tiled.json");
        PlanInput in;
        in.nowMs = 10000;
        QVector<Strip> s = planStrips(t, FrameConfig(), in);
        QCOMPARE(s.size(), 2);                                // the terminal is framed too (no bar yet: hidden)
        const Strip &a = *byId(s, 10);
        QCOMPARE(a.id, 10);
        QVERIFY(a.show);
        QCOMPARE(a.pos, QPoint(8, 4));                        // 8 px in, centred in the 32 px bar
        QCOMPARE(a.size, QSize(80, 24));
        QCOMPARE(a.title, QStringLiteral("YouTube"));
        in.buttonsRight = true;
        s = planStrips(t, FrameConfig(), in);
        QCOMPARE(byId(s, 10)->pos, QPoint(640 - 8 - 80, 4));  // the right corner of the 640 px bar
        QVERIFY(byId(s, 10)->right);
    }
    void noBarYetMeansNoStrip()
    {
        const FrameTree t = load("tiled.json");
        FrameConfig all;
        all.frameAllForeign = true;
        PlanInput in;
        in.nowMs = 10000;
        const QVector<Strip> s = planStrips(t, all, in);
        QCOMPARE(s.size(), 2);
        QCOMPARE(byId(s, 11)->reason, QStringLiteral("no-titlebar"));   // foot has border pixel: no bar to paint over
        QVERIFY(!byId(s, 11)->show);
    }
    void tiledTerminalWhenIncluded()
    {
        FrameTree t = load("tiled.json");
        for (FrameWindow &w : t.windows)
            if (w.id == 11) { w.border = QStringLiteral("normal"); w.deco = QRect(640, 0, 640, 32); w.rect = QRect(640, 32, 640, 688); }
        FrameConfig c;
        PlanInput in;
        in.nowMs = 10000;
        const QVector<Strip> s = planStrips(t, c, in);
        QCOMPARE(s.size(), 2);
        QCOMPARE(byId(s, 11)->pos, QPoint(640 + 8, 4));
        QVERIFY(byId(s, 11)->show);
    }
    void followsAMove()
    {
        FrameTree t = load("floating-overlap.json");
        PlanInput in;
        in.nowMs = 10000;
        const QPoint before = byId(planStrips(t, FrameConfig(), in), 21)->pos;
        QCOMPARE(before, QPoint(408, 68 + 4));
        for (FrameWindow &w : t.windows)
            if (w.id == 21) { w.deco.translate(300, 200); w.rect.translate(300, 200); }
        QCOMPARE(byId(planStrips(t, FrameConfig(), in), 21)->pos, before + QPoint(300, 200));
    }
    void movingHidesUntilSettled()
    {
        const FrameTree t = load("tiled.json");
        PlanInput in;
        in.nowMs = 10000;
        in.settleMs = 150;
        in.changedAt.insert(10, 9900);                         // 100 ms ago
        QCOMPARE(byId(planStrips(t, FrameConfig(), in), 10)->reason, QStringLiteral("moving"));
        QVERIFY(!byId(planStrips(t, FrameConfig(), in), 10)->show);
        in.nowMs = 10100;                                      // 200 ms ago
        QVERIFY(byId(planStrips(t, FrameConfig(), in), 10)->show);
    }
    void floatingOverlapHidesTheCoveredButtons()
    {
        const FrameTree t = load("floating-overlap.json");
        PlanInput in;
        in.nowMs = 10000;
        QVector<Strip> s = planStrips(t, FrameConfig(), in);
        QVERIFY(byId(s, 20)->show);                            // left buttons at x 108..188, Mail starts at 400
        QVERIFY(byId(s, 21)->show);
        in.buttonsRight = true;                                // right buttons of Music sit under Mail
        s = planStrips(t, FrameConfig(), in);
        QCOMPARE(byId(s, 20)->reason, QStringLiteral("occluded"));
        QVERIFY(byId(s, 21)->show);                            // the one on top is never covered
    }
    void hiddenStates()
    {
        const FrameTree t = load("special.json");
        PlanInput in;
        in.nowMs = 10000;
        const QVector<Strip> s = planStrips(t, FrameConfig(), in);
        QCOMPARE(byId(s, 30)->reason, QStringLiteral("fullscreen"));
        QCOMPARE(byId(s, 31)->reason, QStringLiteral("hidden"));      // on another workspace
        QCOMPARE(byId(s, 32)->reason, QStringLiteral("no-titlebar")); // sway has not given it a bar yet
        QCOMPARE(byId(s, 40)->reason, QStringLiteral("minimised"));
        QVERIFY(byId(s, 40)->output.isEmpty());
        QVERIFY(!byId(s, 51));                                        // taOS app
        QVERIFY(!byId(s, 52));                                        // normal browser window
    }
    void secondOutput()
    {
        const FrameTree t = load("special.json");
        PlanInput in;
        in.nowMs = 10000;
        const Strip *s = byId(planStrips(t, FrameConfig(), in), 50);
        QVERIFY(s && s->show);
        QCOMPARE(s->output, QStringLiteral("HEADLESS-2"));
        QCOMPARE(s->pos, QPoint(1280 + 8, 4));                        // layout coordinates
        QCOMPARE(s->margin, QPoint(8, 4));                            // layer-shell margins are from the output
    }
    // Captured from a real sway 1.10 whose workspace starts 33 px down (a notification bar reserved the top): deco_rect
    // is workspace relative there (tiled y 0, floating 267) while rect is absolute. The strip must sit on the bar that
    // is drawn, directly above the content.
    void realSwayWithReservedTop()
    {
        const FrameTree t = load("real-sway-reserved-top.json");
        PlanInput in;
        in.nowMs = 10000;
        const QVector<Strip> s = planStrips(t, FrameConfig(), in);
        QCOMPARE(s.size(), 2);
        QCOMPARE(byId(s, 6)->pos, QPoint(8, 33 + 3));              // tiled: content at 64, bar 31 high above it at 33
        QCOMPARE(byId(s, 5)->pos, QPoint(200 + 8, 300 + 3));       // floating: content at 331
        QVERIFY(byId(s, 6)->show && byId(s, 5)->show);
    }
    void tabbedAndStackedAreLeftToSway()
    {
        const FrameTree t = load("tabbed.json");
        PlanInput in;
        in.nowMs = 10000;
        const QVector<Strip> s = planStrips(t, FrameConfig(), in);
        QVERIFY(!s.isEmpty());
        for (const Strip &x : s)
            QCOMPARE(x.reason, QStringLiteral("tabbed"));
    }
    void csdWindowsAreNeverGivenABar()
    {
        FrameTree t = load("special.json");
        FrameConfig all;
        all.frameAllForeign = true;
        QSet<int> framed;
        const QStringList c = borderCommands(t, all, &framed);
        QVERIFY(!c.join(QLatin1Char(';')).contains(QLatin1String("con_id=52")));    // chromium, border csd
        QVERIFY(!framed.contains(52));
    }
    void closedWindowHasNoStrip()
    {
        FrameTree t = load("tiled.json");
        PlanInput in;
        in.nowMs = 10000;
        QVERIFY(byId(planStrips(t, FrameConfig(), in), 10));
        t.windows.removeIf([](const FrameWindow &w) { return w.id == 10; });
        QVERIFY(!byId(planStrips(t, FrameConfig(), in), 10));
    }

    // ---- every window: the rule, the kinds and the styles
    void everyNormalWindowIsFramedWhateverTheToolkit()
    {
        FrameTree t = load("kinds.json");
        for (FrameWindow &w : t.windows)
            if (w.id == 68) w.selfDrawn = true;                    // the controller sets it from /proc/<pid>/maps
        PlanInput in;
        in.nowMs = 10000;
        const QVector<Strip> s = planStrips(t, FrameConfig(), in);
        QVERIFY2(byId(s, 60) && byId(s, 60)->show, qPrintable(QStringLiteral("the terminal is framed and shows its buttons: %1").arg(byId(s, 60) ? byId(s, 60)->reason : QStringLiteral("no strip"))));
        QVERIFY(byId(s, 61) && byId(s, 61)->show);
        QCOMPARE(byId(s, 60)->pos, QPoint(20 + 8, 40 + 4));
        QVERIFY2(!byId(s, 62), "a header bar app (self decorating list) gets no second set");
        QVERIFY2(!byId(s, 68), "GTK 4 / libadwaita draws its own header: found from the process, not the name");
        QVERIFY2(!byId(s, 63), "a window that set its own decoration mode (border csd) gets none");
        QVERIFY2(!byId(s, 66), "a taOS app draws its own header");
        QVERIFY2(byId(s, 64) && byId(s, 64)->show, "an X11 dialog without an app id is still framed");
    }
    void focusedAndUnfocusedStyle()
    {
        FrameTree t = load("kinds.json");
        PlanInput in;
        in.nowMs = 10000;
        const QVector<Strip> s = planStrips(t, FrameConfig(), in);
        QVERIFY(byId(s, 60)->focused);
        QVERIFY2(!byId(s, 61)->focused, "an unfocused window's strip is told so (dim style)");
        FrameTree moved = t;
        for (FrameWindow &w : moved.windows) w.focused = w.id == 61;
        const QVector<Strip> s2 = planStrips(moved, FrameConfig(), in);
        QVERIFY(!byId(s2, 60)->focused && byId(s2, 61)->focused);
    }
    void fullscreenHasNoBar()
    {
        const FrameTree t = load("kinds.json");
        PlanInput in;
        in.nowMs = 10000;
        const Strip *s = byId(planStrips(t, FrameConfig(), in), 67);
        QVERIFY(s);
        QVERIFY(!s->show);
        QCOMPARE(s->reason, QStringLiteral("fullscreen"));
    }
    void dialogsTakeCloseOnlyAndFixedSizeTakesNoFullSize()
    {
        const FrameTree t = load("kinds.json");
        PlanInput in;
        in.nowMs = 10000;
        FrameConfig c;
        c.fixedSize = {QStringLiteral("^calculator$")};
        const QVector<Strip> s = planStrips(t, c, in);
        QVERIFY(byId(s, 60)->canMinimise && byId(s, 60)->canFull);                 // an ordinary window: all three
        QVERIFY2(!byId(s, 64)->canMinimise && !byId(s, 64)->canFull, "an X11 dialog (transient, type dialog): close only");
        QVERIFY2(byId(s, 65)->canMinimise && !byId(s, 65)->canFull, "a fixed size window: no full size");
        FrameConfig d;
        d.dialogs = {QStringLiteral("^calculator$")};
        QVERIFY(!byId(planStrips(t, d, in), 65)->canMinimise);
    }
    void toolkitsThatAlwaysDrawTheirOwnHeader()
    {
        QVERIFY(mapsShowSelfDrawingToolkit("7f00 r-xp 0 08:01 1 /usr/lib/x86_64-linux-gnu/libadwaita-1.so.0.5.0\n"));
        QVERIFY(mapsShowSelfDrawingToolkit("7f00 r-xp 0 08:01 1 /usr/lib/x86_64-linux-gnu/libgtk-4.so.1.1400.0\n"));
        QVERIFY2(!mapsShowSelfDrawingToolkit("7f00 r-xp 0 08:01 1 /usr/lib/x86_64-linux-gnu/libgtk-3.so.0.2406.0\n"), "GTK 3 may take server side decoration");
        QVERIFY(!mapsShowSelfDrawingToolkit("7f00 r-xp 0 08:01 1 /opt/taos/qt/lib/libQt6Gui.so.6\n"));
    }
    void tooltipsNameTheKeyboardWay()
    {
        QVERIFY(StripLayout::tipFor(QStringLiteral("close"), true).contains(QLatin1String("Super+Q")));
        QVERIFY(StripLayout::tipFor(QStringLiteral("minimise"), true).contains(QLatin1String("Super+M")));
        QVERIFY(StripLayout::tipFor(QStringLiteral("full"), true).contains(QLatin1String("Super+Ctrl+Up")));
        QVERIFY(StripLayout::tipFor(QStringLiteral("full"), false).isEmpty());
    }

    // ---- sway border commands
    void terminalGetsABarFromSway()
    {
        const FrameTree t = load("tiled.json");
        QSet<int> framed;
        const QStringList c = borderCommands(t, FrameConfig(), &framed);
        QVERIFY2(c.contains(QStringLiteral("[con_id=11] border normal 1")), qPrintable(c.join(QLatin1Char(';'))));
        QVERIFY(framed.contains(11));
    }
    // GTK 3 header bar windows tell sway they draw their own header a moment AFTER they map (they show border none, then
    // csd). A bar forced on them first stays (measured), so a new window is only judged once it is a little older.
    void aNewWindowIsNotJudgedUntilItIsReady()
    {
        const FrameTree t = load("tiled.json");
        QSet<int> framed, ready;
        QVERIFY(borderCommands(t, FrameConfig(), &framed, &ready).isEmpty());
        QVERIFY(!framed.contains(11));
        ready.insert(11);
        QVERIFY(borderCommands(t, FrameConfig(), &framed, &ready).contains(QStringLiteral("[con_id=11] border normal 1")));
    }
    void borderCommandsFrameAndRelease()
    {
        const FrameTree t = load("special.json");
        QSet<int> framed;
        QStringList c = borderCommands(t, FrameConfig(), &framed);
        QVERIFY(c.contains(QStringLiteral("[con_id=32] border normal 1")));
        QVERIFY(!c.join(QLatin1Char(';')).contains(QLatin1String("con_id=51")));   // never a taOS app
        QVERIFY(!c.join(QLatin1Char(';')).contains(QLatin1String("con_id=52")));
        QVERIFY(framed.contains(32) && framed.contains(50) && framed.contains(40));
        // The rule changes: the web apps are no longer framed; the ones that had a bar get theirs back as a pixel.
        FrameConfig none;
        none.include.clear();
        c = borderCommands(t, none, &framed);
        QVERIFY(c.contains(QStringLiteral("[con_id=50] border pixel 1")));
        QVERIFY(!framed.contains(50));
    }

    // ---- buttons
    void buttonsLeftAndRight()
    {
        QStringList l, r;
        for (const auto &b : StripLayout::buttons(false)) l << b.name;
        for (const auto &b : StripLayout::buttons(true)) r << b.name;
        QCOMPARE(l, (QStringList{QStringLiteral("close"), QStringLiteral("minimise"), QStringLiteral("full")}));
        QCOMPARE(r, (QStringList{QStringLiteral("minimise"), QStringLiteral("full"), QStringLiteral("close")}));
    }
    void hitRegions()
    {
        QCOMPARE(StripLayout::buttonAt(false, QPoint(12, 12)), QStringLiteral("close"));
        QCOMPARE(StripLayout::buttonAt(false, QPoint(0, 0)), QStringLiteral("close"));
        QCOMPARE(StripLayout::buttonAt(false, QPoint(23, 23)), QStringLiteral("close"));
        QCOMPARE(StripLayout::buttonAt(false, QPoint(25, 12)), QString());            // the gap
        QCOMPARE(StripLayout::buttonAt(false, QPoint(40, 12)), QStringLiteral("minimise"));
        QCOMPARE(StripLayout::buttonAt(false, QPoint(68, 12)), QStringLiteral("full"));
        QCOMPARE(StripLayout::buttonAt(false, QPoint(80, 12)), QString());            // outside
        QCOMPARE(StripLayout::buttonAt(false, QPoint(12, 24)), QString());
        QCOMPARE(StripLayout::buttonAt(true, QPoint(12, 12)), QStringLiteral("minimise"));
        QCOMPARE(StripLayout::buttonAt(true, QPoint(68, 12)), QStringLiteral("close"));
        for (bool right : {false, true})
            for (const auto &b : StripLayout::buttons(right))
                QVERIFY(b.rect.width() >= 24 && b.rect.height() >= 24);
    }
    void accessibleNames()
    {
        QCOMPARE(StripLayout::labelFor(QStringLiteral("close"), QStringLiteral("YouTube")), QStringLiteral("Close YouTube"));
        QCOMPARE(StripLayout::labelFor(QStringLiteral("minimise"), QStringLiteral("YouTube")), QStringLiteral("Minimise YouTube"));
        QCOMPARE(StripLayout::labelFor(QStringLiteral("full"), QStringLiteral("YouTube")), QStringLiteral("Full size YouTube"));
        QCOMPARE(StripLayout::labelFor(QStringLiteral("close"), QString()), QStringLiteral("Close window"));
    }

    // ---- title bar tuner
    void tunerConverges()
    {
        // sway: bar = font height + 2 * padding. The stand-in font height grows with the pixel size like pango's.
        auto bar = [](int fontSize, int vPad) { return int(fontSize * 1.3) + 2 * vPad; };
        for (int px = 9; px <= 20; ++px) {
            int fontSize = px, vPad = 4, steps = 0;
            for (; steps < 8; ++steps) {
                const TuneStep s = tuneTitleBar(bar(fontSize, vPad), fontSize, vPad);
                if (s.done)
                    break;
                fontSize = s.fontSize;
                vPad = s.vPad;
            }
            QVERIFY2(steps < 8, qPrintable(QStringLiteral("start %1px did not settle").arg(px)));
            QCOMPARE(bar(fontSize, vPad), 32);
        }
        QVERIFY(tuneTitleBar(32, 13, 7).done);
        QCOMPARE(tuneTitleBar(30, 13, 5).vPad, 6);            // font height 20 -> padding 6 gives 32
    }
};

QTEST_GUILESS_MAIN(TestFramePlan)
#include "test_frameplan.moc"
