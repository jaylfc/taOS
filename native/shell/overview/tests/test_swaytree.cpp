#include <QFile>
#include <QtTest>

#include "swaytree.h"

// Fixtures are real `swaymsg -t get_tree` output of a headless sway 1.12: four tiled windows and one
// floating one on workspaces 1 and 2; in one of them an empty workspace 3 holds the focus.
static QByteArray fixture(const char *name) {
    QFile f(QStringLiteral(TAOS_FIXTURE_DIR "/") + QLatin1String(name));
    return f.open(QIODevice::ReadOnly) ? f.readAll() : QByteArray();
}

class TestSwayTree : public QObject {
    Q_OBJECT
private slots:
    void parsesWindows() {
        Snapshot s;
        QString err;
        QVERIFY2(SwayTree::parse(fixture("tree-focus-editor.json"), &s, &err), qPrintable(err));
        QCOMPARE(s.windows.size(), 5);
        const auto byTitle = [&](const char *t) {
            for (const WinInfo &w : s.windows) if (w.title == QLatin1String(t)) return w;
            return WinInfo();
        };
        const WinInfo editor = byTitle("editor");
        QCOMPARE(editor.workspace, QStringLiteral("2"));
        QCOMPARE(editor.workspaceNum, 2);
        QCOMPARE(editor.output, QStringLiteral("HEADLESS-1"));
        QCOMPARE(editor.appId, QStringLiteral("foot"));
        QVERIFY(editor.focused);
        QVERIFY(!editor.floating);
        QVERIFY(!editor.urgent);
        QCOMPARE(editor.identifier.size(), 32);          // foreign_toplevel_identifier
        QVERIFY(editor.rect.width() > 0 && editor.rect.height() > 0);
        QVERIFY(editor.pid > 0);
        const WinInfo calc = byTitle("floating calc");
        QVERIFY(calc.floating);
        QCOMPARE(calc.appId, QStringLiteral("floatapp"));
        QCOMPARE(calc.workspace, QStringLiteral("2"));
        QVERIFY(!byTitle("build log").floating);
        QCOMPARE(byTitle("build log").workspace, QStringLiteral("1"));
        QCOMPARE(s.focusedConId, editor.conId);
        QCOMPARE(s.focusedWorkspace, QStringLiteral("2"));
        QCOMPARE(s.focusedOutput, QStringLiteral("HEADLESS-1"));
    }
    void identifiersAreUnique() {
        Snapshot s;
        QVERIFY(SwayTree::parse(fixture("tree-focus-editor.json"), &s));
        QSet<QString> ids;
        for (const WinInfo &w : s.windows) { QVERIFY(!w.identifier.isEmpty()); ids.insert(w.identifier); }
        QCOMPARE(ids.size(), s.windows.size());
    }
    void workspacesAndOutputs() {
        Snapshot s;
        QVERIFY(SwayTree::parse(fixture("tree-focus-editor.json"), &s));
        QCOMPARE(s.outputs.size(), 1);                    // the __i3 holder is not an output
        QCOMPARE(s.outputs[0].name, QStringLiteral("HEADLESS-1"));
        QCOMPARE(s.outputs[0].currentWorkspace, QStringLiteral("2"));
        QCOMPARE(s.outputs[0].rect.size(), QSize(1920, 1080));
        QCOMPARE(s.workspaces.size(), 2);                 // the scratchpad is not a workspace
        QCOMPARE(s.workspaces[0].name, QStringLiteral("1"));
        QVERIFY(!s.workspaces[0].visible);
        QVERIFY(s.workspaces[1].visible);
        QVERIFY(s.workspaces[1].focused);
        QVERIFY(!s.workspaces[0].focused);
    }
    void emptyFocusedWorkspace() {
        Snapshot s;
        QVERIFY(SwayTree::parse(fixture("tree-empty-ws3.json"), &s));
        QCOMPARE(s.windows.size(), 5);
        QCOMPARE(s.workspaces.size(), 3);
        QCOMPARE(s.focusedConId, 0);                       // nothing focused: the workspace is empty
        QCOMPARE(s.focusedWorkspace, QStringLiteral("3"));
        QVERIFY(s.workspaces[2].focused);
        for (const WinInfo &w : s.windows) QVERIFY(w.workspace != QLatin1String("3"));
    }
    void seedOrderStartsWithFocus() {
        Snapshot s;
        QVERIFY(SwayTree::parse(fixture("tree-focus-editor.json"), &s));
        QCOMPARE(s.seedOrder.size(), 5);
        QCOMPARE(s.seedOrder.first(), s.focusedConId);
        // the focused workspace's windows come before the other workspace's
        QStringList wsOrder;
        for (int id : s.seedOrder)
            for (const WinInfo &w : s.windows) if (w.conId == id) wsOrder << w.workspace;
        QCOMPARE(wsOrder.mid(0, 3), QStringList({ "2", "2", "2" }));
    }
    void rejectsGarbage() {
        Snapshot s;
        QString err;
        QVERIFY(!SwayTree::parse("not json", &s, &err));
        QVERIFY(!err.isEmpty());
        QVERIFY(!SwayTree::parse("[1,2]", &s, &err));
    }
    void emptyTree() {
        Snapshot s;
        QVERIFY(SwayTree::parse("{\"type\":\"root\",\"nodes\":[]}", &s));
        QVERIFY(s.windows.isEmpty());
        QVERIFY(s.workspaces.isEmpty());
    }
    void xwaylandClassAsAppId() {
        const QByteArray j = R"({"nodes":[{"type":"output","name":"X-1","rect":{"x":0,"y":0,"width":100,"height":100},
          "current_workspace":"1","nodes":[{"type":"workspace","name":"1","num":1,"id":4,"rect":{"x":0,"y":0,"width":100,"height":100},
          "nodes":[{"type":"con","id":9,"name":"Calc","app_id":null,"pid":5,"window":77,"focused":true,
            "window_properties":{"class":"Gnome-calculator"},"rect":{"x":0,"y":0,"width":50,"height":50},
            "nodes":[],"floating_nodes":[]}],"floating_nodes":[]}]}]})";
        Snapshot s;
        QVERIFY(SwayTree::parse(j, &s));
        QCOMPARE(s.windows.size(), 1);
        QCOMPARE(s.windows[0].appId, QStringLiteral("Gnome-calculator"));
    }
    void windowEventNode() {
        WinInfo w;
        QVERIFY(SwayTree::parseWindowNode(R"({"id":12,"name":"new title","app_id":"foot","urgent":true,"focused":false,"floating":"auto_off"})", &w));
        QCOMPARE(w.conId, 12);
        QCOMPARE(w.title, QStringLiteral("new title"));
        QVERIFY(w.urgent);
        QVERIFY(!SwayTree::parseWindowNode("{}", &w));
    }
};
QTEST_APPLESS_MAIN(TestSwayTree)
#include "test_swaytree.moc"
