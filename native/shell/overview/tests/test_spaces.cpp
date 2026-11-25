// SPDX-License-Identifier: AGPL-3.0-or-later
#include <QJsonArray>
#include <QJsonDocument>
#include <QJsonObject>
#include <QTemporaryDir>
#include <QtTest>

#include "spaces.h"

using namespace Spaces;

namespace {
struct Rec : Sink {
    QStringList cmds;
    void command(const QString &c) override { cmds << c; }
};

struct Snap {
    Snapshot s;
    Snap() { s.outputs.push_back({ QStringLiteral("HEADLESS-1"), QRect(0, 0, 1920, 1080), QString(), true }); s.focusedOutput = QStringLiteral("HEADLESS-1"); }
    // a workspace; `current` makes it the one on screen
    Snap &ws(const QString &name, bool current = false) {
        WsInfo w;
        w.name = name;
        bool ok = false;
        w.num = name.toInt(&ok);
        if (!ok) w.num = -1;
        w.output = QStringLiteral("HEADLESS-1");
        w.visible = w.focused = current;
        w.rect = QRect(0, 0, 1920, 1080);
        s.workspaces.push_back(w);
        if (current) { s.outputs[0].currentWorkspace = name; s.focusedWorkspace = name; }
        return *this;
    }
    Snap &win(int id, const QString &ws, bool floating = false, bool minimised = false) {
        WinInfo w;
        w.conId = id;
        w.workspace = ws;
        w.output = QStringLiteral("HEADLESS-1");
        w.floating = floating;
        w.minimised = minimised;
        s.windows.push_back(w);
        return *this;
    }
};
const QString OUT = QStringLiteral("HEADLESS-1");
QStringList labels(const Engine &e) { QStringList l; for (const View &v : e.views()) l << v.label; return l; }
QStringList keys(const Engine &e) { QStringList l; for (const View &v : e.views()) l << v.key; return l; }
}  // namespace

class TestSpaces : public QObject {
    Q_OBJECT
    QTemporaryDir dir;
    QString path(const char *n = "spaces.json") const { return dir.filePath(QString::fromLatin1(n)); }
private slots:
    void init() { QFile::remove(path()); }

