// SPDX-License-Identifier: AGPL-3.0-or-later
// Logic of the window engine against fixture trees (real sway get_tree output) and a fake transport.
#include <QFile>
#include <QTemporaryDir>
#include <QJsonArray>
#include <QJsonDocument>
#include <QJsonObject>
#include <QtTest>

#include "core/desktopmodes.h"
#include "core/swayconn.h"
#include "core/windowevents.h"
#include "core/windowmodel.h"
#include "core/windowops.h"

using namespace TaosWin;

static QByteArray fixture(const char *name) {
    QFile f(QStringLiteral(TAOS_WINDOW_FIXTURES "/") + QLatin1String(name));
    return f.open(QIODevice::ReadOnly) ? f.readAll() : QByteArray();
}

namespace {
struct FakeTransport : Transport {
    QByteArray tree;
    QStringList commands;
    QString failOn;
    bool getTree(QByteArray *j, QString *) override { *j = tree; return true; }
    bool runCommand(const QString &c, QString *e) override {
        commands << c;
        if (!failOn.isEmpty() && c.contains(failOn)) { if (e) *e = QStringLiteral("boom"); return false; }
        return true;
    }
};
}  // namespace


namespace {
// A tree with one output (1920x1080), workspaces "1" (focused, usable area 0,23 1920x1057: a bar on top) and "2", and the
// windows given. A window is {id, workspace, floating, container rect, marks, fullscreen}.
struct TW {
    int id; QString ws; bool floating; QRect rect; QStringList marks = {}; bool fullscreen = false; bool scratch = false;
};
static QByteArray treeJson(const QVector<TW> &wins, const QString &focusedWs = QStringLiteral("1")) {
    auto rj = [](const QRect &r) { return QJsonObject{{"x", r.x()}, {"y", r.y()}, {"width", r.width()}, {"height", r.height()}}; };
    auto node = [&](const TW &w) {
        QJsonObject o{{"id", w.id}, {"type", w.floating || w.scratch ? "floating_con" : "con"}, {"app_id", QStringLiteral("app.%1").arg(w.id)},
                      {"name", QStringLiteral("W%1").arg(w.id)}, {"pid", 100 + w.id}, {"focused", false},
                      {"floating", w.floating || w.scratch ? "user_on" : "auto_off"}, {"fullscreen_mode", w.fullscreen ? 1 : 0},
                      {"border", "pixel"}, {"rect", rj(w.rect)}, {"deco_rect", rj(QRect())},
                      {"nodes", QJsonArray()}, {"floating_nodes", QJsonArray()}, {"shell", "xdg_shell"}};
        QJsonArray m;
        for (const QString &x : w.marks) m << x;
        o["marks"] = m;
        return o;
    };
    auto ws = [&](const QString &name, const QRect &area) {
        QJsonArray tiled, floating;
        for (const TW &w : wins)
            if (!w.scratch && w.ws == name) (w.floating ? floating : tiled) << node(w);
        return QJsonObject{{"id", name.toInt() + 1000}, {"type", "workspace"}, {"name", name}, {"rect", rj(area)},
                           {"focused", name == focusedWs}, {"visible", name == focusedWs}, {"nodes", tiled}, {"floating_nodes", floating}};
    };
    QJsonArray scratchWins;
    for (const TW &w : wins) if (w.scratch) scratchWins << node(w);
    QJsonObject scratchWs{{"type", "workspace"}, {"name", "__i3_scratch"}, {"rect", rj(QRect())}, {"nodes", QJsonArray()}, {"floating_nodes", scratchWins}};
    QJsonObject scratchOut{{"type", "output"}, {"name", "__i3"}, {"rect", rj(QRect())}, {"nodes", QJsonArray{scratchWs}}};
    QJsonObject out{{"type", "output"}, {"name", "O1"}, {"rect", rj(QRect(0, 0, 1920, 1080))},
                    {"nodes", QJsonArray{ws(QStringLiteral("1"), QRect(0, 23, 1920, 1057)), ws(QStringLiteral("2"), QRect(0, 23, 1920, 1057))}}};
    return QJsonDocument(QJsonObject{{"type", "root"}, {"nodes", QJsonArray{scratchOut, out}}}).toJson();
}
}  // namespace

class TestWindowCore : public QObject {
    Q_OBJECT
private slots:
    // A window with a sway title bar: rect is the container (bar included), the unit `resize set` and `move position` use.
    void rectIncludesTheTitleBar() {
        const QByteArray json = "{\"type\":\"root\",\"nodes\":[{\"type\":\"output\",\"name\":\"O\",\"rect\":{\"x\":0,\"y\":0,\"width\":800,\"height\":600},"
            "\"nodes\":[{\"type\":\"workspace\",\"name\":\"1\",\"rect\":{\"x\":0,\"y\":0,\"width\":800,\"height\":600},\"nodes\":[],\"floating_nodes\":["
            "{\"id\":9,\"type\":\"floating_con\",\"floating\":\"user_on\",\"shell\":\"xdg_shell\",\"app_id\":\"chrome-a__-D\",\"name\":\"A\",\"pid\":1,\"border\":\"normal\","
            "\"rect\":{\"x\":100,\"y\":132,\"width\":500,\"height\":268},\"deco_rect\":{\"x\":0,\"y\":100,\"width\":500,\"height\":32},\"marks\":[],\"nodes\":[],\"floating_nodes\":[]},"
            "{\"id\":10,\"type\":\"floating_con\",\"floating\":\"user_on\",\"shell\":\"xdg_shell\",\"app_id\":\"foot\",\"name\":\"B\",\"pid\":2,\"border\":\"pixel\","
            "\"rect\":{\"x\":50,\"y\":50,\"width\":300,\"height\":200},\"deco_rect\":{\"x\":0,\"y\":0,\"width\":0,\"height\":0},\"marks\":[],\"nodes\":[],\"floating_nodes\":[]}]}]}]}";
        Tree t;
        QString err;
        QVERIFY2(parseTree(json, &t, &err), qPrintable(err));
        QCOMPARE(t.find(9)->rect, QRect(100, 100, 500, 300));      // bar included
        QCOMPARE(t.find(10)->rect, QRect(50, 50, 300, 200));       // no bar: unchanged
    }

