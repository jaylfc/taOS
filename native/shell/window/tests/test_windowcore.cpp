// SPDX-License-Identifier: AGPL-3.0-or-later
// Logic of the window engine against fixture trees (real sway get_tree output) and a fake transport.
#include <QFile>
#include <QTemporaryDir>
#include <QJsonDocument>
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
        QCOMPARE(keys, QStringList({"app_id", "floating", "focused", "fullscreen", "id", "minimised", "output", "pid", "title", "workspace"}));
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
        QCOMPARE(big.size(), QSize(1152, 648));   // 90 % of the free area
        QVERIFY(QRect(0, 0, 1280, 720).contains(big));
        QCOMPARE(placeNewWindow(t, fl, DesktopModes(), QSize(600, 400)).size(), QSize(600, 400));
        QCOMPARE(placeNewWindow(t, fl, DesktopModes(), QSize()).size(), QSize(768, 504));   // no request: the default
    }

    // The owner's case: an app asks for 1500 x 950 on a 3840 x 1200 output with a bar on top and a dock below.
    void placementKeepsWindowsOnScreen() {
        Tree t;
        Workspace ws;
        ws.name = QStringLiteral("1"); ws.output = QStringLiteral("DP-1");
        ws.rect = QRect(0, 48, 3840, 1066);   // usable: y 48..1114
        t.workspaces.push_back(ws);
        Window w;
        w.id = 1; w.appId = QStringLiteral("my.taos.survival"); w.workspace = QStringLiteral("1"); w.output = QStringLiteral("DP-1");
        w.floating = true; w.pid = 100;
        t.windows.push_back(w);
        const QRect usable = ws.rect;
        QRect r = placeNewWindow(t, w, DesktopModes(), QSize(1500, 950));
        QVERIFY2(usable.contains(r), qPrintable(QStringLiteral("%1,%2 %3x%4").arg(r.x()).arg(r.y()).arg(r.width()).arg(r.height())));
        QVERIFY(r.height() <= usable.height() * 9 / 10);
        QCOMPARE(r.size(), QSize(1500, 950));   // fits within 90 %: kept
        QVERIFY(qAbs(r.center().x() - 1920) <= 1);   // centred
        // an app asking for more than the screen: cut to 90 % of the usable area, still inside
        r = placeNewWindow(t, w, DesktopModes(), QSize(5000, 3000));
        QCOMPARE(r.size(), QSize(3456, 959));
        QVERIFY(usable.contains(r));
        // a window that is already floating on a tiling workspace is placed too (a dialog sway floated)
        DesktopModes tiling;
        tiling.workspaces.insert(QStringLiteral("1"), QStringLiteral("tiling"));
        QVERIFY(placeNewWindow(t, w, tiling, QSize(1500, 950)).isValid());
        // no request: the default stays modest on a wide output
        QCOMPARE(placeNewWindow(t, w, DesktopModes(), QSize()).width(), 1600);
    }
    void placementCascadesFromTheLastWindowOfTheApp() {
        Tree t;
        Workspace ws;
        ws.name = QStringLiteral("1"); ws.output = QStringLiteral("DP-1"); ws.rect = QRect(0, 0, 1920, 1080);
        t.workspaces.push_back(ws);
        Window a; a.id = 1; a.appId = QStringLiteral("ed"); a.workspace = QStringLiteral("1"); a.output = QStringLiteral("DP-1");
        a.floating = true; a.pid = 5; a.rect = QRect(300, 200, 900, 600);
        Window b = a; b.id = 2; b.pid = 6; b.rect = QRect(600, 100, 900, 600);
        b.appId = QStringLiteral("ed");
        t.windows << a << b;
        Window n = a; n.id = 3; n.pid = 7;
        t.windows << n;
        const QRect r = placeNewWindow(t, n, DesktopModes(), QSize(900, 600));
        QCOMPARE(r.topLeft(), QPoint(632, 132));   // the last one of the app, plus 32 px each way
        // at the edge it wraps to centre instead of leaving the screen
        t.windows[1].rect = QRect(1000, 480, 900, 600);
        QVERIFY(QRect(0, 0, 1920, 1080).contains(placeNewWindow(t, n, DesktopModes(), QSize(900, 600))));
    }
    void placementCentresADialogOnItsParent() {
        Tree t;
        Workspace ws;
        ws.name = QStringLiteral("1"); ws.output = QStringLiteral("DP-1"); ws.rect = QRect(0, 0, 1920, 1080);
        t.workspaces.push_back(ws);
        Window p; p.id = 1; p.appId = QStringLiteral("ed"); p.workspace = QStringLiteral("1"); p.output = QStringLiteral("DP-1");
        p.floating = true; p.pid = 5; p.rect = QRect(100, 100, 1000, 700); p.focused = true;
        Window d = p; d.id = 2; d.focused = false; d.rect = QRect(0, 0, 400, 200);
        t.windows << p << d;
        const QRect r = placeNewWindow(t, d, DesktopModes(), QSize(400, 200));
        QCOMPARE(r.center(), p.rect.center());
    }
    void placementRemembersAppGeometryPerOutput() {
        Tree t;
        Workspace ws;
        ws.name = QStringLiteral("1"); ws.output = QStringLiteral("DP-1"); ws.rect = QRect(0, 48, 3840, 1066);
        t.workspaces.push_back(ws);
        Window w;
        w.id = 1; w.appId = QStringLiteral("ed"); w.workspace = QStringLiteral("1"); w.output = QStringLiteral("DP-1");
        w.floating = true; w.pid = 5; w.rect = QRect(200, 120, 1000, 700);
        t.windows << w;
        QTemporaryDir dir;
        const QString file = dir.filePath(QStringLiteral("g.json"));
        PlacementMemory m;
        QVERIFY(m.remember(w));
        QString err;
        QVERIFY2(m.write(file, &err), qPrintable(err));
        PlacementMemory back;
        QVERIFY(back.read(file));
        QCOMPARE(placeNewWindow(t, w, DesktopModes(), QSize(1500, 950), &back), QRect(200, 120, 1000, 700));
        // another output has nothing remembered; a remembered rectangle that no longer fits is ignored
        Window other = w; other.output = QStringLiteral("DP-2");
        PlacementMemory big; Window huge = w; huge.rect = QRect(0, 48, 3800, 1060);
        QVERIFY(big.remember(huge));
        QVERIFY(placeNewWindow(t, w, DesktopModes(), QSize(1500, 950), &big) != huge.rect);
        // fullscreen, minimised or maximised ("full size") windows are not remembered
        Window fs = w; fs.fullscreen = true;
        PlacementMemory none;
        QVERIFY(!none.remember(fs));
        fs = w; fs.fullSize = true;
        QVERIFY(!none.remember(fs));
        QVERIFY(none.byOutput.isEmpty());
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