    // The owner's case: he made a new space, moved on, and the first one disappeared from sway.
    void firstRunWithOnlySpaceTwo() {
        Store st(path()); Rec rec; Engine e(&st, &rec);
        e.sync(Snap().ws(QStringLiteral("2"), true).win(10, QStringLiteral("2")).s, OUT);
        QCOMPARE(labels(e), QStringList { QStringLiteral("Desktop 1") });
        QCOMPARE(keys(e), QStringList { QStringLiteral("2") });
        QVERIFY(e.views()[0].current);
    }
    void emptySpacesSurviveSwayDroppingThem() {
        Store st(path()); Rec rec; Engine e(&st, &rec);
        e.sync(Snap().ws(QStringLiteral("1")).ws(QStringLiteral("2"), true).win(1, QStringLiteral("1")).win(2, QStringLiteral("2")).s, OUT);
        QCOMPARE(labels(e).size(), 2);
        // space 1 loses its window: sway forgets workspace 1
        e.sync(Snap().ws(QStringLiteral("2"), true).win(2, QStringLiteral("2")).s, OUT);
        QCOMPARE(keys(e), (QStringList { QStringLiteral("1"), QStringLiteral("2") }));
        QVERIFY(!e.views()[0].live);
        QCOMPARE(e.views()[0].windows, 0);
    }
    void survivesARestart() {
        {
            Store st(path()); Rec rec; Engine e(&st, &rec);
            e.sync(Snap().ws(QStringLiteral("1")).ws(QStringLiteral("2"), true).win(1, QStringLiteral("1")).win(2, QStringLiteral("2")).s, OUT);
            QVERIFY(e.handle({ QStringLiteral("space"), QStringLiteral("add"), QStringLiteral("Mail") }).startsWith(QLatin1String("ok")));
            QCOMPARE(e.handle({ QStringLiteral("space"), QStringLiteral("rename"), QStringLiteral("1"), QStringLiteral("Work") }), QStringLiteral("ok"));
        }
        Store st(path()); QString err; QVERIFY2(st.load(&err), qPrintable(err));
        Rec rec; Engine e(&st, &rec);
        e.sync(Snap().ws(QStringLiteral("2"), true).win(2, QStringLiteral("2")).s, OUT);   // a new login: only workspace 2 is alive
        QCOMPARE(labels(e), (QStringList { QStringLiteral("Work"), QStringLiteral("Desktop 2"), QStringLiteral("Mail") }));
    }
    void defaultNamesFollowOrderAndCustomNamesStay() {
        Store st(path()); Rec rec; Engine e(&st, &rec);
        e.sync(Snap().ws(QStringLiteral("1"), true).win(1, QStringLiteral("1")).s, OUT);
        QString k2, k3;
        QVERIFY(e.add({}, &k2).isEmpty());
        QVERIFY(e.add(QStringLiteral("  Mail \n box "), &k3).isEmpty());
        QCOMPARE(k2, QStringLiteral("2"));
        QCOMPARE(labels(e), (QStringList { QStringLiteral("Desktop 1"), QStringLiteral("Desktop 2"), QStringLiteral("Mail box") }));
        QVERIFY(e.move(QStringLiteral("3"), 0).isEmpty());   // Mail box first
        QCOMPARE(labels(e), (QStringList { QStringLiteral("Mail box"), QStringLiteral("Desktop 2"), QStringLiteral("Desktop 3") }));
        QCOMPARE(keys(e), (QStringList { QStringLiteral("3"), QStringLiteral("1"), QStringLiteral("2") }));   // sway names never change
        QVERIFY(e.rename(QStringLiteral("3"), QString()).isEmpty());   // empty name: back to the default
        QCOMPARE(labels(e)[0], QStringLiteral("Desktop 1"));
        QCOMPARE(cleanName(QString(80, QLatin1Char('x'))).size(), 40);
    }
    void freshKeyNeverReusesALiveWorkspace() {
        Store st(path()); Rec rec; Engine e(&st, &rec);
        e.sync(Snap().ws(QStringLiteral("1"), true).ws(QStringLiteral("2")).win(1, QStringLiteral("1")).win(2, QStringLiteral("2")).s, OUT);
        QVERIFY(e.remove(QStringLiteral("2")).isEmpty());
        // sway has not yet dropped workspace 2 (a window event is in flight): the new space must not be "2"
        QString k;
        QVERIFY(e.add({}, &k).isEmpty());
        QVERIFY2(k != QLatin1String("2"), qPrintable(k));
    }
    void bareStartupWorkspaceIsNotAdoptedAndPersonGoesBack() {
        { Store st(path()); Rec r; Engine e(&st, &r);
          e.sync(Snap().ws(QStringLiteral("1")).ws(QStringLiteral("2"), true).win(1, QStringLiteral("1")).win(2, QStringLiteral("2")).s, OUT); }
        QVERIFY(QFile::copy(path(), path("saved.json")));
        {   // sway booted on an empty workspace that IS one of the spaces: nothing to fix
            Store st(path()); QVERIFY(st.load(nullptr));
            Rec rec; Engine e(&st, &rec);
            e.sync(Snap().ws(QStringLiteral("1"), true).s, OUT);
            QVERIFY(rec.cmds.isEmpty());
        }
        QFile::remove(path()); QVERIFY(QFile::copy(path("saved.json"), path()));
        Store st2(path()); QVERIFY(st2.load(nullptr));
        Rec rec2; Engine e2(&st2, &rec2);
        e2.sync(Snap().ws(QStringLiteral("9"), true).s, OUT);   // booted on a stray empty "9"
        QCOMPARE(keys(e2), (QStringList { QStringLiteral("1"), QStringLiteral("2") }));
        QCOMPARE(rec2.cmds, QStringList { QStringLiteral("workspace number 2") });
    }
    void unknownWorkspaceWithWindowsIsAdoptedAtTheEnd() {
        Store st(path()); Rec rec; Engine e(&st, &rec);
        e.sync(Snap().ws(QStringLiteral("1"), true).win(1, QStringLiteral("1")).s, OUT);
        e.sync(Snap().ws(QStringLiteral("1"), true).ws(QStringLiteral("web")).win(1, QStringLiteral("1")).win(5, QStringLiteral("web")).s, OUT);
        QCOMPARE(keys(e), (QStringList { QStringLiteral("1"), QStringLiteral("web") }));
        QCOMPARE(labels(e), (QStringList { QStringLiteral("Desktop 1"), QStringLiteral("web") }));
        QCOMPARE(Engine::wsRef(QStringLiteral("web")), QStringLiteral("\"web\""));
        QCOMPARE(Engine::wsRef(QStringLiteral("3")), QStringLiteral("number 3"));
    }
    void removeMovesWindowsToTheNeighbourAndFollowsFocus() {
        Store st(path()); Rec rec; Engine e(&st, &rec);
        e.sync(Snap().ws(QStringLiteral("1")).ws(QStringLiteral("2"), true).ws(QStringLiteral("3"))
                   .win(1, QStringLiteral("1")).win(2, QStringLiteral("2")).win(3, QStringLiteral("2"), true)
                   .win(4, QStringLiteral("2"), false, true).win(5, QStringLiteral("3")).s, OUT);
        QCOMPARE(e.handle({ QStringLiteral("space"), QStringLiteral("remove"), QStringLiteral("2") }), QStringLiteral("ok"));
        QCOMPARE(keys(e), (QStringList { QStringLiteral("1"), QStringLiteral("3") }));
        QCOMPARE(rec.cmds.value(0), QStringLiteral("workspace number 3"));        // the one on screen: leave first
        QVERIFY(rec.cmds.contains(QStringLiteral("[con_id=2] move container to workspace number 3")));
        QVERIFY(rec.cmds.contains(QStringLiteral("[con_id=3] move container to workspace number 3")));   // floating too
        QVERIFY(rec.cmds.contains(QStringLiteral("[con_id=4] mark --add \"taos_ws_3\"")));              // minimised: its home changes
        QVERIFY(rec.cmds.contains(QStringLiteral("[con_id=4] unmark \"taos_ws_2\"")));
        QVERIFY(!rec.cmds.join(QLatin1Char('\n')).contains(QLatin1String("con_id=1]")));
    }
    void lastSpaceCannotBeRemoved() {
        Store st(path()); Rec rec; Engine e(&st, &rec);
        e.sync(Snap().ws(QStringLiteral("1"), true).win(1, QStringLiteral("1")).s, OUT);
        const QString r = e.handle({ QStringLiteral("space"), QStringLiteral("remove"), QStringLiteral("1") });
        QVERIFY2(r.startsWith(QLatin1String("error:")), qPrintable(r));
        QCOMPARE(labels(e).size(), 1);
        QVERIFY(rec.cmds.isEmpty());
    }
    void failedWriteIsReportedAndNothingMoves() {
        QFile blocker(path("blocked")); QVERIFY(blocker.open(QIODevice::WriteOnly)); blocker.close();
        Store st(path("blocked/spaces.json")); Rec rec; Engine e(&st, &rec);   // "blocked" is a file: no folder can be made
        e.sync(Snap().ws(QStringLiteral("1")).ws(QStringLiteral("2"), true).win(1, QStringLiteral("1")).win(2, QStringLiteral("2")).s, OUT);
        QVERIFY(!e.lastError().isEmpty());
        const QString r = e.handle({ QStringLiteral("space"), QStringLiteral("remove"), QStringLiteral("2") });
        QVERIFY2(r.startsWith(QLatin1String("error:")), qPrintable(r));
        QCOMPARE(labels(e).size(), 2);
        QVERIFY(rec.cmds.isEmpty());
    }
    void damagedFileIsKeptAside() {
        QFile f(path()); QVERIFY(f.open(QIODevice::WriteOnly)); f.write("{not json"); f.close();
        Store st(path()); QString err;
        QVERIFY(!st.load(&err));
        QVERIFY(err.contains(QLatin1String("kept as")));
        QVERIFY(!QFile::exists(path()));
        QVERIFY(!QDir(dir.path()).entryList({ QStringLiteral("spaces.json.damaged-*") }).isEmpty());
    }
    void commandWords() {
        Store st(path()); Rec rec; Engine e(&st, &rec);
        e.sync(Snap().ws(QStringLiteral("1"), true).win(7, QStringLiteral("1")).s, OUT);
        const QString list = e.handle({ QStringLiteral("space"), QStringLiteral("list") });
        QVERIFY(list.startsWith(QLatin1String("ok ")));
        const QJsonArray a = QJsonDocument::fromJson(list.mid(3).toUtf8()).array();
        QCOMPARE(a.size(), 1);
        QCOMPARE(a[0].toObject().value(QLatin1String("name")).toString(), QStringLiteral("Desktop 1"));
        QVERIFY(a[0].toObject().value(QLatin1String("current")).toBool());
        QVERIFY(e.handle({ QStringLiteral("space"), QStringLiteral("add") }).startsWith(QLatin1String("ok")));
        QCOMPARE(e.handle({ QStringLiteral("space"), QStringLiteral("go"), QStringLiteral("2") }), QStringLiteral("ok"));
        QCOMPARE(rec.cmds.last(), QStringLiteral("workspace number 2"));
        QCOMPARE(e.handle({ QStringLiteral("space"), QStringLiteral("move-window"), QStringLiteral("7"), QStringLiteral("2") }), QStringLiteral("ok"));
        QCOMPARE(rec.cmds.last(), QStringLiteral("[con_id=7] move container to workspace number 2"));
        QCOMPARE(e.handle({ QStringLiteral("space"), QStringLiteral("move"), QStringLiteral("2"), QStringLiteral("1") }), QStringLiteral("ok"));
        QCOMPARE(keys(e), (QStringList { QStringLiteral("2"), QStringLiteral("1") }));
        QVERIFY(e.handle({ QStringLiteral("space"), QStringLiteral("go"), QStringLiteral("9") }).startsWith(QLatin1String("error:")));
        QVERIFY(e.handle({ QStringLiteral("space"), QStringLiteral("frobnicate") }).startsWith(QLatin1String("error:")));
    }
};
QTEST_MAIN(TestSpaces)
#include "test_spaces.moc"