    void marksRoundTrip() {
        for (const QString &n : {QStringLiteral("1"), QStringLiteral("2: web"), QStringLiteral("a_b\"c;d,e"), QStringLiteral("café ☃"), QStringLiteral("50%")}) {
            const QString e = Marks::encodeName(n);
            QVERIFY2(!e.contains(QLatin1Char(' ')) && !e.contains(QLatin1Char('_')) && !e.contains(QLatin1Char('"')), qPrintable(e));
            QCOMPARE(Marks::decodeName(e), n);
        }
        QRect r;
        QVERIFY(Marks::parseGeometry(Marks::geometry(QStringLiteral("taos_geo_"), QRect(-5, 7, 300, 200)), QStringLiteral("taos_geo_"), &r));
        QCOMPARE(r, QRect(-5, 7, 300, 200));
        QVERIFY(!Marks::parseGeometry(QStringLiteral("taos_geo_1_2_3"), QStringLiteral("taos_geo_"), &r));
        QVERIFY(!Marks::parseGeometry(QStringLiteral("taos_geo_a_b_c_d"), QStringLiteral("taos_geo_"), &r));
    }

    void parsePlain() {
        Tree t;
        QString err;
        QVERIFY2(parseTree(fixture("tree-plain.json"), &t, &err), qPrintable(err));
        QCOMPARE(t.windows.size(), 2);
        const Window *a = t.find(5), *b = t.find(6);
        QVERIFY(a && b);
        QVERIFY(!a->minimised && !b->minimised);
        QVERIFY(b->floating && !a->floating);
        QCOMPARE(a->workspace, QStringLiteral("1"));
        QCOMPARE(a->output, QStringLiteral("HEADLESS-1"));
        QVERIFY(a->pid > 0);
        QCOMPARE(a->appId, QStringLiteral("app.one"));
        QVERIFY(!t.workspaces.isEmpty());
        QCOMPARE(t.workspace(QStringLiteral("1"))->rect, QRect(0, 0, 1280, 720));
        QCOMPARE(t.focusedWorkspace, QStringLiteral("1"));
        QVERIFY(!t.lastMinimised());
    }

    void parseMinimised() {
        Tree t;
        QVERIFY(parseTree(fixture("tree-min.json"), &t));
        QCOMPARE(t.windows.size(), 2);
        const Window *b = t.find(6);
        QVERIFY(b);
        QVERIFY(b->minimised && b->taosMinimised && b->inScratchpad);
        QCOMPARE(b->workspace, QStringLiteral("1"));
        QCOMPARE(b->output, QStringLiteral("HEADLESS-1"));
        QVERIFY(b->floating);
        QCOMPARE(b->savedGeometry, QRect(200, 150, 400, 300));
        QCOMPARE(b->minimisedAt, qint64(1700000000123));
        QCOMPARE(t.lastMinimised(), b);
        QVERIFY(!t.find(5)->minimised);
        // The JSON contract, exactly these keys.
        const QJsonObject o = b->toJson();
        QStringList keys = o.keys();
        keys.sort();
        QCOMPARE(keys, QStringList({"app_id", "floating", "focused", "full_size", "fullscreen", "id", "minimised", "output", "pid", "title", "workspace"}));

        QCOMPARE(o.value(QStringLiteral("minimised")).toBool(), true);
        QCOMPARE(o.value(QStringLiteral("id")).toInt(), 6);
    }

    void staleMarkIsNotMinimised() {
        // taos_min_6 on a window that is on a workspace again (someone ran scratchpad show by hand).
        QByteArray j = fixture("tree-plain.json");
        QVERIFY(!j.isEmpty());
        QJsonDocument d = QJsonDocument::fromJson(j);
        QVERIFY(d.isObject());
        Tree t;
        QVERIFY(parseTree(fixture("tree-plain.json"), &t));
        QVERIFY(!t.find(6)->minimised);
        QVERIFY(!t.lastMinimised());
    }

    void minimisePlans() {
        Tree t;
        QVERIFY(parseTree(fixture("tree-plain.json"), &t));
        const Plan tiled = Plans::minimise(*t.find(5), 1234);
        QVERIFY(tiled.ok());
        QVERIFY(tiled.commands.contains(QStringLiteral("[con_id=5] mark --add \"taos_min_5\"")));
        QVERIFY(tiled.commands.contains(QStringLiteral("[con_id=5] mark --add \"taos_at_1234\"")));
        QVERIFY(tiled.commands.contains(QStringLiteral("[con_id=5] mark --add \"taos_ws_1\"")));
        QVERIFY(!tiled.commands.join(QLatin1Char('\n')).contains(QLatin1String("taos_mfl")));
        QCOMPARE(tiled.commands.last(), QStringLiteral("[con_id=5] move scratchpad"));
        const Plan fl = Plans::minimise(*t.find(6), 99);
        const QString all = fl.commands.join(QLatin1Char('\n'));
        QVERIFY(all.contains(QLatin1String("taos_mfl")));
        QVERIFY(all.contains(QLatin1String("taos_geo_200_150_400_300")));
        // Marks come before the move: if the move fails nothing is half done in the other direction.
        QVERIFY(fl.commands.indexOf(QStringLiteral("[con_id=6] move scratchpad")) == fl.commands.size() - 1);
        // Already minimised: no commands.
        Tree m;
        QVERIFY(parseTree(fixture("tree-min.json"), &m));
        QVERIFY(Plans::minimise(*m.find(6), 1).commands.isEmpty());
    }

    void minimiseFullscreenAndWorkspaceQuoting() {
        Window w;
        w.id = 9; w.workspace = QStringLiteral("2: \"web\""); w.fullscreen = true; w.floating = false;
        const Plan p = Plans::minimise(w, 5);
        const QString all = p.commands.join(QLatin1Char('\n'));
        QVERIFY(all.contains(QLatin1String("fullscreen disable")));
        QVERIFY(all.contains(QLatin1String("taos_mfs")));
        QVERIFY(all.contains(QLatin1String("taos_ws_2%3a%20%22web%22")));
        QCOMPARE(Plans::quoted(QStringLiteral("a\"b\\c")), QStringLiteral("\"a\\\"b\\\\c\""));
    }

