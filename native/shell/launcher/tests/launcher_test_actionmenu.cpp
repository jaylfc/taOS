// SPDX-License-Identifier: AGPL-3.0-or-later
#include <QJsonArray>
#include <QtTest>

#include "actionmenu.h"

using namespace taos::search;

static QStringList titles(const QList<ActionNode> &l) { QStringList t; for (const ActionNode &n : l) t << n.title; return t; }
static std::function<bool(const QString &)> all() { return [](const QString &) { return true; }; }
static std::function<bool(const QString &)> only(QStringList have) { return [have](const QString &n) { return have.contains(n); }; }

class TestActionMenu : public QObject {
    Q_OBJECT
private slots:
    void topLevelHasTheGroupsThatHaveEngines() {
        ActionMenu m(all());
        QCOMPARE(titles(m.level({})), (QStringList{ "Capture", "Toggle", "Style", "Setup", "Install", "System" }));
    }
    void groupsWithoutAnEngineVanishInsteadOfBeingDead() {
        ActionMenu m(only({ "systemctl", "swaymsg" }));
        QCOMPARE(titles(m.level({})), (QStringList{ "System" }));
        QCOMPARE(titles(m.level({ "system" })), (QStringList{ "Sleep", "Sign out", "Restart", "Shut down" }));   // no lock helper installed: no Lock row
        ActionMenu none(only({}));
        QVERIFY(none.level({}).isEmpty());
    }
    void drillDown() {
        ActionMenu m(all());
        const QList<ActionNode> sys = m.level({ "system" });
        QCOMPARE(titles(sys), (QStringList{ "Lock", "Sleep", "Sign out", "Restart", "Shut down" }));
        QVERIFY(!sys.first().isGroup);
        QVERIFY(m.level({ "system", "nope" }).isEmpty());
        QVERIFY(m.level({ "nope" }).isEmpty());
        QCOMPARE(m.level({}).first().childCount, 2);   // Capture: screen and area
    }
    void powerLeavesNeedAConfirmEnter() {
        ActionMenu m(all());
        QVERIFY(m.find("system/shutdown").action.confirm);
        QVERIFY(m.find("system/restart").action.confirm);
        QVERIFY(m.find("system/logout").action.confirm);
        QVERIFY(!m.find("system/lock").action.confirm);
        QCOMPARE(m.find("system/shutdown").action.args, (QStringList{ "systemctl", "poweroff" }));   // the same command the search offers
    }
    void filterWorksAtEveryLevel() {
        ActionMenu m(all());
        QCOMPARE(titles(m.filter({}, "sty")).value(0), QString("Style"));
        QCOMPARE(titles(m.filter({}, "shot")).value(0), QString("Capture"));         // by keyword
        QCOMPARE(titles(m.filter({ "system" }, "shut")).value(0), QString("Shut down"));
        QCOMPARE(titles(m.filter({ "toggle" }, "blue")), (QStringList{ "Bluetooth on", "Bluetooth off" }));
        QVERIFY(m.filter({ "system" }, "zzzzqx").isEmpty());
        QCOMPARE(m.filter({ "system" }, "").size(), m.level({ "system" }).size());
    }
    void leavesAreBackedByRealEngines() {
        ActionMenu m(all());
        QCOMPARE(m.find("toggle/wifi-off").action.args, (QStringList{ "nmcli", "radio", "wifi", "off" }));
        QCOMPARE(m.find("style/dark").action.args, (QStringList{ "taos-theme", "set", "--mode", "dark" }));
        QCOMPARE(m.find("setup/display").action.args, (QStringList{ "taos-settings", "--section", "display" }));
        QCOMPARE(m.find("install/webapps").action.args, (QStringList{ "taos-store", "--section", "webapps" }));
        QCOMPARE(m.find("capture/region").action.verb, QString("capture-region"));
        QVERIFY(m.find("nope").id.isEmpty());
    }
    void captureAreaNeedsSlurp() {
        ActionMenu m(only({ "grim" }));
        QCOMPARE(titles(m.level({ "capture" })), (QStringList{ "Screenshot of the screen" }));
    }
    void jsonForAgents() {
        ActionMenu m(all());
        const QJsonArray g = m.toJson().value("groups").toArray();
        QCOMPARE(g.size(), 6);
        QCOMPARE(g.last().toObject().value("id").toString(), QString("system"));
        QVERIFY(g.last().toObject().value("items").toArray().last().toObject().value("confirm").toBool());
    }
};
QTEST_MAIN(TestActionMenu)
#include "launcher_test_actionmenu.moc"