    void restorePlans() {
        Tree t;
        QVERIFY(parseTree(fixture("tree-min.json"), &t));
        // Focused workspace is "1" and the window came from "1": no workspace switch.
        const Plan p = Plans::restore(*t.find(6), t);
        QVERIFY(p.ok());
        QCOMPARE(p.commands.first(), QStringLiteral("[con_id=6] scratchpad show"));
        QVERIFY(!p.commands.join(QLatin1Char('\n')).contains(QLatin1String("floating disable")));  // it was floating
        QCOMPARE(p.commands.last(), QStringLiteral("[con_id=6] focus"));
        QVERIFY(p.commands.join(QLatin1Char('\n')).contains(QLatin1String("unmark \"taos_min_6\"")));
        // From another workspace: go there first. A window that was tiled becomes tiled again.
        Tree t2 = t;
        t2.focusedWorkspace = QStringLiteral("3");
        Window w = *t2.find(6);
        w.minWasFloating = false;
        const Plan q = Plans::restore(w, t2);
        QCOMPARE(q.commands.first(), QStringLiteral("workspace \"1\""));
        QVERIFY(q.commands.contains(QStringLiteral("[con_id=6] floating disable")));
        // A window that is not minimised: nothing to do.
        Tree plain;
        QVERIFY(parseTree(fixture("tree-plain.json"), &plain));
        QVERIFY(Plans::restore(*plain.find(5), plain).commands.isEmpty());
    }

    void fullPlans() {
        Tree t;
        QVERIFY(parseTree(fixture("tree-plain.json"), &t));
        const Plan tiled = Plans::full(*t.find(5), t);
        QVERIFY(tiled.ok());
        QVERIFY(tiled.commands.contains(QStringLiteral("[con_id=5] mark --add \"taos_ftiled\"")));
        QVERIFY(tiled.commands.contains(QStringLiteral("[con_id=5] floating enable")));
        QVERIFY(tiled.commands.contains(QStringLiteral("[con_id=5] resize set 1280 720")));
        QVERIFY(tiled.commands.contains(QStringLiteral("[con_id=5] move absolute position 0 0")));
        const Plan fl = Plans::full(*t.find(6), t);
        QVERIFY(fl.commands.contains(QStringLiteral("[con_id=6] mark --add \"taos_fgeo_200_150_400_300\"")));
        QVERIFY(!fl.commands.contains(QStringLiteral("[con_id=6] floating enable")));
        // Reverting.
        Window f = *t.find(6);
        f.fullSize = true;
        f.fullSavedGeometry = QRect(200, 150, 400, 300);
        f.marks << QStringLiteral("taos_full") << QStringLiteral("taos_fgeo_200_150_400_300");
        const Plan back = Plans::full(f, t);
        QVERIFY(back.commands.contains(QStringLiteral("[con_id=6] resize set 400 300")));
        QVERIFY(back.commands.contains(QStringLiteral("[con_id=6] move absolute position 200 150")));
        QVERIFY(back.commands.contains(QStringLiteral("[con_id=6] unmark \"taos_full\"")));
        Window g = *t.find(5);
        g.fullSize = true; g.fullWasTiled = true; g.floating = true;
        g.marks << QStringLiteral("taos_full") << QStringLiteral("taos_ftiled");
        const Plan back2 = Plans::full(g, t);
        QVERIFY(back2.commands.contains(QStringLiteral("[con_id=5] floating disable")));
        QVERIFY(back2.commands.contains(QStringLiteral("[con_id=5] unmark \"taos_ftiled\"")));
        // Minimised windows are not resized.
        Tree m;
        QVERIFY(parseTree(fixture("tree-min.json"), &m));
        QVERIFY(!Plans::full(*m.find(6), m).ok());
        // Real fullscreen first goes back.
        Window fs = *t.find(5);
        fs.fullscreen = true;
        QCOMPARE(Plans::full(fs, t).commands, QStringList{QStringLiteral("[con_id=5] fullscreen disable")});
    }


    // ---- window controls (dock menu, taskbar menu, agents) ---------------------------------------------------------
    void jsonReportsFullSizeAndSpaces() {
        Tree t;
        QVERIFY(parseTree(treeJson({{5, "1", true, QRect(0, 23, 1920, 1057), {"taos_full", "taos_fgeo_10_10_400_300"}}, {6, "2", false, QRect(0, 23, 960, 1057)}}), &t));
        QVERIFY(t.find(5)->toJson().value("full_size").toBool());
        QVERIFY(!t.find(6)->toJson().value("full_size").toBool());
        QCOMPARE(t.workspaces.size(), 2);
        QCOMPARE(t.workspace("1")->count, 1);
        QCOMPARE(t.workspace("2")->toJson().value("windows").toInt(), 1);
        QVERIFY(t.workspace("1")->toJson().value("focused").toBool());
    }

    void stateEventsWhenMaximisedFullscreenOrMoved() {
        Tree a, b;
        QVERIFY(parseTree(treeJson({{5, "1", true, QRect(100, 100, 400, 300)}}), &a));
        auto kinds = [](const QList<WindowEvent> &l) { QStringList k; for (const auto &e : l) k << e.kind + QString::number(e.window.id); return k; };
        QVERIFY(parseTree(treeJson({{5, "1", true, QRect(0, 23, 1920, 1057), {"taos_full"}}}), &b));
        QCOMPARE(kinds(diffWindows(a, b)), QStringList{"state5"});
        QVERIFY(parseTree(treeJson({{5, "1", true, QRect(0, 0, 1920, 1080), {}, true}}), &b));
        QCOMPARE(kinds(diffWindows(a, b)), QStringList{"state5"});
        QVERIFY(parseTree(treeJson({{5, "2", true, QRect(100, 100, 400, 300)}}), &b));
        QCOMPARE(kinds(diffWindows(a, b)), QStringList{"state5"});
    }

    void maximiseIsIdempotentAndNeverToggles() {
        Tree t;
        QVERIFY(parseTree(treeJson({{5, "1", true, QRect(100, 100, 400, 300)}, {6, "1", false, QRect(0, 23, 960, 1057)}}), &t));
        const Plan fl = Plans::maximise(*t.find(5), t);
        QVERIFY(fl.ok());
        QVERIFY(fl.commands.contains(QStringLiteral("[con_id=5] mark --add \"taos_fgeo_100_100_400_300\"")));
        QVERIFY(fl.commands.contains(QStringLiteral("[con_id=5] resize set 1920 1057")));
        QVERIFY(fl.commands.contains(QStringLiteral("[con_id=5] move absolute position 0 23")));
        for (const QString &c : fl.commands) QVERIFY2(!c.contains(QLatin1String("fullscreen")), qPrintable(c));
        // Already maximised: asked again it keeps the saved geometry and only re-fits the area; never goes back.
        Tree m;
        QVERIFY(parseTree(treeJson({{5, "1", true, QRect(0, 23, 1920, 1057), {"taos_full", "taos_fgeo_100_100_400_300"}}}), &m));
        const Plan again = Plans::maximise(*m.find(5), m);
        QVERIFY(again.ok());
        QVERIFY(again.commands.contains(QStringLiteral("[con_id=5] resize set 1920 1057")));
        for (const QString &c : again.commands) QVERIFY2(!c.contains(QLatin1String("unmark")) && !c.contains(QLatin1String("floating disable")), qPrintable(c));
        // Full screen first leaves full screen (sway restores the geometry it had), then maximises.
        Tree f;
        QVERIFY(parseTree(treeJson({{5, "1", true, QRect(0, 0, 1920, 1080), {}, true}}), &f));
        const Plan fromFs = Plans::maximise(*f.find(5), f);
        // (what to go back to is only known once sway has left full screen: the controller plans again)
        QCOMPARE(fromFs.commands, QStringList{QStringLiteral("[con_id=5] fullscreen disable")});
        Tree fm;
        QVERIFY(parseTree(treeJson({{5, "1", true, QRect(0, 0, 1920, 1080), {"taos_full", "taos_fgeo_100_100_400_300"}, true}}), &fm));
        const Plan fsMax = Plans::maximise(*fm.find(5), fm);
        QCOMPARE(fsMax.commands.value(0), QStringLiteral("[con_id=5] fullscreen disable"));
        QVERIFY(fsMax.commands.contains(QStringLiteral("[con_id=5] resize set 1920 1057")));
        QVERIFY(!fsMax.commands.contains(QStringLiteral("[con_id=5] unmark \"taos_full\"")));
        // Minimised: refused with a sentence.
        Tree n;
        QVERIFY(parseTree(treeJson({{5, "1", true, QRect(0, 23, 400, 300), {"taos_min_5", "taos_ws_1"}, false, true}}), &n));
        QVERIFY(!Plans::maximise(*n.find(5), n).ok());
    }

    void restorePeelsFullscreenThenFullSizeThenNothing() {
        Tree t;
        // fullscreen on top of full size: back to the geometry before the maximise, fullscreen off first
        QVERIFY(parseTree(treeJson({{5, "1", true, QRect(0, 0, 1920, 1080), {"taos_full", "taos_fgeo_100_100_400_300"}, true}}), &t));
        const Plan both = Plans::unmaximise(*t.find(5), t);
        QCOMPARE(both.commands.value(0), QStringLiteral("[con_id=5] fullscreen disable"));
        QVERIFY(both.commands.contains(QStringLiteral("[con_id=5] resize set 400 300")));
        QVERIFY(both.commands.contains(QStringLiteral("[con_id=5] move absolute position 100 100")));
        QVERIFY(both.commands.contains(QStringLiteral("[con_id=5] unmark \"taos_full\"")));
        // only fullscreen: just leave it
        QVERIFY(parseTree(treeJson({{5, "1", true, QRect(0, 0, 1920, 1080), {}, true}}), &t));
        QCOMPARE(Plans::unmaximise(*t.find(5), t).commands, QStringList{QStringLiteral("[con_id=5] fullscreen disable")});
        // maximised from tiled: tiled again
        QVERIFY(parseTree(treeJson({{5, "1", true, QRect(0, 23, 1920, 1057), {"taos_full", "taos_ftiled"}}}), &t));
        QVERIFY(Plans::unmaximise(*t.find(5), t).commands.contains(QStringLiteral("[con_id=5] floating disable")));
        // nothing to undo
        QVERIFY(parseTree(treeJson({{5, "1", true, QRect(100, 100, 400, 300)}}), &t));
        QVERIFY(Plans::unmaximise(*t.find(5), t).commands.isEmpty());
    }

    void restoreOfAMinimisedWindowStillRestoresIt() {
        FakeTransport tx;
        tx.tree = treeJson({{5, "1", true, QRect(100, 100, 400, 300), {"taos_min_5", "taos_ws_1", "taos_mfl", "taos_at_7"}, false, true}});
        WindowController c(&tx);
        QString err;
        QVERIFY2(c.restore(QStringLiteral("5"), &err), qPrintable(err));
        QVERIFY(tx.commands.contains(QStringLiteral("[con_id=5] scratchpad show")));
        // and a window that is on screen and has nothing to undo: success, no command at all
        tx.commands.clear();
        tx.tree = treeJson({{5, "1", true, QRect(100, 100, 400, 300)}});
        QVERIFY2(c.restore(QStringLiteral("5"), &err), qPrintable(err));
        QVERIFY(tx.commands.isEmpty());
    }

    void fullscreenModes() {
        Tree t;
        QVERIFY(parseTree(treeJson({{5, "1", true, QRect(100, 100, 400, 300)}, {6, "1", true, QRect(0, 0, 1920, 1080), {}, true}}), &t));
        QCOMPARE(Plans::fullscreen(*t.find(5), Plans::Fullscreen::On).commands, QStringList{QStringLiteral("[con_id=5] fullscreen enable")});
        QVERIFY(Plans::fullscreen(*t.find(6), Plans::Fullscreen::On).commands.isEmpty());      // already
        QCOMPARE(Plans::fullscreen(*t.find(6), Plans::Fullscreen::Off).commands, QStringList{QStringLiteral("[con_id=6] fullscreen disable")});
        QVERIFY(Plans::fullscreen(*t.find(5), Plans::Fullscreen::Off).commands.isEmpty());     // already
        QCOMPARE(Plans::fullscreen(*t.find(5)).commands, QStringList{QStringLiteral("[con_id=5] fullscreen toggle")});
        // never a resize: the container is not touched, sway sends the client set_fullscreen itself
        for (const QString &c : Plans::fullscreen(*t.find(5), Plans::Fullscreen::On).commands) QVERIFY(!c.contains(QLatin1String("resize")));
    }

    void bringHerePlans() {
        Tree t;
        // 5: floating, far off screen on workspace 2, bigger than the screen; 6: on screen floating; 7: tiled on 2; 8: minimised floating
        QVERIFY(parseTree(treeJson({{5, "2", true, QRect(5000, 4000, 3000, 2000)}, {6, "2", true, QRect(10, 10, 500, 400)},
                                    {7, "2", false, QRect(0, 23, 960, 1057)},
                                    {8, "2", true, QRect(100, 100, 600, 400), {"taos_min_8", "taos_ws_2", "taos_mfl", "taos_at_1", "taos_geo_100_100_600_400"}, false, true}}), &t));
        const Plan big = Plans::bringHere(*t.find(5), t);
        QVERIFY2(big.ok(), qPrintable(big.error));
        QCOMPARE(big.commands.value(0), QStringLiteral("[con_id=5] move container to workspace \"1\""));
        QVERIFY(big.commands.contains(QStringLiteral("[con_id=5] resize set 1152 739")));          // 60 % x 70 % of 1920x1057
        QVERIFY(big.commands.contains(QStringLiteral("[con_id=5] move absolute position 384 182")));  // centred in 0,23 1920x1057
        QVERIFY(big.commands.last().contains(QLatin1String("focus")));
        const Plan fits = Plans::bringHere(*t.find(6), t);
        QVERIFY(fits.commands.contains(QStringLiteral("[con_id=6] resize set 500 400")));          // its own size is kept
        QVERIFY(fits.commands.contains(QStringLiteral("[con_id=6] move absolute position 710 351")));
        const Plan tiled = Plans::bringHere(*t.find(7), t);
        QVERIFY(tiled.commands.contains(QStringLiteral("[con_id=7] move container to workspace \"1\"")));
        for (const QString &c : tiled.commands) QVERIFY2(!c.contains(QLatin1String("resize")) && !c.contains(QLatin1String("floating")), qPrintable(c));
        const Plan min = Plans::bringHere(*t.find(8), t);
        QVERIFY2(min.ok(), qPrintable(min.error));
        QVERIFY(min.commands.contains(QStringLiteral("[con_id=8] scratchpad show")));
        QVERIFY(min.commands.contains(QStringLiteral("[con_id=8] unmark \"taos_min_8\"")));
        QVERIFY(min.commands.contains(QStringLiteral("[con_id=8] move absolute position 660 351")));
        for (const QString &c : min.commands) QVERIFY2(!c.contains(QLatin1String("workspace \"2\"")), qPrintable(c));   // does not go to where it came from
        // already here and on screen: still centred (it may be half off the edge), no workspace move
        Tree here;
        QVERIFY(parseTree(treeJson({{5, "1", true, QRect(-300, -200, 500, 400)}}), &here));
        const Plan edge = Plans::bringHere(*here.find(5), here);
        for (const QString &c : edge.commands) QVERIFY2(!c.contains(QLatin1String("move container")), qPrintable(c));
        QVERIFY(edge.commands.contains(QStringLiteral("[con_id=5] move absolute position 710 351")));
        // fullscreen: only the workspace changes
        Tree fs;
        QVERIFY(parseTree(treeJson({{5, "2", true, QRect(0, 0, 1920, 1080), {}, true}}), &fs));
        const Plan f = Plans::bringHere(*fs.find(5), fs);
        QVERIFY(f.commands.contains(QStringLiteral("[con_id=5] move container to workspace \"1\"")));
        for (const QString &c : f.commands) QVERIFY2(!c.contains(QLatin1String("resize")) && !c.contains(QLatin1String("fullscreen")), qPrintable(c));
        // a maximised window fills the usable area again
        Tree mx;
        QVERIFY(parseTree(treeJson({{5, "2", true, QRect(0, 23, 1920, 1057), {"taos_full", "taos_fgeo_1_2_400_300"}}}), &mx));
        QVERIFY(Plans::bringHere(*mx.find(5), mx).commands.contains(QStringLiteral("[con_id=5] resize set 1920 1057")));
    }

    void resetSizePlans() {
        Tree t;
        QVERIFY(parseTree(treeJson({{5, "2", true, QRect(5000, 4000, 3000, 2000), {"taos_full", "taos_fgeo_1_2_400_300"}},
                                    {6, "1", false, QRect(0, 23, 960, 1057)}, {7, "1", true, QRect(0, 0, 1920, 1080), {}, true}}), &t));
        const Plan p = Plans::resetSize(*t.find(5), t);
        QVERIFY2(p.ok(), qPrintable(p.error));
        QVERIFY(p.commands.contains(QStringLiteral("[con_id=5] resize set 1152 739")));
        QVERIFY(p.commands.contains(QStringLiteral("[con_id=5] move absolute position 384 182")));   // on ITS workspace, not the focused one
        QVERIFY(p.commands.contains(QStringLiteral("[con_id=5] unmark \"taos_full\"")));
        // workspace 2 is not shown: the position pulls the window to the shown workspace, so it is sent back to its own
        QCOMPARE(p.commands.last(), QStringLiteral("[con_id=5] move container to workspace \"2\""));
        QCOMPARE(p.commands.count(QStringLiteral("[con_id=5] move container to workspace \"2\"")), 1);
        QCOMPARE(p.settleHidden, QStringLiteral("2"));
        const Plan tiled = Plans::resetSize(*t.find(6), t);
        QVERIFY(!tiled.ok());
        QVERIFY(tiled.error.contains(QLatin1String("tiled")));
        const Plan fs = Plans::resetSize(*t.find(7), t);
        QCOMPARE(fs.commands.value(0), QStringLiteral("[con_id=7] fullscreen disable"));
        QVERIFY(fs.commands.contains(QStringLiteral("[con_id=7] resize set 1152 739")));
    }

    void moveToSpacePlans() {
        Tree t;
        QVERIFY(parseTree(treeJson({{5, "1", true, QRect(100, 100, 400, 300)}, {6, "1", false, QRect(0, 23, 960, 1057)},
                                    {7, "1", true, QRect(100, 100, 600, 400), {"taos_min_7", "taos_ws_1", "taos_mfl", "taos_at_1"}, false, true}}), &t));
        const Plan p = Plans::moveToSpace(*t.find(5), t, QStringLiteral("2"));
        QVERIFY2(p.ok(), qPrintable(p.error));
        // workspace 2 is not shown: position first (that lands the window on the shown workspace), then on to space 2
        QVERIFY(p.commands.contains(QStringLiteral("[con_id=5] resize set 400 300")));       // sensible size kept, centred in the new space
        QVERIFY(p.commands.contains(QStringLiteral("[con_id=5] move absolute position 760 401")));
        QCOMPARE(p.commands.last(), QStringLiteral("[con_id=5] move container to workspace \"2\""));
        QCOMPARE(p.settleHidden, QStringLiteral("2"));      // a settle that pulls the window to the shown workspace sends it back
        // a shown target: move first, then place, and settle
        Tree shown;
        QVERIFY(parseTree(treeJson({{5, "1", true, QRect(100, 100, 400, 300)}}, QStringLiteral("2")), &shown));
        const Plan sh = Plans::moveToSpace(*shown.find(5), shown, QStringLiteral("2"));
        QCOMPARE(sh.commands.value(0), QStringLiteral("[con_id=5] move container to workspace \"2\""));
        QVERIFY(sh.settle.isValid() && sh.settleHidden.isEmpty());
        QCOMPARE(Plans::moveToSpace(*t.find(6), t, QStringLiteral("2")).commands, QStringList{QStringLiteral("[con_id=6] move container to workspace \"2\"")});   // tiled: just moved
        QVERIFY(Plans::moveToSpace(*t.find(5), t, QStringLiteral("1")).commands.isEmpty());     // already there
        // a name with quotes cannot break the command
        QVERIFY(Plans::moveToSpace(*t.find(6), t, QStringLiteral("a\"b;c")).commands.value(0).contains(QStringLiteral("workspace \"a\\\"b;c\"")));
        // minimised: only the mark moves; the window stays in the scratchpad
        const Plan m = Plans::moveToSpace(*t.find(7), t, QStringLiteral("2"));
        QVERIFY2(m.ok(), qPrintable(m.error));
        QVERIFY(m.commands.contains(QStringLiteral("[con_id=7] unmark \"taos_ws_1\"")));
        QVERIFY(m.commands.contains(QStringLiteral("[con_id=7] mark --add \"taos_ws_2\"")));
        for (const QString &c : m.commands) QVERIFY2(!c.contains(QLatin1String("move container")) && !c.contains(QLatin1String("scratchpad")), qPrintable(c));
        // an empty name is the focused workspace
        Tree two;
        QVERIFY(parseTree(treeJson({{5, "2", true, QRect(100, 100, 400, 300)}}), &two));
        QVERIFY(Plans::moveToSpace(*two.find(5), two, QString()).commands.value(0).contains(QLatin1String("workspace \"1\"")));   // shown: moved first
    }

    void newSpaceNameFollowsTheOverview() {
        Tree t;
        QVERIFY(parseTree(treeJson({{5, "1", true, QRect(100, 100, 400, 300)}}), &t));
        QCOMPARE(Plans::newSpaceName(t), QStringLiteral("3"));     // 1 and 2 exist: the next number after the highest
        FakeTransport tx;
        tx.tree = treeJson({{5, "1", true, QRect(100, 100, 400, 300)}});
        WindowController c(&tx);
        QString created, err;
        QVERIFY2(c.newSpace(QStringLiteral("5"), &created, &err), qPrintable(err));
        QCOMPARE(created, QStringLiteral("3"));
        QVERIFY(tx.commands.contains(QStringLiteral("[con_id=5] move container to workspace \"3\"")));
        QVERIFY(tx.commands.indexOf(QStringLiteral("workspace \"3\"")) > tx.commands.indexOf(QStringLiteral("[con_id=5] move container to workspace \"3\"")));   // then shown
        tx.commands.clear();
        QVERIFY2(c.newSpace(QString(), &created, &err), qPrintable(err));
        QCOMPARE(tx.commands, QStringList{QStringLiteral("workspace \"3\"")});
    }

    void controllerVerbsRunTheirPlans() {
        FakeTransport tx;
        tx.tree = treeJson({{5, "2", true, QRect(5000, 4000, 3000, 2000)}});
        WindowController c(&tx);
        QString err;
        QVERIFY2(c.bringHere(QStringLiteral("5"), &err), qPrintable(err));
        QVERIFY(tx.commands.contains(QStringLiteral("[con_id=5] move container to workspace \"1\"")));
        tx.commands.clear();
        QVERIFY2(c.maximise(QStringLiteral("5"), &err), qPrintable(err));
        QVERIFY(tx.commands.contains(QStringLiteral("[con_id=5] resize set 1920 1057")));
        tx.commands.clear();
        QVERIFY2(c.fullscreen(QStringLiteral("5"), &err, Plans::Fullscreen::On), qPrintable(err));
        QCOMPARE(tx.commands, QStringList{QStringLiteral("[con_id=5] fullscreen enable")});
        QVERIFY2(c.moveToSpace(QStringLiteral("5"), QStringLiteral("1"), &err), qPrintable(err));
        QVERIFY2(!c.resetSize(QStringLiteral("99"), &err), "unknown id");
        QVERIFY(err.contains(QLatin1String("no window with id 99")));
    }

    void aForeignWebAppWindowIsControlledById() {
        // A web app is a Chromium window: no taOS title bar, an app_id like chrome-<host>__<path>-<profile>. Every
        // command goes by container id, so the app_id does not matter.
        QByteArray tree = fixture("tree-min.json");
        tree.replace("\"app_id\": \"app.two\"", "\"app_id\": \"chrome-www.youtube.com__-k3j9x0a1bc\"");
        FakeTransport tx;
        tx.tree = tree;
        WindowController c(&tx);
        QString err;
        QVERIFY(c.close(QStringLiteral("6"), &err));
        QCOMPARE(tx.commands, QStringList{QStringLiteral("[con_id=6] kill")});
        tx.commands.clear();
        QVERIFY(c.minimise(QStringLiteral("5"), &err) || true);
        QVERIFY(c.full(QStringLiteral("6"), &err) || !err.isEmpty());
        QVERIFY(!tx.commands.isEmpty());
        QVERIFY(tx.commands.first().contains(QLatin1String("con_id=")));
    }

    void controllerResolves() {
        FakeTransport tx;
        tx.tree = fixture("tree-min.json");
        WindowController c(&tx);
        QString err;
        // focused = window 5
        QVERIFY(c.close(QStringLiteral("focused"), &err));
        QCOMPARE(tx.commands, QStringList{QStringLiteral("[con_id=5] kill")});
        tx.commands.clear();
        QVERIFY(c.restore(QStringLiteral("last"), &err));
        QVERIFY(tx.commands.first().contains(QLatin1String("scratchpad show")));
        tx.commands.clear();
        // focus on a minimised window restores it
        QVERIFY(c.focus(QStringLiteral("6"), &err));
        QVERIFY(tx.commands.first().contains(QLatin1String("scratchpad show")));
        tx.commands.clear();
        // toggle on a minimised window restores, on a plain one minimises
        QVERIFY(c.toggleMinimise(QStringLiteral("6"), &err));
        QVERIFY(tx.commands.first().contains(QLatin1String("scratchpad show")));
        tx.commands.clear();
        QVERIFY(c.toggleMinimise(QStringLiteral("5"), &err));
        QVERIFY(tx.commands.last().contains(QLatin1String("move scratchpad")));
        // errors are sentences
        tx.commands.clear();
        QVERIFY(!c.close(QStringLiteral("77"), &err));
        QVERIFY(err.contains(QLatin1String("no window with id 77")));
        QVERIFY(!c.close(QStringLiteral("banana"), &err));
        QVERIFY(err.contains(QLatin1String("banana")));
        QVERIFY(!c.close(QStringLiteral("last"), &err));   // "last" is for restore and focus only
        QVERIFY(tx.commands.isEmpty());
        // a failing command is reported
        tx.failOn = QStringLiteral("kill");
        QVERIFY(!c.close(QStringLiteral("5"), &err));
        QCOMPARE(err, QStringLiteral("boom"));
        // no minimised window
        tx.tree = fixture("tree-plain.json");
        QVERIFY(!c.restore(QStringLiteral("last"), &err));
        QVERIFY(err.contains(QLatin1String("no window is minimised")));
    }

    void ownWindow() {
        Tree t;
        QVERIFY(parseTree(fixture("tree-plain.json"), &t));
        const int pidB = t.find(6)->pid;
        QCOMPARE(findOwn(t, pidB, QString())->id, 6);
        QCOMPARE(findOwn(t, 1, QStringLiteral("app.one"))->id, 5);   // pid unknown: by app_id
        QVERIFY(!findOwn(t, 1, QStringLiteral("nobody")));
        FakeTransport tx;
        tx.tree = fixture("tree-plain.json");
        WindowController c(&tx);
        QCOMPARE(c.ownWindowId(pidB, QString()), 6);
    }

    void diffEvents() {
        Tree a, b, m;
        QVERIFY(parseTree(fixture("tree-plain.json"), &a));
        QVERIFY(parseTree(fixture("tree-min.json"), &m));
        auto kinds = [](const QList<WindowEvent> &l) { QStringList k; for (const auto &e : l) k << e.kind + QString::number(e.window.id); return k; };
        { QStringList k = kinds(diffWindows(a, m)); QVERIFY(k.contains("minimise6")); QVERIFY(k.size() <= 2); }
        { QStringList k = kinds(diffWindows(m, a)); QVERIFY(k.contains("restore6")); QVERIFY(k.size() <= 2); }
        b = a;
        b.windows.removeLast();
        QCOMPARE(kinds(diffWindows(a, b)), QStringList{"close6"});
        QCOMPARE(kinds(diffWindows(b, a)), QStringList{"new6"});
        b = a;
        b.windows[0].title = QStringLiteral("changed");
        QCOMPARE(kinds(diffWindows(a, b)), QStringList{"title5"});
        Tree a2 = a;
        a2.windows[0].focused = false;
        a2.windows[1].focused = false;
        b = a2;
        b.windows[1].focused = true;
        QCOMPARE(kinds(diffWindows(a2, b)), QStringList{"focus6"});
        QVERIFY(diffWindows(a, a).isEmpty());
    }

    void ipcFraming() {
        const QByteArray msg = Ipc::encode(Ipc::RunCommand, "nop");
        QByteArray buf = msg.left(10);
        quint32 t = 0;
        QByteArray p;
        QVERIFY(!Ipc::take(&buf, &t, &p));
        buf = msg + msg;
        QVERIFY(Ipc::take(&buf, &t, &p));
        QCOMPARE(p, QByteArray("nop"));
        QVERIFY(Ipc::take(&buf, &t, &p));
        QVERIFY(buf.isEmpty());
    }

    void modesFileRoundTrip() {
        QTemporaryDir dir;
        QVERIFY(dir.isValid());
        const QString p = dir.path() + QStringLiteral("/taos/desktop.json");
        DesktopModes m;
        QVERIFY(Modes::read(p, &m));   // no file: desktop everywhere
        QCOMPARE(m.defaultMode, QStringLiteral("desktop"));
        QCOMPARE(m.modeFor(QStringLiteral("7")), QStringLiteral("desktop"));
        m.workspaces.insert(QStringLiteral("2: web"), QStringLiteral("tiling"));
        m.defaultMode = QStringLiteral("tiling");
        QVERIFY(Modes::write(p, m));   // creates the folder, atomically
        DesktopModes back;
        QVERIFY(Modes::read(p, &back));
        QVERIFY(back == m);
        QCOMPARE(back.modeFor(QStringLiteral("2: web")), QStringLiteral("tiling"));
        QCOMPARE(back.modeFor(QStringLiteral("other")), QStringLiteral("tiling"));
        // Bad values are ignored, a broken file is reported and means defaults.
        QFile f(p);
        QVERIFY(f.open(QIODevice::WriteOnly | QIODevice::Truncate));
        f.write("{\"default\": \"weird\", \"workspaces\": {\"1\": \"tiling\", \"2\": 5}}");
        f.close();
        QVERIFY(Modes::read(p, &back));
        QCOMPARE(back.defaultMode, QStringLiteral("desktop"));
        QCOMPARE(back.workspaces.size(), 1);
        QVERIFY(f.open(QIODevice::WriteOnly | QIODevice::Truncate));
        f.write("not json");
        f.close();
        QString err;
        QVERIFY(!Modes::read(p, &back, &err));
        QVERIFY(back.workspaces.isEmpty());
        QVERIFY(Modes::valid(QStringLiteral("tiling")) && !Modes::valid(QStringLiteral("grid")));
    }

    void modeCommands() {
        Tree t;
        QVERIFY(parseTree(fixture("tree-plain.json"), &t));   // 5 tiled, 6 floating, both on workspace 1
        const QStringList toDesktop = modeSwitchCommands(t, QStringLiteral("1"), QStringLiteral("desktop"));
        QVERIFY(toDesktop.contains(QStringLiteral("[con_id=5] floating enable")));
        QVERIFY(toDesktop.contains(QStringLiteral("[con_id=5] resize set 1280 720")));
        QVERIFY(toDesktop.contains(QStringLiteral("[con_id=5] move absolute position 0 0")));
        QVERIFY(!toDesktop.join(QLatin1Char('\n')).contains(QLatin1String("con_id=6")));   // already floating
        const QStringList toTiling = modeSwitchCommands(t, QStringLiteral("1"), QStringLiteral("tiling"));
        QCOMPARE(toTiling, QStringList{QStringLiteral("[con_id=6] floating disable")});
        QVERIFY(modeSwitchCommands(t, QStringLiteral("9"), QStringLiteral("tiling")).isEmpty());   // other workspace
        // Minimised windows are left alone.
        Tree m;
        QVERIFY(parseTree(fixture("tree-min.json"), &m));
        QVERIFY(modeSwitchCommands(m, QStringLiteral("1"), QStringLiteral("tiling")).isEmpty());
        // A new window: floated, sized to 60 % x 70 %, inside the workspace, and cascaded behind the other floating one.
        DesktopModes modes;
        Window w = *t.find(5);
        const QStringList first = newWindowCommands(t, w, modes);
        QCOMPARE(first.size(), 3);
        QVERIFY(first[2].contains(QStringLiteral("move absolute position")));   // `move position` is relative to the workspace
        QVERIFY(first[1].startsWith(QStringLiteral("[con_id=5] resize set 768 504")));
        modes.workspaces.insert(QStringLiteral("1"), QStringLiteral("tiling"));
        QVERIFY(newWindowCommands(t, w, modes).isEmpty());
        QVERIFY(newWindowCommands(t, *t.find(6), DesktopModes()).isEmpty());   // already floating
        Tree none = t;
        none.windows[1].minimised = true;
        const QStringList centred = newWindowCommands(none, w, DesktopModes());
        QVERIFY(centred[2] != newWindowCommands(t, w, DesktopModes())[2]);   // the floating one moves the cascade
        // An application that asked for a size keeps it (clamped to the free area): a 1500 x 950 request on a
        // 1280 x 720 workspace is cut to the area less a margin; a 600 x 400 one is kept as it is.
        Window fl = w;
        fl.floating = true;
        const QRect big = placeNewWindow(t, fl, DesktopModes(), QSize(1500, 950));
        QCOMPARE(big.size(), QSize(1248, 688));
        QVERIFY(QRect(0, 0, 1280, 720).contains(big));
        QCOMPARE(placeNewWindow(t, fl, DesktopModes(), QSize(600, 400)).size(), QSize(600, 400));
        QCOMPARE(placeNewWindow(t, fl, DesktopModes(), QSize()).size(), QSize(768, 504));   // no request: the default
    }

    void modeEventJson() {
        WindowEvent e;
        e.kind = QStringLiteral("mode");
        e.workspace = QStringLiteral("1");
        e.mode = QStringLiteral("tiling");
        const QJsonObject o = e.toJson();
        QCOMPARE(o.value(QStringLiteral("event")).toString(), QStringLiteral("mode"));
        QCOMPARE(o.value(QStringLiteral("workspace")).toString(), QStringLiteral("1"));
        QCOMPARE(o.value(QStringLiteral("mode")).toString(), QStringLiteral("tiling"));
        QVERIFY(!o.contains(QStringLiteral("window")));
    }

    void noSway() {
        SwayTransport tx(QStringLiteral("/nonexistent/sway.sock"));
        QString err;
        QByteArray j;
        QVERIFY(!tx.getTree(&j, &err));
        QVERIFY(err.contains(QLatin1String("cannot connect")));
    }
};

QTEST_GUILESS_MAIN(TestWindowCore)
#include "test_windowcore.moc"
